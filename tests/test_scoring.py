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

    def test_static_text_file_is_not_job_detail(self):
        candidate = score_job_link(
            RawLink(
                url="https://apply.workable.com/example/llms.txt",
                text="",
                source_url="https://apply.workable.com/example",
            ),
            career_page_url="https://apply.workable.com/example",
        )

        self.assertFalse(is_likely_job_detail(candidate))
        self.assertLess(candidate.score, 0)

    def test_ats_asset_path_is_not_a_job_listing(self):
        candidate = score_job_link(
            RawLink(
                url="https://oneok.wd1.myworkdayjobs.com/ONEOK/assets/logo",
                text="",
                source_url="https://oneok.wd1.myworkdayjobs.com/ONEOK",
            ),
            career_page_url="https://oneok.wd1.myworkdayjobs.com/ONEOK",
        )

        self.assertFalse(is_likely_job_detail(candidate))
        self.assertFalse(is_likely_job_listing_page(candidate))
        self.assertLess(candidate.score, 0)

    def test_ats_login_is_not_a_job_listing(self):
        candidate = score_job_link(
            RawLink(
                url="https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/Acme/my-profile/sign-in",
                text="Login",
                source_url="https://example.com/careers",
            ),
            career_page_url="https://example.com/careers",
        )

        self.assertFalse(is_likely_job_detail(candidate))
        self.assertFalse(is_likely_job_listing_page(candidate))

    def test_search_results_route_is_a_listing_candidate(self):
        candidate = score_job_link(
            RawLink(
                url="https://careers.example.com/us/en/search-results",
                text="Explore roles",
                source_url="https://careers.example.com/us/en",
            ),
            career_page_url="https://careers.example.com/us/en",
        )

        self.assertTrue(is_likely_job_listing_page(candidate))


if __name__ == "__main__":
    unittest.main()
