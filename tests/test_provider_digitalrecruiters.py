import json
import unittest
from urllib.parse import parse_qs, urlparse

from job_source_agent.job_board import JobBoard
from job_source_agent.providers.base import JobQuery
from job_source_agent.providers.digitalrecruiters import DigitalRecruitersAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.web import FetchError, Page


BOARD_URL = "https://careers.example.com/en/annonces"


def page_html(tenant="careers.example.com"):
    return (
        '<link rel="stylesheet" href="https://api.digitalrecruiters.com/careers/v1/'
        f'careers-sites/{tenant}/css">'
    )


def row(job_id=123, address_id=456, title="Account Executive", location="New York", **extra):
    return {
        "id": f"{job_id}-{address_id}",
        "job_ad_id": job_id,
        "title": title,
        "location": location,
        "url": f"{job_id}-account-executive-new-york",
        "career_domain": "careers.example.com",
        "is_external": False,
        **extra,
    }


class RecordingFetcher:
    def __init__(self, responses=(), error=None):
        self.responses = list(responses)
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error:
            raise self.error
        value = self.responses.pop(0)
        if isinstance(value, Page):
            return value
        return Page(url=url, final_url=url, html=json.dumps(value), source="fixture-api")


class DigitalRecruitersAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = DigitalRecruitersAdapter()
        self.board = self.adapter.identify_board_from_page(
            Page(url=BOARD_URL, html=page_html())
        )
        self.assertIsNotNone(self.board)

    def test_auto_discovered_page_evidence_builds_tenant_board(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIn("digitalrecruiters", native)
        self.assertFalse(self.adapter.recognizes(BOARD_URL))
        self.assertEqual(self.board.url, BOARD_URL)
        self.assertEqual(
            json.loads(self.board.identifier),
            {
                "api_base": "https://api.digitalrecruiters.com/public/v1",
                "board_url": BOARD_URL,
                "locale": "en",
                "tenant": "careers.example.com",
            },
        )

    def test_rejects_cross_tenant_or_unsafe_page_evidence(self):
        cases = (
            Page(url=BOARD_URL, html=page_html("other.example.com")),
            Page(url="http://careers.example.com/en/annonces", html=page_html()),
            Page(url="https://user@careers.example.com/en/annonces", html=page_html()),
            Page(url=BOARD_URL, html=page_html() + page_html("other.example.com")),
        )
        for page in cases:
            with self.subTest(url=page.url):
                self.assertIsNone(self.adapter.identify_board_from_page(page))

    def test_rejects_commented_scripted_or_malformed_provider_evidence(self):
        evidence = page_html()
        cases = (
            f"<!-- {evidence} -->",
            f"<script>const fake = {json.dumps(evidence)};</script>",
            evidence.replace('rel="stylesheet"', 'rel="preload"'),
            evidence.replace("https://api.", "http://api."),
            evidence.replace("/css\">", "/css#fake\">"),
            evidence.replace("careers.example.com", "bad..tenant"),
        )
        for html in cases:
            with self.subTest(html=html):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(Page(url=BOARD_URL, html=html))
                )

    def test_title_filtered_inventory_builds_canonical_detail(self):
        fetcher = RecordingFetcher([{"count": 1, "items": [row()]}])

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="  Account   Executive  ", location="New York, NY"),
        )

        url, data, headers = fetcher.requests[0]
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["domainName"], ["careers.example.com"])
        self.assertEqual(query["locale"], ["en_GB"])
        self.assertEqual(json.loads(data), {"filters": {}, "q": "Account%20Executive"})
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(result.candidates[0].url, "https://careers.example.com/en/annonce/123-account-executive-new-york")
        self.assertEqual(result.candidates[0].location, "New York")
        self.assertTrue(result.inventory_complete)
        self.assertIsNone(result.reason_code)

    def test_paginates_and_requires_consistent_complete_counts(self):
        first = [row(job_id=index + 1, address_id=index + 101) for index in range(20)]
        second = [row(job_id=21, address_id=121)]
        fetcher = RecordingFetcher(
            [
                {"count": 21, "items": first},
                {"count": 21, "items": second},
            ]
        )

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="Engineer"))

        self.assertEqual(len(result.candidates), 21)
        self.assertEqual(parse_qs(urlparse(fetcher.requests[1][0]).query)["page"], ["2"])

    def test_empty_filtered_inventory_is_complete(self):
        result = self.adapter.list_jobs(
            RecordingFetcher([{"count": 0, "items": []}]),
            self.board,
            JobQuery(title="No Such Role"),
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")

    def test_malformed_cross_tenant_duplicate_and_count_mismatch_fail_closed(self):
        cases = (
            ([{"count": 1, "items": [row(career_domain="evil.example")]}], "cross_tenant_response"),
            ([{"count": 1, "items": [row(career_domain=123)]}], "cross_tenant_response"),
            ([{"count": 1, "items": ["not-a-record"]}], "invalid_response_schema"),
            ([{"count": 2, "items": [row()]}], "pagination_count_mismatch"),
            ([{"count": 1, "items": [row(url="../evil")]}], "invalid_or_cross_tenant_record"),
            ([{"count": 1, "items": [row(job_ad_id=999)]}], "invalid_or_cross_tenant_record"),
        )
        for responses, stop_reason in cases:
            with self.subTest(stop_reason=stop_reason):
                result = self.adapter.list_jobs(
                    RecordingFetcher(responses), self.board, JobQuery(title="Engineer")
                )
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])
                self.assertEqual(result.trace["stop_reason"], stop_reason)

    def test_non_string_and_malformed_candidate_fields_fail_closed(self):
        malformed = (
            {"id": 123},
            {"job_ad_id": "123"},
            {"job_ad_id": True},
            {"title": None},
            {"title": "  "},
            {"title": "Engineer\x00"},
            {"location": ["New York"]},
            {"location": "New York\x00"},
            {"url": 123},
            {"is_external": "false"},
            {"is_external": True},
        )
        for changed in malformed:
            with self.subTest(changed=changed):
                result = self.adapter.list_jobs(
                    RecordingFetcher([{"count": 1, "items": [row(**changed)]}]),
                    self.board,
                    JobQuery(title="Engineer"),
                )
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertEqual(result.trace["stop_reason"], "invalid_or_cross_tenant_record")

    def test_malformed_inventory_envelope_fails_closed(self):
        payloads = (
            None,
            [],
            {},
            {"count": True, "items": []},
            {"count": "0", "items": []},
            {"count": -1, "items": []},
            {"count": 0, "items": {}},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                result = self.adapter.list_jobs(
                    RecordingFetcher([payload]), self.board, JobQuery(title="Engineer")
                )
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")

    def test_partial_pagination_never_exposes_candidates(self):
        first = [row(job_id=index + 1, address_id=index + 101) for index in range(20)]
        cases = (
            (
                [{"count": 21, "items": first}, {"count": 22, "items": [row(job_id=21)]}],
                "contradictory_total",
            ),
            (
                [{"count": 21, "items": first}, {"count": 21, "items": []}],
                "pagination_count_mismatch",
            ),
            (
                [{"count": 21, "items": first}, {"count": 21, "items": [first[0]]}],
                "duplicate_job_id",
            ),
        )
        for responses, stop_reason in cases:
            with self.subTest(stop_reason=stop_reason):
                result = self.adapter.list_jobs(
                    RecordingFetcher(responses), self.board, JobQuery(title="Engineer")
                )
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])
                self.assertEqual(result.trace["exposed_candidate_count"], 0)
                self.assertEqual(result.trace["stop_reason"], stop_reason)

    def test_row_and_page_caps_fail_closed(self):
        over_cap = self.adapter.list_jobs(
            RecordingFetcher([{"count": 201, "items": []}]),
            self.board,
            JobQuery(title="Engineer"),
        )
        pages = []
        for page_number in range(10):
            start = page_number * 20 + 1
            pages.append(
                {
                    "count": 200,
                    "items": [
                        row(job_id=index, address_id=index + 1000)
                        for index in range(start, start + 20)
                    ],
                }
            )
        complete_at_cap = self.adapter.list_jobs(
            RecordingFetcher(pages), self.board, JobQuery(title="Engineer")
        )

        self.assertFalse(over_cap.inventory_complete)
        self.assertEqual(over_cap.candidates, [])
        self.assertEqual(over_cap.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertEqual(over_cap.trace["stop_reason"], "row_cap_exceeded")
        self.assertTrue(complete_at_cap.inventory_complete)
        self.assertEqual(len(complete_at_cap.candidates), 200)

    def test_cross_host_redirect_and_tampered_locator_fail_closed(self):
        request_page = Page(
            url="https://api.digitalrecruiters.com/public/v1/careers-site/job-ads",
            final_url="https://evil.example/jobs",
            html=json.dumps({"count": 0, "items": []}),
        )
        redirected = self.adapter.list_jobs(
            RecordingFetcher([request_page]), self.board, JobQuery(title="Engineer")
        )
        value = json.loads(self.board.identifier)
        value["tenant"] = "other.example.com"
        tampered = self.adapter.list_jobs(
            RecordingFetcher(),
            JobBoard(
                url=self.board.url,
                provider=self.board.provider,
                identifier=json.dumps(value),
                replay_safe=True,
            ),
            JobQuery(title="Engineer"),
        )

        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(tampered.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_same_host_credential_port_fragment_and_query_redirects_fail_closed(self):
        request_url = (
            "https://api.digitalrecruiters.com/public/v1/careers-site/job-ads"
            "?domainName=careers.example.com&limit=20&page=1&locale=en_GB"
        )
        redirects = (
            request_url.replace("https://", "https://user@"),
            request_url.replace(".com/", ".com:443/"),
            f"{request_url}#fragment",
            request_url.replace("page=1", "page=2"),
        )
        for final_url in redirects:
            with self.subTest(final_url=final_url):
                page = Page(
                    url=request_url,
                    final_url=final_url,
                    html=json.dumps({"count": 0, "items": []}),
                )
                result = self.adapter.list_jobs(
                    RecordingFetcher([page]), self.board, JobQuery(title="Engineer")
                )
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_timeout_is_retryable_and_title_is_not_recorded(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("The read operation timed out")),
            self.board,
            JobQuery(title="Private Search Terms"),
        )

        self.assertEqual(result.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(result.retryable)
        self.assertNotIn("Private", json.dumps(result.trace))


if __name__ == "__main__":
    unittest.main()
