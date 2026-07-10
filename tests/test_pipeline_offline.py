import unittest
from pathlib import Path

from job_source_agent.linkedin import load_company_inputs
from job_source_agent.models import CompanyInput
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import Fetcher


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
        self.assertIn("career keyword 'join us'", trace["selected"]["reasons"])

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
        self.assertEqual(trace["opening_error"], "open_position_not_found")

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


if __name__ == "__main__":
    unittest.main()
