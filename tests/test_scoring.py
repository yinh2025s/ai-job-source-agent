import unittest

from job_source_agent.scoring import (
    is_ats_url,
    is_explicit_job_list_command,
    is_likely_job_detail,
    is_likely_job_listing_page,
    score_career_link,
    score_job_link,
)
from job_source_agent.web import RawLink


class ScoringTests(unittest.TestCase):
    def test_job_list_command_taxonomy_is_shared_and_bounded(self):
        for text in (
            "Find jobs",
            "Explore roles",
            "View open jobs",
            "Browse job opportunities",
            "Search All Jobs",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_explicit_job_list_command(text))

        for text in ("Meet our team", "Explore Bosch", "Job benefits"):
            with self.subTest(text=text):
                self.assertFalse(is_explicit_job_list_command(text))

    def test_find_jobs_link_receives_explicit_listing_evidence(self):
        candidate = score_job_link(
            RawLink(
                url="https://jobs.example.com/en/",
                text="Find jobs",
                source_url="https://www.example.com/careers/",
            ),
            career_page_url="https://www.example.com/careers/",
        )

        self.assertIn("explicit job-list command", candidate.reasons)

    def test_search_all_jobs_command_is_not_penalized_as_generic_all_jobs_text(self):
        candidate = score_job_link(
            RawLink(
                url="https://jobs.parent.example/en/",
                text="Search All Jobs",
                source_url="https://www.example.com/careers/",
            ),
            career_page_url="https://www.example.com/careers/",
        )

        self.assertIn("explicit job-list command", candidate.reasons)
        self.assertNotIn("negative keyword 'all jobs'", candidate.reasons)
        self.assertGreaterEqual(candidate.score, 30)

    def test_whitecarrot_hosts_are_known_ats_domains(self):
        self.assertTrue(is_ats_url("https://app.whitecarrot.io/careers/acme"))
        self.assertTrue(is_ats_url("https://acme.whitecarrot.ai/jobs"))
        self.assertFalse(is_ats_url("https://whitecarrot.ai.example.com/jobs"))

    def test_career_link_prefers_careers_keyword(self):
        candidate = score_career_link(
            RawLink(
                url="https://example.com/careers",
                text="Careers",
                source_url="https://example.com",
            )
        )
        self.assertGreaterEqual(candidate.score, 100)

    def test_career_asset_is_rejected_even_when_filename_contains_keyword(self):
        candidate = score_career_link(
            RawLink(
                url="https://example.com/uploads/life-at-careers.webp",
                text="Careers image",
                source_url="https://example.com/sitemap.xml",
            )
        )

        self.assertEqual(candidate.score, -500)
        self.assertEqual(candidate.reasons, ["static/resource URL"])

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

    def test_same_page_job_route_with_allowlisted_stable_id_is_detail(self):
        source_url = "https://zello.com/careers/"
        for key, job_id in (
            ("jid", "f8f40e9f-4c49-4a3d-9d89-750fc2409835"),
            ("jobId", "123456"),
            ("job_id", "engineering-7f3a9c"),
        ):
            with self.subTest(key=key, job_id=job_id):
                candidate = score_job_link(
                    RawLink(
                        url=f"{source_url}job/?{key}={job_id}",
                        text="Machine Learning Engineer",
                        source_url=source_url,
                    ),
                    career_page_url=source_url,
                )

                self.assertTrue(is_likely_job_detail(candidate))
                self.assertIn("job-detail query pattern", candidate.reasons)
                self.assertNotIn("same as career page", candidate.reasons)

    def test_job_query_detail_requires_strict_same_origin_child_path_and_allowlisted_id(self):
        source_url = "https://zello.com/careers/"
        invalid_urls = (
            "https://zello.com/careers/jobs?jid=123",
            source_url + "job/?jid=",
            source_url + "job/?q=engineer",
            source_url + "job/?search=engineer",
            source_url + "job/?filter=remote",
            source_url + "job/?posting=123",
            source_url + "job/?jid=123&q=engineer",
            source_url + "job/?jid=" + "1" * 25,
            "https://zello.com/about/job/?jid=123",
            "https://jobs.zello.com/careers/job/?jid=123",
        )

        for url in invalid_urls:
            with self.subTest(url=url):
                candidate = score_job_link(
                    RawLink(url=url, text="Engineer", source_url=source_url),
                    career_page_url=source_url,
                )

                self.assertFalse(is_likely_job_detail(candidate))

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

    def test_oracle_job_detail_remains_recognized(self):
        candidate = score_job_link(
            RawLink(
                url=(
                    "https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/"
                    "sites/Acme/JoB/Software-Engineer/123"
                ),
                text="Software Engineer",
                source_url="https://example.com/careers",
            ),
            career_page_url="https://example.com/careers",
        )

        self.assertTrue(is_likely_job_detail(candidate))
        self.assertFalse(is_likely_job_listing_page(candidate))

    def test_workday_boards_and_job_details_remain_recognized(self):
        board_url = "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers"
        listing_url = board_url + "/jobs"
        detail_url = board_url + "/JOB/New-York/Software-Engineer_R123"

        for url, is_detail in (
            (board_url, False),
            (listing_url, False),
            (detail_url, True),
        ):
            with self.subTest(url=url):
                candidate = score_job_link(
                    RawLink(
                        url=url,
                        text="Software Engineer" if is_detail else "Search Jobs",
                        source_url="https://example.com/careers",
                    ),
                    career_page_url="https://example.com/careers",
                )

                self.assertEqual(is_likely_job_detail(candidate), is_detail)
                self.assertEqual(is_likely_job_listing_page(candidate), not is_detail)

    def test_workday_introduce_yourself_routes_are_not_jobs(self):
        urls = (
            "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers/introduceYourself",
            "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers/INTRODUCEYOURSELF/",
            (
                "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers/"
                "JoB/Talent-Community/IntroduceYourself"
            ),
        )

        for url in urls:
            with self.subTest(url=url):
                candidate = score_job_link(
                    RawLink(
                        url=url,
                        text="Introduce Yourself",
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

    def test_explicit_all_jobs_route_is_a_listing_candidate(self):
        candidate = score_job_link(
            RawLink(
                url="https://careers.example.com/en/all-jobs/",
                text="Search Jobs",
                source_url="https://careers.example.com/en/",
            ),
            career_page_url="https://careers.example.com/en/",
        )

        self.assertTrue(is_likely_job_listing_page(candidate))
        self.assertIn("explicit all-jobs route", candidate.reasons)

    def test_unlabeled_all_jobs_route_is_not_a_listing_candidate(self):
        candidate = score_job_link(
            RawLink(
                url="https://careers.example.com/en/all-jobs/",
                text="",
                source_url="https://careers.example.com/en/",
            ),
            career_page_url="https://careers.example.com/en/",
        )

        self.assertFalse(is_likely_job_listing_page(candidate))

    def test_first_party_all_jobs_numeric_child_is_job_detail(self):
        source_url = "https://careers.example.com/en/all-jobs/"
        candidate = score_job_link(
            RawLink(
                url=(
                    source_url
                    + "8036603/product-manager/?gh_jid=8036603"
                ),
                text="Product Manager",
                source_url=source_url,
            ),
            career_page_url=source_url,
        )

        self.assertTrue(is_likely_job_detail(candidate))
        self.assertIn("first-party numeric job detail route", candidate.reasons)

    def test_all_jobs_numeric_detail_contract_rejects_ambiguous_routes(self):
        source_url = "https://careers.example.com/en/all-jobs/"
        invalid_urls = (
            "https://careers.unrelated.example/en/all-jobs/8036603/product-manager/",
            source_url + "product-manager/8036603/",
            source_url + "803/product-manager/",
            source_url + "8036603/product-manager/extra/",
            source_url + "8036603/product-manager/?gh_jid=9999999",
            source_url + "8036603/product-manager/?gh_jid=8036603&utm_source=test",
            "https://careers.example.com/en/teams/8036603/product-manager/",
        )

        for url in invalid_urls:
            with self.subTest(url=url):
                candidate = score_job_link(
                    RawLink(
                        url=url,
                        text="Product Manager",
                        source_url=source_url,
                    ),
                    career_page_url=source_url,
                )
                self.assertFalse(is_likely_job_detail(candidate))

    def test_nested_job_results_route_is_a_listing_not_a_detail(self):
        candidate = score_job_link(
            RawLink(
                url="https://example.com/en/careers/job-results-global?redirected=true",
                text="",
                source_url="https://example.com/en/careers",
            ),
            career_page_url="https://example.com/en/careers",
        )

        self.assertFalse(is_likely_job_detail(candidate))
        self.assertTrue(is_likely_job_listing_page(candidate))
        self.assertIn("job-listing route name", candidate.reasons)

    def test_career_expertise_page_is_not_a_job_detail_or_listing(self):
        candidate = score_job_link(
            RawLink(
                url="https://example.com/en/careers/areas-of-expertise/design-mechanical-engineering",
                text="Learn More",
                source_url="https://example.com/en/careers",
            ),
            career_page_url="https://example.com/en/careers",
        )

        self.assertFalse(is_likely_job_detail(candidate))
        self.assertFalse(is_likely_job_listing_page(candidate))

    def test_known_ats_embed_board_is_a_listing_candidate(self):
        candidate = score_job_link(
            RawLink(
                url="https://jobs.ashbyhq.com/Acme/embed?version=2",
                text="",
                source_url="https://acme.example/careers",
            ),
            career_page_url="https://acme.example/careers",
        )

        self.assertTrue(is_likely_job_listing_page(candidate))


if __name__ == "__main__":
    unittest.main()
