import json
from pathlib import Path
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.pinpoint import ADAPTER, PinpointAdapter
from job_source_agent.web import FetchError, Page


FIXTURES = Path(__file__).parent / "fixtures" / "pinpoint"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class RecordingFetcher:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        if isinstance(self.response, Page):
            return self.response
        return Page(url=url, html=self.response, source="fresh100-pinpoint-fixture")


def inventory_page(tenant, payload, *, final_url=None):
    url = f"https://{tenant}.pinpointhq.com/postings.json"
    return Page(
        url=url,
        final_url=final_url or url,
        html=payload,
        source=f"fresh100-{tenant}-postings",
    )


class PinpointAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = PinpointAdapter()
        self.wolfe_board = JobBoard(
            "https://wolfe.pinpointhq.com/",
            "pinpoint",
            "wolfe",
            replay_safe=True,
        )

    def test_is_typed_provider_and_canonicalizes_public_board_urls(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        accepted = (
            "https://wolfe.pinpointhq.com/",
            "https://WOLFE.pinpointhq.com/?source=careers",
            "https://wolfe.pinpointhq.com/en/",
            "https://wolfe.pinpointhq.com/postings.json",
            "https://wolfe.pinpointhq.com/en/postings/cacd0cbd-eee6-4326-99f1-b73ab432e303",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.wolfe_board)

    def test_rejects_unsafe_ambiguous_and_non_board_urls(self):
        rejected = (
            "http://wolfe.pinpointhq.com/",
            "https://pinpointhq.com/",
            "https://www.pinpointhq.com/",
            "https://wolfe.pinpointhq.com.evil.test/",
            "https://user@wolfe.pinpointhq.com/",
            "https://wolfe.pinpointhq.com:8443/",
            "https://wolfe.pinpointhq.com/register-your-interest/new",
            "https://wolfe.pinpointhq.com/en/postings/not-a-uuid",
            "https://bad_tenant.pinpointhq.com/",
            "https://wolfe.pinpointhq.com/#jobs",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_lists_wolfe_fresh100_opening_with_title_and_location(self):
        fetcher = RecordingFetcher(
            inventory_page("wolfe", fixture("fresh100_wolfe_postings.json"))
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.wolfe_board,
            JobQuery(title=" DevOps  Engineer ", location="Pittsburgh, PA"),
        )

        self.assertEqual(len(fetcher.requests), 1)
        self.assertEqual(
            fetcher.requests[0][0], "https://wolfe.pinpointhq.com/postings.json"
        )
        self.assertEqual(fetcher.requests[0][2]["Accept"], "application/json")
        self.assertEqual(
            fetcher.requests[0][2]["Referer"], "https://wolfe.pinpointhq.com/"
        )
        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "full")
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.title, "DevOps Engineer")
        self.assertEqual(candidate.location, "Pittsburgh, Pennsylvania")
        self.assertEqual(
            candidate.url,
            "https://wolfe.pinpointhq.com/en/postings/cacd0cbd-eee6-4326-99f1-b73ab432e303",
        )
        self.assertEqual(candidate.raw["posting_id"], "504718")
        self.assertTrue(result.trace["exact_title_found"])

    def test_lists_oneapp_fresh100_opening_without_company_cases(self):
        board = self.adapter.identify_board("https://oneapp.pinpointhq.com/")
        fetcher = RecordingFetcher(
            inventory_page("oneapp", fixture("fresh100_oneapp_postings.json"))
        )

        result = self.adapter.list_jobs(
            fetcher,
            board,
            JobQuery(title="Product Designer - Portland Metro"),
        )

        self.assertIsNone(result.reason_code)
        self.assertEqual(result.candidates[0].title, "Product Designer - Portland Metro")
        self.assertEqual(result.candidates[0].location, "Portland, Oregon")
        self.assertEqual(result.trace["records_seen"], 1)
        self.assertEqual(result.trace["candidate_count"], 1)

    def test_register_interest_route_is_not_exposed_as_public_opening(self):
        payload = json.loads(fixture("fresh100_wolfe_postings.json"))
        payload["data"].append(
            {
                "id": "999999",
                "title": "Register Your Interest",
                "url": "https://wolfe.pinpointhq.com/register-your-interest/new",
                "path": "/register-your-interest/new",
            }
        )

        result = self.adapter.list_jobs(
            RecordingFetcher(inventory_page("wolfe", json.dumps(payload))),
            self.wolfe_board,
            JobQuery(),
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.trace["records_seen"], 2)
        self.assertEqual(result.trace["interest_records_excluded"], 1)

        payload["data"][1]["url"] = (
            "https://oneapp.pinpointhq.com/register-your-interest/new"
        )
        cross_tenant = self.adapter.list_jobs(
            RecordingFetcher(inventory_page("wolfe", json.dumps(payload))),
            self.wolfe_board,
            JobQuery(),
        )
        self.assertEqual(cross_tenant.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(cross_tenant.inventory_complete)

    def test_verified_empty_inventory_is_complete(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(inventory_page("wolfe", '{"data": []}')),
            self.wolfe_board,
            JobQuery(title="No such role"),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.candidates, [])

    def test_cross_tenant_redirect_and_records_fail_closed(self):
        redirected = self.adapter.list_jobs(
            RecordingFetcher(
                inventory_page(
                    "wolfe",
                    fixture("fresh100_wolfe_postings.json"),
                    final_url="https://oneapp.pinpointhq.com/postings.json",
                )
            ),
            self.wolfe_board,
            JobQuery(),
        )
        payload = json.loads(fixture("fresh100_wolfe_postings.json"))
        payload["data"][0]["url"] = payload["data"][0]["url"].replace(
            "wolfe", "oneapp"
        )
        cross_tenant = self.adapter.list_jobs(
            RecordingFetcher(inventory_page("wolfe", json.dumps(payload))),
            self.wolfe_board,
            JobQuery(),
        )

        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(cross_tenant.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(redirected.inventory_complete)
        self.assertFalse(cross_tenant.inventory_complete)
        self.assertEqual(redirected.candidates, [])
        self.assertEqual(cross_tenant.candidates, [])

    def test_tampered_board_and_malformed_records_are_typed(self):
        tampered = self.adapter.list_jobs(
            RecordingFetcher(),
            JobBoard("https://wolfe.pinpointhq.com/", "pinpoint", "oneapp"),
            JobQuery(),
        )
        base = json.loads(fixture("fresh100_wolfe_postings.json"))
        malformed_payloads = []
        for mutation in ("path", "location", "job", "id"):
            payload = json.loads(json.dumps(base))
            if mutation == "path":
                payload["data"][0][mutation] = "/en/postings/00000000-0000-0000-0000-000000000000"
            elif mutation == "location":
                payload["data"][0][mutation] = None
            elif mutation == "job":
                payload["data"][0][mutation]["id"] = "0"
            else:
                payload["data"][0][mutation] = True
            malformed_payloads.append(json.dumps(payload))

        self.assertEqual(tampered.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        for payload in ("not-json", '{"jobs": []}', *malformed_payloads):
            with self.subTest(payload=payload[-80:]):
                result = self.adapter.list_jobs(
                    RecordingFetcher(inventory_page("wolfe", payload)),
                    self.wolfe_board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])

    def test_duplicate_and_unsafe_detail_urls_fail_closed(self):
        base = json.loads(fixture("fresh100_wolfe_postings.json"))
        duplicate = json.loads(json.dumps(base))
        duplicate["data"].append(json.loads(json.dumps(base["data"][0])))
        unsafe = json.loads(json.dumps(base))
        unsafe["data"][0]["url"] += "?token=secret"

        for payload in (duplicate, unsafe):
            result = self.adapter.list_jobs(
                RecordingFetcher(inventory_page("wolfe", json.dumps(payload))),
                self.wolfe_board,
                JobQuery(),
            )
            self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
            self.assertFalse(result.inventory_complete)

    def test_bounded_inventory_failures_are_typed_and_do_not_expose_partials(self):
        oversized = '{"data": []}' + (" " * 5_000_000)
        base_record = json.loads(fixture("fresh100_wolfe_postings.json"))["data"][0]
        too_many = {"data": [base_record] * 2001}

        for payload, stop_reason in (
            (oversized, "response_cap_exceeded"),
            (json.dumps(too_many), "row_cap_exceeded"),
        ):
            with self.subTest(stop_reason=stop_reason):
                result = self.adapter.list_jobs(
                    RecordingFetcher(inventory_page("wolfe", payload)),
                    self.wolfe_board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "FETCH_BUDGET_EXHAUSTED")
                self.assertTrue(result.retryable)
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])
                self.assertEqual(result.trace["stop_reason"], stop_reason)

    def test_fetch_failures_preserve_typed_classification(self):
        timeout = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("The read operation timed out")),
            self.wolfe_board,
            JobQuery(title="private search terms"),
        )
        forbidden = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("HTTP Error 403: Forbidden")),
            self.wolfe_board,
            JobQuery(),
        )
        typed = self.adapter.list_jobs(
            RecordingFetcher(
                error=FetchError(
                    "upstream unavailable",
                    reason_code="RATE_LIMITED",
                    retryable=True,
                )
            ),
            self.wolfe_board,
            JobQuery(),
        )

        self.assertEqual(timeout.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(timeout.retryable)
        self.assertNotIn("private", json.dumps(timeout.trace))
        self.assertEqual(forbidden.reason_code, "HTTP_FORBIDDEN")
        self.assertFalse(forbidden.retryable)
        self.assertEqual(typed.reason_code, "RATE_LIMITED")
        self.assertTrue(typed.retryable)


if __name__ == "__main__":
    unittest.main()
