import unittest
from pathlib import Path

from job_source_agent.opening_matcher import (
    JobOpeningMatcher,
    build_provider_search_urls,
    detect_provider,
    score_title_match,
)
from job_source_agent.web import Fetcher


ROOT = Path(__file__).resolve().parents[1]


class OpeningMatcherTests(unittest.TestCase):
    def test_title_match_scores_relevant_title_higher(self):
        good_score, _ = score_title_match("Product Manager, Ads", "Product Manager, Ads")
        weak_score, _ = score_title_match("Software Engineer", "Product Manager, Ads")

        self.assertGreater(good_score, weak_score)

    def test_google_search_results_match_linkedin_title(self):
        matcher = JobOpeningMatcher(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        match, trace = matcher.match(
            "https://www.google.com/about/careers/applications/",
            "Product Manager, Ads",
        )

        self.assertIsNotNone(match)
        self.assertIn("123-product-manager-ads", match.url)
        self.assertEqual(trace["provider"], "google_careers")

    def test_provider_detection_covers_enterprise_ats(self):
        cases = {
            "https://company.wd5.myworkdayjobs.com/en-US/acme": "workday",
            "https://careers-acme.icims.com/jobs/search": "icims",
            "https://jobs.smartrecruiters.com/AcmeCorp": "smartrecruiters",
            "https://acme.successfactors.com/career": "successfactors",
        }

        for url, provider in cases.items():
            with self.subTest(url=url):
                self.assertEqual(detect_provider(url), provider)

    def test_enterprise_ats_opening_matchers(self):
        matcher = JobOpeningMatcher(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )
        cases = {
            "https://company.wd5.myworkdayjobs.com/en-US/acme": "Data-Analyst_R123",
            "https://careers-acme.icims.com/jobs/search": "/jobs/1234/data-analyst/job",
            "https://jobs.smartrecruiters.com/AcmeCorp": "743999999999999-data-analyst",
            "https://acme.successfactors.com/career": "career_job_req_id=987",
        }

        for url, expected_url_part in cases.items():
            with self.subTest(url=url):
                match, trace = matcher.match(url, "Data Analyst")
                self.assertIsNotNone(match)
                self.assertIn(expected_url_part, match.url)
                self.assertEqual(trace["provider"], detect_provider(url))

    def test_provider_search_urls_are_provider_specific(self):
        cases = {
            "https://company.wd5.myworkdayjobs.com/en-US/acme": "q=Data+Analyst",
            "https://careers-acme.icims.com/jobs/search": "searchKeyword=Data+Analyst",
            "https://jobs.smartrecruiters.com/AcmeCorp": "search=Data+Analyst",
            "https://acme.successfactors.com/career": "keyword=Data+Analyst",
        }

        for url, expected_query in cases.items():
            with self.subTest(url=url):
                urls = build_provider_search_urls(url, "Data Analyst")
                self.assertTrue(any(expected_query in search_url for search_url in urls))


if __name__ == "__main__":
    unittest.main()
