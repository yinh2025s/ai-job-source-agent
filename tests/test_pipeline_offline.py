import unittest
from pathlib import Path

from job_source_agent.linkedin import load_company_inputs
from job_source_agent.models import CompanyInput, LinkCandidate
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import FetchError, Fetcher, Page, RawLink


ROOT = Path(__file__).resolve().parents[1]


class OfflinePipelineTests(unittest.TestCase):
    def test_sample_jobs_discover_successfully(self):
        companies = load_company_inputs(ROOT / "samples" / "linkedin_jobs.json")
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        results = [agent.discover(company) for company in companies]

        self.assertEqual([result.status for result in results], ["success", "success"])
        self.assertEqual(results[0].career_page_url, "https://jobs.lever.co/aurora-data")
        self.assertIn("d9d64766", results[0].open_position_url)
        self.assertEqual(results[1].career_page_url, "https://nimbus-robotics.example/careers")
        self.assertIn("5012345001", results[1].open_position_url)

    def test_provider_root_uses_linkedin_title_for_opening_match(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )
        company = CompanyInput(
            company_name="Google",
            company_website_url="https://www.google.com",
            career_root_url="https://www.google.com/about/careers/applications/",
            job_title="Product Manager, Ads",
        )

        result = agent.discover(company)

        self.assertEqual(result.status, "success")
        self.assertIn("123-product-manager-ads", result.open_position_url)

    def test_brand_join_path_can_be_discovered(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        career_url, trace = agent.find_career_page("https://brandedjoin.example")

        self.assertEqual(career_url, "https://brandedjoin.example/join-brandedjoin")
        self.assertIn("brand-specific join path", trace["selected"]["reasons"])

    def test_target_title_prevents_unrelated_opening_match(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        opening_url, job_list_url, trace = agent.find_open_position(
            "https://jobs.lever.co/titlefilter",
            target_title="AI Engineer",
        )

        self.assertIsNone(opening_url)
        self.assertEqual(job_list_url, "https://jobs.lever.co/titlefilter")
        self.assertEqual(trace["opening_error"], "specific_opening_not_found")

    def test_rippling_board_is_not_mistaken_for_a_job_detail(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        opening_url, job_list_url, trace = agent.find_open_position(
            "https://ats.rippling.com/embed/acme-rippling/jobs",
            target_title="Data Analyst",
        )

        self.assertIn("b4f5c9d3", opening_url)
        self.assertEqual(job_list_url, "https://ats.rippling.com/embed/acme-rippling/jobs")
        self.assertEqual(trace["selected"]["provider"], "rippling")

    def test_provider_board_is_kept_when_its_page_contains_only_assets(self):
        class WorkdayAssetFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if url == "https://example.com/careers":
                    return Page(
                        url=url,
                        final_url=url,
                        html='<a href="https://tenant.wd1.myworkdayjobs.com/acme">Search jobs</a>',
                    )
                if url == "https://tenant.wd1.myworkdayjobs.com/acme":
                    return Page(
                        url=url,
                        final_url=url,
                        html='<img src="https://tenant.wd1.myworkdayjobs.com/acme/assets/logo">',
                    )
                if "/wday/cxs/" in url:
                    raise FetchError("no matching job")
                raise FetchError(f"unexpected URL: {url}")

        agent = JobSourceAgent(WorkdayAssetFetcher(offline=True), max_job_pages=3)

        opening_url, job_list_url, _trace = agent.find_open_position(
            "https://example.com/careers",
            target_title="Data Analyst",
        )

        self.assertIsNone(opening_url)
        self.assertEqual(job_list_url, "https://tenant.wd1.myworkdayjobs.com/acme")

    def test_career_page_can_be_discovered_from_search_fallback(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True),
            max_candidates=4,
            enable_sitemap_discovery=False,
        )

        career_url, trace = agent.find_career_page(
            "https://searchfallback.example",
            company_name="Search Fallback",
        )

        self.assertEqual(career_url, "https://searchfallback.example/real-careers")
        self.assertEqual(trace["selected_from"], "search_discovery")

    def test_career_page_can_be_discovered_from_derived_ats_board(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True),
            max_candidates=4,
            max_career_candidate_fetches=2,
            max_ats_board_fetches=3,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        career_url, trace = agent.find_career_page(
            "https://atsprobe.example",
            company_name="ATS Probe",
            target_title="Data Analyst",
        )

        self.assertEqual(career_url, "https://jobs.lever.co/atsprobe")
        self.assertEqual(trace["selected_from"], "ats_board_discovery")
        self.assertIn("derived Lever board candidate", trace["selected"]["reasons"])
        self.assertEqual(trace["ats_board_discovery"]["provider_board_verification"][0]["method"], "page_job_links")

    def test_unverified_derived_ats_board_is_rejected(self):
        class EmptyAshbyFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "api.ashbyhq.com" in url:
                    raise FetchError("HTTP Error 404: Not Found")
                return Page(url=url, final_url=url, html="<html><body>Ashby</body></html>")

        agent = JobSourceAgent(EmptyAshbyFetcher(offline=True), max_ats_board_fetches=1)
        trace = {"candidate_fetch_errors": []}

        selected = agent._select_verified_career_candidate(
            [
                LinkCandidate(
                    "https://jobs.ashbyhq.com/missing",
                    "",
                    "https://missing.example",
                    180,
                    ["derived Ashby board candidate"],
                )
            ],
            trace,
            max_fetches=1,
        )

        self.assertIsNone(selected)
        self.assertEqual(trace["provider_board_verification"][0]["method"], "unverified")

    def test_common_path_candidates_include_www_variant(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True),
            max_candidates=80,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        career_url, trace = agent.find_career_page("https://wwwvariant.example")

        self.assertEqual(career_url, "https://www.wwwvariant.example/careers")
        self.assertEqual(trace["selected"]["url"], "https://www.wwwvariant.example/careers")

    def test_common_path_candidates_include_localized_us_paths(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True),
            max_candidates=80,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        career_url, trace = agent.find_career_page("https://localized.example")

        self.assertEqual(career_url, "https://localized.example/en-us/careers")
        self.assertEqual(trace["selected"]["url"], "https://localized.example/en-us/careers")

    def test_localized_paths_include_nested_company_careers_route(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        paths = agent._locale_career_paths("/en/company/overview")

        self.assertIn("/en/company/careers", paths)

    def test_localized_company_careers_path_is_prioritized(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        candidate = agent._score_career_candidate(
            RawLink(
                "https://example.com/en/company/careers",
                "",
                "https://example.com/en",
                "path_probe",
            )
        )

        self.assertIn("localized career section", candidate.reasons)
        self.assertGreater(candidate.score, 240)

    def test_short_career_probe_ranks_above_deep_career_jobs_probe(self):
        agent = JobSourceAgent(Fetcher(offline=True), enable_career_search=False)

        ranked = sorted(
            [agent._score_career_candidate(link) for link in agent._common_path_candidates("https://example.com")],
            key=lambda candidate: candidate.score,
            reverse=True,
        )
        urls = [candidate.url for candidate in ranked]

        self.assertLess(urls.index("https://example.com/careers"), urls.index("https://example.com/careers/jobs"))

    def test_homepage_career_link_ranks_above_same_path_probe(self):
        agent = JobSourceAgent(Fetcher(offline=True), enable_career_search=False)
        homepage_link = agent._score_career_candidate(
            RawLink("https://example.com/about/careers", "Careers", "https://example.com", "page_link")
        )
        path_probe = agent._score_career_candidate(
            RawLink("https://example.com/careers", "", "https://example.com", "path_probe")
        )

        self.assertGreater(homepage_link.score, path_probe.score)

    def test_error_page_is_not_treated_as_career_page(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        self.assertTrue(
            agent._looks_like_error_page(
                "https://example.com/errors/404/",
                "<html><title>Careers</title><body>Page not found</body></html>",
            )
        )

    def test_career_candidate_fetch_budget_is_respected(self):
        class BudgetFetcher(Fetcher):
            def fetch(self, url, data=None, headers=None):
                if "slow" in url:
                    raise FetchError("timeout")
                return Page(url=url, html="<html><body>Open roles and careers</body></html>", final_url=url)

        agent = JobSourceAgent(
            BudgetFetcher(offline=True),
            max_candidates=2,
            max_career_candidate_fetches=1,
        )
        trace = {"candidate_fetch_errors": []}

        selected = agent._select_verified_career_candidate(
            [
                LinkCandidate("https://slow.example/careers", "careers", "https://example.com", 100, []),
                LinkCandidate("https://good.example/careers", "careers", "https://example.com", 100, []),
            ],
            trace,
        )

        self.assertIsNone(selected)
        self.assertEqual(len(trace["candidate_fetch_errors"]), 1)
        self.assertEqual(trace["candidate_fetch_budget_exhausted"]["limit"], 1)

    def test_zero_career_candidate_fetch_budget_skips_candidates(self):
        agent = JobSourceAgent(Fetcher(offline=True), max_career_candidate_fetches=0)
        trace = {"candidate_fetch_errors": []}

        selected = agent._select_verified_career_candidate(
            [LinkCandidate("https://good.example/careers", "careers", "https://example.com", 100, [])],
            trace,
        )

        self.assertIsNone(selected)
        self.assertEqual(trace["candidate_fetch_budget_exhausted"]["limit"], 0)


if __name__ == "__main__":
    unittest.main()
