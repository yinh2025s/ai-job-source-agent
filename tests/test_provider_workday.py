import json
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.workday import ADAPTER, WorkdayAdapter
from job_source_agent.web import FetchError, Page


class RecordingFetcher:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append({"url": url, "data": data, "headers": headers})
        return Page(url=url, html=self.response, final_url=url, source="workday-fixture")


class WorkdayAdapterTests(unittest.TestCase):
    def test_exported_adapter_satisfies_provider_contract(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertEqual(ADAPTER.name, "workday")
        self.assertTrue(ADAPTER.supports_listing)

    def test_recognizes_workday_hosts_without_accepting_lookalikes(self):
        adapter = WorkdayAdapter()

        self.assertTrue(adapter.recognizes("https://acme.wd5.myworkdayjobs.com/en-US/jobs"))
        self.assertTrue(adapter.recognizes("https://acme.workdayjobs.com/jobs"))
        self.assertFalse(adapter.recognizes("https://myworkdayjobs.com.evil.example/jobs"))
        self.assertFalse(adapter.recognizes("https://careers.example.com/jobs"))
        self.assertFalse(adapter.recognizes("https://evil@acme.wd5.myworkdayjobs.com/jobs"))
        self.assertFalse(adapter.recognizes("https://acme.wd5.myworkdayjobs.com:8443/jobs"))
        self.assertFalse(adapter.recognizes("http://[invalid/jobs"))

    def test_identifies_tenant_site_and_canonical_board(self):
        adapter = WorkdayAdapter()
        cases = {
            "https://company.wd5.myworkdayjobs.com/en-US/acme?source=linkedin": (
                "company/acme",
                "https://company.wd5.myworkdayjobs.com/en-US/acme",
            ),
            "https://company.wd5.myworkdayjobs.com/en-US/acme/job/NY/Data-Analyst_R123": (
                "company/acme",
                "https://company.wd5.myworkdayjobs.com/en-US/acme",
            ),
            "https://company.wd5.myworkdayjobs.com/acme": (
                "company/acme",
                "https://company.wd5.myworkdayjobs.com/acme",
            ),
        }

        for url, (identifier, board_url) in cases.items():
            with self.subTest(url=url):
                board = adapter.identify_board(url)
                self.assertIsNotNone(board)
                self.assertEqual(board.identifier, identifier)
                self.assertEqual(board.url, board_url)

    def test_posts_cxs_payload_and_parses_job_postings(self):
        adapter = WorkdayAdapter()
        board = adapter.identify_board("https://company.wd5.myworkdayjobs.com/en-US/acme")
        fetcher = RecordingFetcher(
            json.dumps(
                {
                    "jobPostings": [
                        {
                            "title": "Data Analyst",
                            "externalPath": "/job/New-York-NY/Data-Analyst_R123",
                            "locationsText": "New York, NY",
                            "postedOn": "Posted Today",
                        },
                        {
                            "title": "Platform Engineer",
                            "externalPath": "job/Remote/Platform-Engineer_R456",
                            "bulletFields": ["Remote"],
                        },
                    ]
                }
            )
        )

        result = adapter.list_jobs(fetcher, board, JobQuery(title="Data Analyst"))

        call = fetcher.calls[0]
        self.assertEqual(
            call["url"],
            "https://company.wd5.myworkdayjobs.com/wday/cxs/company/acme/jobs",
        )
        self.assertEqual(
            json.loads(call["data"]),
            {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "Data Analyst"},
        )
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        self.assertEqual(call["headers"]["Origin"], "https://company.wd5.myworkdayjobs.com")
        self.assertEqual(
            call["headers"]["Referer"],
            "https://company.wd5.myworkdayjobs.com/en-US/acme",
        )
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(
            result.candidates[0].url,
            "https://company.wd5.myworkdayjobs.com/en-US/acme/job/New-York-NY/Data-Analyst_R123",
        )
        self.assertEqual(result.candidates[0].location, "New York, NY")
        self.assertEqual(result.trace["candidate_count"], 2)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.trace["inventory_scope"], result.inventory_scope)
        self.assertEqual(result.trace["inventory_complete"], result.inventory_complete)

    def test_paginates_with_tenant_compatible_page_size(self):
        adapter = WorkdayAdapter()
        board = adapter.identify_board("https://company.wd5.myworkdayjobs.com/en-US/acme")

        class PaginatedFetcher:
            def __init__(self):
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                payload = json.loads(data)
                self.calls.append(payload)
                offset = payload["offset"]
                count = 20 if offset == 0 else 1
                postings = [
                    {
                        "title": f"Role {offset + index}",
                        "externalPath": f"/job/Role-{offset + index}_R{offset + index}",
                    }
                    for index in range(count)
                ]
                return Page(
                    url=url,
                    final_url=url,
                    html=json.dumps({"total": 21, "jobPostings": postings}),
                    source="workday-fixture",
                )

        fetcher = PaginatedFetcher()
        result = adapter.list_jobs(fetcher, board, JobQuery(title="Role"))

        self.assertEqual([call["limit"] for call in fetcher.calls], [20, 20])
        self.assertEqual([call["offset"] for call in fetcher.calls], [0, 20])
        self.assertEqual(len(result.candidates), 21)
        self.assertEqual(result.trace["page_count"], 2)
        self.assertEqual(result.trace["total"], 21)
        self.assertTrue(result.inventory_complete)

    def test_keeps_candidates_but_marks_inventory_incomplete_on_later_fetch_failure(self):
        adapter = WorkdayAdapter()
        board = adapter.identify_board("https://company.wd5.myworkdayjobs.com/en-US/acme")

        class PartialFetcher:
            def __init__(self):
                self.calls = 0

            def fetch(self, url, data=None, headers=None):
                self.calls += 1
                if self.calls == 2:
                    raise FetchError("page two unavailable")
                postings = [
                    {"title": f"Role {index}", "externalPath": f"/job/Role-{index}_R{index}"}
                    for index in range(20)
                ]
                return Page(
                    url=url,
                    final_url=url,
                    html=json.dumps({"total": 40, "jobPostings": postings}),
                )

        result = adapter.list_jobs(PartialFetcher(), board, JobQuery(title="Role"))

        self.assertEqual(len(result.candidates), 20)
        self.assertIsNone(result.reason_code)
        self.assertFalse(result.inventory_complete)
        self.assertFalse(result.trace["inventory_complete"])
        self.assertEqual(len(result.trace["errors"]), 1)

    def test_page_cap_with_uncovered_total_is_incomplete(self):
        adapter = WorkdayAdapter()
        board = adapter.identify_board("https://company.wd5.myworkdayjobs.com/en-US/acme")

        class CappedFetcher:
            def fetch(self, url, data=None, headers=None):
                offset = json.loads(data)["offset"]
                postings = [
                    {
                        "title": f"Role {offset + index}",
                        "externalPath": f"/job/Role-{offset + index}_R{offset + index}",
                    }
                    for index in range(20)
                ]
                return Page(
                    url=url,
                    final_url=url,
                    html=json.dumps({"total": 101, "jobPostings": postings}),
                )

        result = adapter.list_jobs(CappedFetcher(), board, JobQuery())

        self.assertEqual(result.trace["page_count"], 5)
        self.assertEqual(len(result.candidates), 100)
        self.assertFalse(result.inventory_complete)
        self.assertFalse(result.trace["inventory_complete"])

    def test_normalizes_absolute_and_site_root_detail_urls(self):
        adapter = WorkdayAdapter()
        board = adapter.identify_board("https://company.wd5.myworkdayjobs.com/en-US/acme")
        fetcher = RecordingFetcher(
            json.dumps(
                {
                    "jobPostings": [
                        {
                            "title": "Absolute",
                            "externalPath": (
                                "https://company.wd5.myworkdayjobs.com/en-US/acme/job/A_R1"
                                "?utm_source=test#details"
                            ),
                        },
                        {
                            "title": "Rooted",
                            "externalPath": "/en-US/acme/job/B_R2",
                        },
                        {
                            "title": "Cross tenant",
                            "externalPath": "https://evil.example/job/C_R3",
                        },
                    ]
                }
            )
        )

        result = adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(
            [candidate.url for candidate in result.candidates],
            [
                "https://company.wd5.myworkdayjobs.com/en-US/acme/job/A_R1",
                "https://company.wd5.myworkdayjobs.com/en-US/acme/job/B_R2",
            ],
        )

    def test_rejects_mismatched_tenant_and_cross_host_api_redirect(self):
        adapter = WorkdayAdapter()
        mismatched = JobBoard(
            url="https://other.wd5.myworkdayjobs.com/en-US/acme",
            provider="workday",
            identifier="company/acme",
        )

        result = adapter.list_jobs(RecordingFetcher('{"jobPostings": []}'), mismatched, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

        class RedirectFetcher(RecordingFetcher):
            def fetch(self, url, data=None, headers=None):
                page = super().fetch(url, data=data, headers=headers)
                page.final_url = "https://evil.example/wday/cxs/company/acme/jobs"
                return page

        board = adapter.identify_board("https://company.wd5.myworkdayjobs.com/en-US/acme")
        redirected = adapter.list_jobs(
            RedirectFetcher('{"jobPostings": []}'),
            board,
            JobQuery(),
        )

        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_reports_invalid_and_empty_responses(self):
        adapter = WorkdayAdapter()
        board = JobBoard(
            url="https://company.wd5.myworkdayjobs.com/en-US/acme",
            provider="workday",
            identifier="company/acme",
        )

        invalid = adapter.list_jobs(RecordingFetcher("not json"), board, JobQuery())
        malformed = adapter.list_jobs(
            RecordingFetcher('{"jobPostings": {"title": "not-a-list"}}'),
            board,
            JobQuery(),
        )
        empty = adapter.list_jobs(RecordingFetcher('{"jobPostings": []}'), board, JobQuery())

        self.assertEqual(invalid.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(malformed.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(empty.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(empty.candidates, [])
        self.assertTrue(empty.inventory_complete)


if __name__ == "__main__":
    unittest.main()
