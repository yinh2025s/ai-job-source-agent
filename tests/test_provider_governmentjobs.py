import json
import unittest

from job_source_agent.job_board import JobBoard
from job_source_agent.providers.base import JobQuery, ProviderAdapter
from job_source_agent.providers.governmentjobs import ADAPTER, GovernmentJobsAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.web import FetchError, Page


LUBBOCK = "https://www.governmentjobs.com/careers/lubbock"
CSTX = "https://www.governmentjobs.com/careers/cstx"


class RecordingFetcher:
    def __init__(self, response=None, error=None, final_url=None, responses=None):
        self.responses = list(responses) if responses is not None else [board_html(), response]
        self.error = error
        self.final_url = final_url
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error:
            raise self.error
        response = self.responses.pop(0)
        if isinstance(response, Page):
            return response
        raw = response if isinstance(response, str) else json.dumps(response)
        return Page(url=url, final_url=self.final_url or url, html=raw, source="fixture-governmentjobs")


def board_html(tenant="lubbock"):
    return f'<html data-agency-folder-name="{tenant}"><title>Job Opportunities</title></html>'


def job(tenant="lubbock", job_id=5342417, title="Information Security and Compliance Analyst", location="Information Technology, Lubbock, TX"):
    return {
        "JobId": job_id,
        "JobTitle": title,
        "Location": location,
        "JobUrl": f"/careers/{tenant}/jobs/{job_id}-0/{title.lower().replace(' ', '-')}",
    }


class GovernmentJobsAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = GovernmentJobsAdapter()
        self.board = JobBoard(LUBBOCK, "governmentjobs", "lubbock")

    def test_auto_discovered_and_canonicalizes_listing_and_detail_urls(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertIn("governmentjobs", {item.name for item in discover_native_adapters()})
        accepted = (
            LUBBOCK,
            LUBBOCK + "/",
            LUBBOCK + "?department=Information+Technology",
            LUBBOCK + "/jobs/5342417-0/information-security-and-compliance-analyst",
            "https://www.governmentjobs.com:443/careers/lubbock/jobs/5342417/information-security-analyst",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)

    def test_rejects_unsafe_ambiguous_and_non_public_routes(self):
        rejected = (
            "http://www.governmentjobs.com/careers/lubbock",
            "https://governmentjobs.com/careers/lubbock",
            "https://www.governmentjobs.com.evil.test/careers/lubbock",
            "https://user@www.governmentjobs.com/careers/lubbock",
            "https://www.governmentjobs.com:8443/careers/lubbock",
            "https://www.governmentjobs.com/careers/bad_tenant",
            "https://www.governmentjobs.com/careers/lubbock/promotionaljobs",
            "https://www.governmentjobs.com/careers/lubbock/jobs/newprint/5342417",
            "https://www.governmentjobs.com/careers/lubbock/jobs/5342417",
            LUBBOCK + "#jobs",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_lists_complete_json_inventory_and_reports_query_matches(self):
        fetcher = RecordingFetcher({"TotalCount": 1, "Jobs": [job()]})
        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title=" information security AND compliance analyst ", location="Lubbock, TX"),
        )

        self.assertEqual(fetcher.requests[0], (LUBBOCK, None, None))
        url, data, headers = fetcher.requests[1]
        self.assertEqual(url, LUBBOCK + "?sort=PositionTitle%7CAscending")
        self.assertIsNone(data)
        self.assertEqual(headers["X-Requested-With"], "XMLHttpRequest")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertEqual(result.candidates[0].url, LUBBOCK + "/jobs/5342417-0/information-security-and-compliance-analyst")
        self.assertEqual(result.candidates[0].location, "Information Technology, Lubbock, TX")
        self.assertTrue(result.trace["exact_title_found"])
        self.assertTrue(result.trace["location_match_found"])

    def test_parses_complete_html_fragment_and_preserves_same_tenant_detail(self):
        html = """
        <div id="number-found-items">1 job found</div>
        <article class="job-item">
          <a href="/careers/cstx/jobs/5372109-0/hr-operations-and-services-manager">
            HR Operations and Services Manager
          </a>
        </article>
        """
        board = JobBoard(CSTX, "governmentjobs", "cstx")
        result = self.adapter.list_jobs(
            RecordingFetcher(responses=[board_html("cstx"), html]), board, JobQuery()
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].provider, "governmentjobs")
        self.assertEqual(result.candidates[0].raw["tenant"], "cstx")
        self.assertEqual(result.trace["variant"], "governmentjobs_public_xhr_html")

    def test_verified_empty_inventory_is_complete(self):
        result = self.adapter.list_jobs(
            RecordingFetcher({"TotalCount": 0, "Jobs": []}), self.board, JobQuery(title="missing")
        )
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.candidates, [])

    def test_rejects_cross_tenant_response_url_and_records(self):
        response_redirect = self.adapter.list_jobs(
            RecordingFetcher(responses=[Page(LUBBOCK, board_html()), Page(CSTX, "")]),
            self.board,
            JobQuery(),
        )
        cross_tenant_record = self.adapter.list_jobs(
            RecordingFetcher({"TotalCount": 1, "Jobs": [job(tenant="cstx")]}),
            self.board,
            JobQuery(),
        )
        self.assertEqual(response_redirect.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(cross_tenant_record.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(response_redirect.inventory_complete)
        self.assertEqual(cross_tenant_record.candidates, [])

    def test_rejects_cross_tenant_page_identity_and_javascript_shell(self):
        cross_tenant = self.adapter.list_jobs(
            RecordingFetcher(responses=[board_html("cstx")]), self.board, JobQuery()
        )
        shell = '<html data-agency-folder-name="lubbock"><div>0 jobs found</div><div id="job-list-container"></div></html>'
        javascript_shell = self.adapter.list_jobs(
            RecordingFetcher(responses=[board_html(), shell]), self.board, JobQuery()
        )
        self.assertEqual(cross_tenant.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(javascript_shell.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(javascript_shell.inventory_complete)

    def test_invalid_counts_duplicates_and_unsafe_details_fail_closed(self):
        duplicate = job()
        cases = (
            {"TotalCount": 2, "Jobs": [job()]},
            {"TotalCount": 2, "Jobs": [duplicate, duplicate]},
            {"TotalCount": 1, "Jobs": [job() | {"JobUrl": "https://evil.test/job/5342417"}]},
            {"TotalCount": 1, "Jobs": [job() | {"JobUrl": "//evil.test/careers/lubbock/jobs/5342417-0/fake"}]},
            {"TotalCount": 1, "Jobs": [job() | {"JobId": 99}]},
            {"TotalCount": True, "Jobs": []},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                result = self.adapter.list_jobs(RecordingFetcher(payload), self.board, JobQuery())
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertEqual(result.candidates, [])

    def test_typed_transport_cap_and_tampered_board_failures(self):
        timeout = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("read operation timed out")), self.board, JobQuery()
        )
        capped = self.adapter.list_jobs(
            RecordingFetcher({"TotalCount": 2001, "Jobs": []}), self.board, JobQuery()
        )
        tampered = self.adapter.list_jobs(
            RecordingFetcher({"TotalCount": 0, "Jobs": []}),
            JobBoard(LUBBOCK, "governmentjobs", "cstx"),
            JobQuery(),
        )
        self.assertEqual(timeout.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(timeout.retryable)
        self.assertEqual(capped.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertEqual(tampered.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
