import unittest

from job_source_agent.scoring import (
    is_likely_job_detail,
    is_likely_job_listing_page,
    score_career_link,
    score_job_link,
)
from job_source_agent.web import RawLink


class ScoringTests(unittest.TestCase):
    def test_career_link_prefers_careers_keyword(self):
        candidate = score_career_link(
            RawLink(
                url="https://example.com/careers",
                text="Careers",
                source_url="https://example.com",
            )
        )
        self.assertGreaterEqual(candidate.score, 100)

    def test_job_link_prefers_ats_job_detail(self):
        candidate = score_job_link(
            RawLink(
                url="https://jobs.lever.co/acme/abc-123",
                text="Software Engineer Intern",
                source_url="https://jobs.lever.co/acme",
            ),
            career_page_url="https://jobs.lever.co/acme",
        )
        self.assertGreaterEqual(candidate.score, 200)
        self.assertTrue(is_likely_job_detail(candidate))

    def test_generic_jobs_page_is_listing_not_detail(self):
        candidate = score_job_link(
            RawLink(
                url="https://example.com/careers/jobs",
                text="Explore open roles",
                source_url="https://example.com/careers",
            ),
            career_page_url="https://example.com/careers",
        )
        self.assertFalse(is_likely_job_detail(candidate))
        self.assertTrue(is_likely_job_listing_page(candidate))

    def test_article_about_jobs_is_not_career_link(self):
        candidate = score_career_link(
            RawLink(
                url="https://example.com/how-is-ai-going-to-affect-jobs-across-various-industries",
                text="How is AI going to affect jobs across various industries?",
                source_url="https://example.com",
            )
        )

        self.assertLess(candidate.score, 50)


if __name__ == "__main__":
    unittest.main()
