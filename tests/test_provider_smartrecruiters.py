import unittest
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
        self.assertFalse(adapter.recognizes("https://user@jobs.smartrecruiters.com/AcmeCorp"))
        self.assertFalse(adapter.recognizes("https://jobs.smartrecruiters.com:8443/AcmeCorp"))

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

    def test_uses_keyword_query_and_follows_bounded_offset_pages(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")

        class PagedFetcher:
            def __init__(self):
                self.requested_urls = []

            def fetch(self, url):
                self.requested_urls.append(url)
                params = parse_qs(urlparse(url).query)
                offset = int(params.get("offset", ["0"])[0])
                payload = {
                    "totalFound": 2,
                    "limit": 1,
                    "offset": offset,
                    "content": [
                        {
                            "name": "Data Analyst" if offset == 0 else "Senior Data Analyst",
                            "id": f"job-{offset}",
                        }
                    ],
                }
                return Page(url=url, final_url=url, html=json.dumps(payload), source="paged")

        fetcher = PagedFetcher()
        result = adapter.list_jobs(fetcher, board, JobQuery(title="Target Analyst"))

        first_params = parse_qs(urlparse(fetcher.requested_urls[0]).query)
        second_params = parse_qs(urlparse(fetcher.requested_urls[1]).query)
        self.assertEqual(first_params["q"], ["Target Analyst"])
        self.assertNotIn("offset", first_params)
        self.assertEqual(second_params["offset"], ["1"])
        self.assertEqual(result.trace["page_count"], 2)
        self.assertEqual(result.trace["total_found"], 2)
        self.assertFalse(result.trace["exact_title_found"])
        self.assertEqual(len(result.candidates), 2)

    def test_stops_pagination_after_exact_normalized_title(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")

        class ExactFetcher:
            def __init__(self):
                self.requested_urls = []

            def fetch(self, url):
                self.requested_urls.append(url)
                return Page(
                    url=url,
                    final_url=url,
                    html=json.dumps({
                        "totalFound": 300,
                        "limit": 100,
                        "offset": 0,
                        "content": [{"name": "  Data   Analyst ", "id": "job-1"}],
                    }),
                )

        fetcher = ExactFetcher()
        result = adapter.list_jobs(fetcher, board, JobQuery(title="data analyst"))

        self.assertEqual(len(fetcher.requested_urls), 1)
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(result.candidates[0].title, "Data   Analyst")

    def test_rejects_cross_company_api_redirect(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")

        class RedirectFetcher:
            def fetch(self, url):
                return Page(
                    url=url,
                    final_url="https://api.smartrecruiters.com/v1/companies/Other/postings",
                    html='{"content":[]}',
                )

        result = adapter.list_jobs(RedirectFetcher(), board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertIn("redirected outside", result.trace["error"])

    def test_rejects_cross_company_detail_without_safe_id_fallback(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")
        fetcher = _StaticFetcher(
            '{"content":[{"name":"External","actions":'
            '{"details":"https://jobs.smartrecruiters.com/Other/job-1"}}]}'
        )

        result = adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")

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
