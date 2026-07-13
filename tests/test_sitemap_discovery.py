import unittest
from pathlib import Path

from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import FetchError, Fetcher, Page


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

    def test_sitemap_index_fanout_is_bounded_when_one_index_has_many_children(self):
        base = "https://large.example"
        children = [f"{base}/region-{index}.xml" for index in range(20)]
        index_xml = "<sitemapindex>" + "".join(
            f"<sitemap><loc>{url}</loc></sitemap>" for url in children
        ) + "</sitemapindex>"

        class MappingFetcher:
            def __init__(self):
                self.urls = []

            def fetch(self, url, data=None, headers=None):
                self.urls.append(url)
                if url == f"{base}/robots.txt":
                    raise FetchError("missing")
                if url == f"{base}/sitemap.xml":
                    return Page(url=url, html=index_xml)
                if url == f"{base}/sitemap_index.xml":
                    raise FetchError("missing")
                if url in children:
                    return Page(url=url, html="<urlset />")
                raise AssertionError(f"unexpected fetch: {url}")

        fetcher = MappingFetcher()
        _links, trace = JobSourceAgent(fetcher)._sitemap_candidates(base)

        checked = trace["sitemaps_checked"]
        self.assertEqual(len(checked), 10)
        self.assertTrue(trace["fanout_limit_reached"])
        self.assertEqual(trace["sitemaps_not_scheduled"], 12)
        self.assertIn(children[7], fetcher.urls)
        self.assertNotIn(children[8], fetcher.urls)


if __name__ == "__main__":
    unittest.main()
