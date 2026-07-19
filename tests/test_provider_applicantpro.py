import json
from pathlib import Path
import unittest

from job_source_agent.providers.applicantpro import ADAPTER, ApplicantProAdapter
from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.web import FetchError, Page


FIXTURES = Path(__file__).parent / "fixtures" / "applicantpro"
TENANT = "bastiontechnologies"
BOARD_URL = "https://www.applicantpro.com/openings/bastiontechnologies/jobs"
LEGACY_URL = "https://bastiontechnologies.applicantpro.com/jobs/"
INVENTORY_URL = (
    "https://bastiontechnologies.applicantpro.com/core/jobs/4242?"
    "getParams=%7B%22isInternal%22%3A0%7D"
)


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class RecordingFetcher:
    def __init__(self, responses=(), error=None):
        self.responses = list(responses)
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise FetchError(f"unexpected URL: {url}")
        response = self.responses.pop(0)
        if isinstance(response, Page):
            return response
        return Page(url=url, html=response, source="fixture-applicantpro")


def board_page(html=None, *, final_url=LEGACY_URL):
    return Page(
        url=BOARD_URL,
        final_url=final_url,
        html=fixture("bastion_board.html") if html is None else html,
        source="frozen-bastion-board",
    )


def inventory_page(html=None, *, final_url=INVENTORY_URL):
    return Page(
        url=INVENTORY_URL,
        final_url=final_url,
        html=fixture("bastion_inventory.json") if html is None else html,
        source="frozen-bastion-inventory",
    )


class ApplicantProAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ApplicantProAdapter()
        self.board = JobBoard(BOARD_URL, "applicantpro", TENANT)

    def test_is_typed_provider_and_canonicalizes_public_url_families(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        accepted = (
            LEGACY_URL,
            LEGACY_URL.rstrip("/"),
            "https://bastiontechnologies.applicantpro.com//jobs//?source=careers",
            "https://bastiontechnologies.applicantpro.com/iframe/",
            "https://bastiontechnologies.applicantpro.com/jobs/3938914.html",
            BOARD_URL,
            BOARD_URL + "/?source=careers",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)

    def test_canonicalizes_safe_legacy_tenant_roots(self):
        accepted = (
            "https://bastiontechnologies.applicantpro.com",
            "https://bastiontechnologies.applicantpro.com/",
            "https://bastiontechnologies.applicantpro.com:443/",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)

        rejected = (
            "http://bastiontechnologies.applicantpro.com/",
            "https://user@bastiontechnologies.applicantpro.com/",
            "https://bastiontechnologies.applicantpro.com:8443/",
            "https://bastiontechnologies.applicantpro.com/?source=careers",
            "https://bastiontechnologies.applicantpro.com/#jobs",
            "https://division.bastiontechnologies.applicantpro.com/",
            "https://applicantpro.com/",
            "https://www.applicantpro.com/",
            "https://bastiontechnologies.applicantpro.com.evil.test/",
            "https://bad_tenant.applicantpro.com/",
            "https://bastiontechnologies.applicantpro.com/account/",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_rejects_unsafe_ambiguous_and_non_listing_urls(self):
        rejected = (
            "http://bastiontechnologies.applicantpro.com/jobs/",
            "https://applicantpro.com/openings/bastiontechnologies/jobs",
            "https://www.applicantpro.com/openings/bastiontechnologies/jobs/3938914",
            "https://www.applicantpro.com/openings/a/bastiontechnologies/jobs",
            "https://bastiontechnologies.applicantpro.com.evil.test/jobs/",
            "https://user@bastiontechnologies.applicantpro.com/jobs/",
            "https://bastiontechnologies.applicantpro.com:8443/jobs/",
            "https://bastiontechnologies.applicantpro.com/account/",
            "https://bastiontechnologies.applicantpro.com/jobs/3938914/apply",
            "https://bastiontechnologies.applicantpro.com/jobs/#opening",
            "https://bad_tenant.applicantpro.com/jobs/",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_discovers_transport_and_lists_exact_official_detail(self):
        fetcher = RecordingFetcher([board_page(), inventory_page()])

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title=" Mechanical  Project Engineer (BT-25195) "),
        )

        self.assertEqual(fetcher.requests[0], (BOARD_URL, None, None))
        self.assertEqual(fetcher.requests[1][0], INVENTORY_URL)
        self.assertEqual(fetcher.requests[1][2]["Referer"], BOARD_URL)
        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "full")
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.title, "Mechanical Project Engineer (BT-25195)")
        self.assertEqual(candidate.location, "Houston, TX, USA")
        self.assertEqual(
            candidate.url,
            "https://bastiontechnologies.applicantpro.com/jobs/3938914.html",
        )
        self.assertEqual(candidate.raw, {"job_id": "3938914", "domain_id": "4242"})
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(
            result.trace["identity"],
            {
                "tenant": TENANT,
                "career_site_name": "Bastion Technologies",
                "organization_id": "1701",
                "domain_id": "4242",
            },
        )

    def test_preserves_validated_provider_detail_variant(self):
        payload = json.loads(fixture("bastion_inventory.json"))
        payload["data"]["jobs"][0]["jobUrl"] = (
            "https://bastiontechnologies.applicantpro.com:443/jobs/3938914/"
        )
        result = self.adapter.list_jobs(
            RecordingFetcher([board_page(), inventory_page(json.dumps(payload))]),
            self.board,
            JobQuery(),
        )

        self.assertEqual(
            result.candidates[0].url,
            "https://bastiontechnologies.applicantpro.com/jobs/3938914",
        )

    def test_verified_empty_inventory_is_complete(self):
        payload = json.loads(fixture("bastion_inventory.json"))
        payload["data"]["jobs"] = []
        payload["data"]["jobCount"] = 0

        result = self.adapter.list_jobs(
            RecordingFetcher([board_page(), inventory_page(json.dumps(payload))]),
            self.board,
            JobQuery(title="No such role"),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.candidates, [])

    def test_cross_tenant_page_redirect_and_inventory_redirect_fail_closed(self):
        page_redirect = self.adapter.list_jobs(
            RecordingFetcher(
                [board_page(final_url="https://other.applicantpro.com/jobs/")]
            ),
            self.board,
            JobQuery(),
        )
        inventory_redirect = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    board_page(),
                    inventory_page(
                        final_url=(
                            "https://other.applicantpro.com/core/jobs/4242?"
                            "getParams=%7B%22isInternal%22%3A0%7D"
                        )
                    ),
                ]
            ),
            self.board,
            JobQuery(),
        )

        for result in (page_redirect, inventory_redirect):
            self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
            self.assertFalse(result.inventory_complete)
            self.assertEqual(result.candidates, [])

        cross_tenant_login = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    board_page(
                        final_url="https://other.applicantpro.com/account/"
                    )
                ]
            ),
            self.board,
            JobQuery(),
        )
        self.assertEqual(
            cross_tenant_login.reason_code, "PROVIDER_VARIANT_UNSUPPORTED"
        )

    def test_cross_tenant_unsafe_and_mismatched_records_fail_closed(self):
        base = json.loads(fixture("bastion_inventory.json"))
        mismatched_id = json.loads(json.dumps(base))
        mismatched_id["data"]["jobs"][0]["id"] = 999
        unsafe_query = json.loads(json.dumps(base))
        unsafe_query["data"]["jobs"][0]["jobUrl"] += "?token=secret"
        cases = (
            fixture("cross_tenant_inventory.json"),
            json.dumps(mismatched_id),
            json.dumps(unsafe_query),
        )
        for payload in cases:
            with self.subTest(payload=payload):
                result = self.adapter.list_jobs(
                    RecordingFetcher([board_page(), inventory_page(payload)]),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])

    def test_unsafe_conflicting_or_missing_page_evidence_is_typed(self):
        good = fixture("bastion_board.html")
        cases = (
            fixture("unsafe_board.html"),
            good.replace('subdomainName: "bastiontechnologies"', 'subdomainName: "other"'),
            good + good.replace('domain_id": "4242"', 'domain_id": "9999"'),
            "<html><body><div id=\"app\">Please enable JavaScript to view jobs.</div></body></html>",
        )
        for html in cases:
            with self.subTest(html=html[-100:]):
                result = self.adapter.list_jobs(
                    RecordingFetcher([board_page(html)]), self.board, JobQuery()
                )
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
                self.assertFalse(result.inventory_complete)
        js_result = self.adapter.list_jobs(
            RecordingFetcher([board_page(cases[-1])]), self.board, JobQuery()
        )
        self.assertEqual(js_result.trace["failure_class"], "javascript_required")

    def test_preserves_typed_forbidden_login_and_fetch_failures(self):
        forbidden_fetch = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("HTTP Error 403: Forbidden")),
            self.board,
            JobQuery(),
        )
        forbidden_shell = self.adapter.list_jobs(
            RecordingFetcher([board_page("<h2>403 Forbidden</h2>")]),
            self.board,
            JobQuery(),
        )
        login_redirect = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    board_page(
                        "<title>Login</title><input type=\"password\">",
                        final_url="https://bastiontechnologies.applicantpro.com/account/",
                    )
                ]
            ),
            self.board,
            JobQuery(),
        )
        timeout = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("The read operation timed out")),
            self.board,
            JobQuery(title="private terms"),
        )

        self.assertEqual(forbidden_fetch.reason_code, "HTTP_FORBIDDEN")
        self.assertFalse(forbidden_fetch.retryable)
        self.assertEqual(forbidden_shell.reason_code, "HTTP_FORBIDDEN")
        self.assertEqual(forbidden_shell.trace["failure_class"], "http_forbidden")
        self.assertEqual(login_redirect.reason_code, "LOGIN_REQUIRED")
        self.assertEqual(timeout.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(timeout.retryable)
        self.assertNotIn("private", json.dumps(timeout.trace))

    def test_tampered_locator_and_malformed_inventory_fail_closed(self):
        tampered = self.adapter.list_jobs(
            RecordingFetcher(),
            JobBoard(BOARD_URL, "applicantpro", "other"),
            JobQuery(),
        )
        malformed = self.adapter.list_jobs(
            RecordingFetcher([board_page(), inventory_page("not-json")]),
            self.board,
            JobQuery(),
        )

        self.assertEqual(tampered.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(malformed.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(malformed.candidates, [])
        self.assertFalse(malformed.inventory_complete)


if __name__ == "__main__":
    unittest.main()
