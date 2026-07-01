import unittest
from pathlib import Path

from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import Fetcher


ROOT = Path(__file__).resolve().parents[1]


class SitemapDiscoveryTests(unittest.TestCase):
    def test_career_page_can_be_discovered_from_sitemap(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        career_url, trace = agent.find_career_page("https://sitemapco.example")

        self.assertEqual(career_url, "https://sitemapco.example/careers")
        self.assertGreater(trace["sitemap_discovery"]["candidate_count"], 0)

    def test_career_page_can_be_discovered_from_sitemap_index(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        career_url, trace = agent.find_career_page("https://sitemapindex.example")

        self.assertEqual(career_url, "https://sitemapindex.example/company/careers")
        checked_urls = [item["url"] for item in trace["sitemap_discovery"]["sitemaps_checked"]]
        self.assertIn("https://sitemapindex.example/pages.xml", checked_urls)


if __name__ == "__main__":
    unittest.main()
