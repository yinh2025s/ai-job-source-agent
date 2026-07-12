import json
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.workday import ADAPTER, WorkdayAdapter
from job_source_agent.web import Page


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
            {"appliedFacets": {}, "limit": 50, "offset": 0, "searchText": "Data Analyst"},
        )
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(
            result.candidates[0].url,
            "https://company.wd5.myworkdayjobs.com/en-US/acme/job/New-York-NY/Data-Analyst_R123",
        )
        self.assertEqual(result.candidates[0].location, "New York, NY")
        self.assertEqual(result.trace["candidate_count"], 2)

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


if __name__ == "__main__":
    unittest.main()
