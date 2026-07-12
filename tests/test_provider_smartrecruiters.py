import unittest
from pathlib import Path

from job_source_agent.providers.base import JobQuery
from job_source_agent.providers.smartrecruiters import SmartRecruitersAdapter
from job_source_agent.web import Fetcher, Page


ROOT = Path(__file__).resolve().parents[1]


class _StaticFetcher:
    def __init__(self, html: str) -> None:
        self.html = html

    def fetch(self, url: str) -> Page:
        return Page(url=url, html=self.html, source="test")


class SmartRecruitersAdapterTests(unittest.TestCase):
    def test_recognizes_only_public_job_board_host(self):
        adapter = SmartRecruitersAdapter()

        self.assertTrue(adapter.recognizes("https://jobs.smartrecruiters.com/AcmeCorp"))
        self.assertFalse(adapter.recognizes("https://api.smartrecruiters.com/v1/companies/AcmeCorp"))
        self.assertFalse(adapter.recognizes("https://smartrecruiters.com.example.com/AcmeCorp"))

    def test_identifies_company_from_board_or_detail_url(self):
        adapter = SmartRecruitersAdapter()

        board = adapter.identify_board(
            "https://jobs.smartrecruiters.com/AcmeCorp/743999999999999-data-analyst"
        )

        self.assertEqual(board.identifier, "AcmeCorp")
        self.assertEqual(board.provider, "smartrecruiters")

    def test_lists_fixture_candidates_and_preserves_fallback_detail_url(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")

        result = adapter.list_jobs(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True),
            board,
            JobQuery(title="Data Analyst"),
        )

        self.assertIsNone(result.reason_code)
        self.assertEqual([candidate.title for candidate in result.candidates], ["Data Analyst", "Sales Manager"])
        self.assertEqual(
            result.candidates[0].url,
            "https://jobs.smartrecruiters.com/AcmeApi/743999111111111-data-analyst",
        )
        self.assertEqual(
            result.candidates[1].url,
            "https://jobs.smartrecruiters.com/AcmeApi/743999222222222-sales-manager",
        )
        self.assertEqual(result.trace["candidate_count"], 2)

    def test_normalizes_location_and_relative_detail_url(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")
        fetcher = _StaticFetcher(
            '{"content":[{"name":"ML Engineer","id":"job-1",'
            '"location":{"city":"Paris","region":"Ile-de-France","country":"FR"},'
            '"actions":{"details":"/AcmeApi/job-1"}}]}'
        )

        result = adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(result.candidates[0].location, "Paris, Ile-de-France, FR")
        self.assertEqual(result.candidates[0].url, "https://jobs.smartrecruiters.com/AcmeApi/job-1")

    def test_reports_invalid_structured_data(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")

        result = adapter.list_jobs(_StaticFetcher("not json"), board, JobQuery())

        self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(result.candidates, [])


if __name__ == "__main__":
    unittest.main()
