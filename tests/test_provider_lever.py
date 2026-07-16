from __future__ import annotations

import json
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.lever import ADAPTER, LeverAdapter
from job_source_agent.web import Page


class StubFetcher:
    def __init__(self, payload):
        self.payload = payload
        self.urls: list[str] = []

    def fetch(self, url, data=None, headers=None):
        self.urls.append(url)
        html = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return Page(url=url, html=html, final_url=url, source="lever-fixture")


class LeverAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = LeverAdapter()

    def test_exports_provider_adapter(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertEqual("lever", ADAPTER.name)
        self.assertTrue(ADAPTER.supports_listing)

    def test_recognizes_jobs_lever_co_only(self):
        self.assertTrue(self.adapter.recognizes("https://jobs.lever.co/acme"))
        self.assertTrue(self.adapter.recognizes("https://jobs.lever.co/acme/123"))
        self.assertFalse(self.adapter.recognizes("https://api.lever.co/v0/postings/acme"))
        self.assertFalse(self.adapter.recognizes("https://jobs.lever.co.example.com/acme"))
        self.assertFalse(self.adapter.recognizes("https://example.com/jobs.lever.co/acme"))
        self.assertFalse(self.adapter.recognizes("https://[invalid"))

    def test_identifies_account_slug(self):
        board = self.adapter.identify_board("https://jobs.lever.co/acme/abc-123")

        self.assertEqual(
            JobBoard(
                url="https://jobs.lever.co/acme",
                provider="lever",
                identifier="acme",
            ),
            board,
        )
        self.assertIsNone(self.adapter.identify_board("https://jobs.lever.co/"))
        self.assertIsNone(self.adapter.identify_board("https://example.com/acme"))

    def test_lists_and_normalizes_postings(self):
        fetcher = StubFetcher(
            [
                {
                    "id": "job-1",
                    "text": "Senior AI Engineer",
                    "hostedUrl": "https://jobs.lever.co/acme/job-1#details",
                    "applyUrl": "https://jobs.lever.co/acme/job-1/apply",
                    "categories": {"location": "New York, NY"},
                },
                {
                    "id": "job-2",
                    "text": "ML Intern",
                    "hostedUrl": "not-a-url",
                    "applyUrl": "https://jobs.lever.co/acme/job-2/apply#application",
                    "categories": {"location": " Remote "},
                },
                {"id": "missing-url", "text": "Skipped"},
                "not-a-posting",
            ]
        )
        board = JobBoard(
            url="https://jobs.lever.co/acme",
            provider="lever",
            identifier="acme",
        )

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title="AI Engineer"))

        self.assertEqual(
            ["https://api.lever.co/v0/postings/acme?mode=json"],
            fetcher.urls,
        )
        self.assertIsNone(result.reason_code)
        self.assertEqual(2, len(result.candidates))
        self.assertEqual("https://jobs.lever.co/acme/job-1", result.candidates[0].url)
        self.assertEqual("New York, NY", result.candidates[0].location)
        self.assertEqual(
            "https://jobs.lever.co/acme/job-2/apply",
            result.candidates[1].url,
        )
        self.assertEqual("Remote", result.candidates[1].location)
        self.assertEqual("lever-fixture", result.trace["response_source"])
        self.assertEqual(4, result.trace["posting_count"])
        self.assertEqual(2, result.trace["candidate_count"])

    def test_reports_missing_identifier_without_fetching(self):
        fetcher = StubFetcher([])
        board = JobBoard(url="https://jobs.lever.co/", provider="lever")

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual("PROVIDER_VARIANT_UNSUPPORTED", result.reason_code)
        self.assertEqual([], fetcher.urls)

    def test_reports_invalid_or_unexpected_json(self):
        board = JobBoard(
            url="https://jobs.lever.co/acme",
            provider="lever",
            identifier="acme",
        )
        for payload in ("not json", {"postings": []}):
            with self.subTest(payload=payload):
                result = self.adapter.list_jobs(StubFetcher(payload), board, JobQuery())
                self.assertEqual("INVALID_STRUCTURED_DATA", result.reason_code)
                self.assertEqual([], result.candidates)

    def test_reports_empty_provider_response(self):
        board = JobBoard(
            url="https://jobs.lever.co/acme",
            provider="lever",
            identifier="acme",
        )

        result = self.adapter.list_jobs(StubFetcher([]), board, JobQuery())

        self.assertEqual("EMPTY_PROVIDER_RESPONSE", result.reason_code)
        self.assertEqual([], result.candidates)
        self.assertFalse(result.retryable)

    def test_api_url_escapes_identifier(self):
        self.assertEqual(
            "https://api.lever.co/v0/postings/acme%20labs?mode=json",
            self.adapter.api_url(" acme labs "),
        )


if __name__ == "__main__":
    unittest.main()
