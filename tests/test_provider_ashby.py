import json
import unittest

from job_source_agent.providers.ashby import AshbyAdapter
from job_source_agent.providers.base import JobBoard, JobQuery
from job_source_agent.web import Page


class StubFetcher:
    def __init__(self, payload):
        self.payload = payload
        self.requested_urls = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        html = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return Page(url=url, final_url=url, html=html, source="ashby-fixture")


class AshbyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = AshbyAdapter()

    def test_recognizes_only_public_ashby_job_boards(self):
        self.assertTrue(self.adapter.recognizes("https://jobs.ashbyhq.com/acme"))
        self.assertTrue(self.adapter.recognizes("https://JOBS.ASHBYHQ.COM/acme/123"))
        self.assertFalse(self.adapter.recognizes("https://api.ashbyhq.com/posting-api/job-board/acme"))
        self.assertFalse(self.adapter.recognizes("https://example.com/jobs.ashbyhq.com/acme"))

    def test_identifies_board_slug_from_list_or_detail_url(self):
        board = self.adapter.identify_board("https://jobs.ashbyhq.com/acme/job-id?embed=true")

        self.assertEqual(board, JobBoard(
            url="https://jobs.ashbyhq.com/acme/job-id?embed=true",
            provider="ashby",
            identifier="acme",
        ))
        self.assertIsNone(self.adapter.identify_board("https://jobs.ashbyhq.com/"))
        self.assertIsNone(self.adapter.identify_board("https://api.ashbyhq.com/acme"))

    def test_lists_normalized_candidates_from_posting_api(self):
        fetcher = StubFetcher({
            "jobs": [
                {
                    "id": "job-1",
                    "title": "  AI Engineer  ",
                    "jobUrl": " https://jobs.ashbyhq.com/acme/job-1 ",
                    "location": "  New York, NY  ",
                },
                {
                    "id": "job-2",
                    "title": "Product Manager",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/job-2",
                    "location": {"name": "Remote"},
                },
                {"id": "missing-url", "title": "Incomplete"},
                "not-a-job",
            ]
        })
        board = self.adapter.identify_board("https://jobs.ashbyhq.com/acme")

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title="AI Engineer"))

        self.assertEqual(
            fetcher.requested_urls,
            ["https://api.ashbyhq.com/posting-api/job-board/acme"],
        )
        self.assertEqual([candidate.title for candidate in result.candidates], [
            "AI Engineer",
            "Product Manager",
        ])
        self.assertEqual(result.candidates[0].url, "https://jobs.ashbyhq.com/acme/job-1")
        self.assertEqual(result.candidates[0].location, "New York, NY")
        self.assertEqual(result.candidates[1].location, "Remote")
        self.assertEqual(result.candidates[0].raw, {"id": "job-1"})
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.trace["candidate_count"], 2)
        self.assertEqual(result.trace["response_source"], "ashby-fixture")

    def test_missing_identifier_returns_structured_failure(self):
        board = JobBoard(url="https://jobs.ashbyhq.com", provider="ashby")

        result = self.adapter.list_jobs(StubFetcher({"jobs": []}), board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.candidates, [])

    def test_invalid_payload_returns_structured_failure(self):
        board = self.adapter.identify_board("https://jobs.ashbyhq.com/acme")

        invalid_json = self.adapter.list_jobs(StubFetcher("not-json"), board, JobQuery())
        invalid_shape = self.adapter.list_jobs(StubFetcher([]), board, JobQuery())

        self.assertEqual(invalid_json.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(invalid_shape.reason_code, "INVALID_STRUCTURED_DATA")

    def test_empty_jobs_returns_empty_provider_response(self):
        board = self.adapter.identify_board("https://jobs.ashbyhq.com/acme")

        result = self.adapter.list_jobs(StubFetcher({"jobs": []}), board, JobQuery())

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
