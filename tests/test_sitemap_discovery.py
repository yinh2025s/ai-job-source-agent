import unittest
from pathlib import Path

from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import FetchError, Fetcher, Page, RawLink


ROOT = Path(__file__).resolve().parents[1]


class SitemapDiscoveryTests(unittest.TestCase):
    def test_target_region_sitemap_is_prioritized_and_stops_cross_region_fanout(self):
        base = "https://global.example"
        au_sitemap = f"{base}/au/sitemaps/sitemap_au_en.xml"
        us_sitemap = f"{base}/us/sitemaps/sitemap_us_en.xml"

        class RegionalFetcher:
            def __init__(self):
                self.urls = []

            def fetch(self, url, data=None, headers=None):
                self.urls.append(url)
                if url.endswith("/robots.txt"):
                    raise FetchError("missing")
                if url == f"{base}/sitemap.xml":
                    return Page(
                        url=url,
                        html=(
                            "<sitemapindex>"
                            f"<sitemap><loc>{au_sitemap}</loc></sitemap>"
                            f"<sitemap><loc>{us_sitemap}</loc></sitemap>"
                            "</sitemapindex>"
                        ),
                    )
                if url == f"{base}/sitemap_index.xml":
                    return Page(url=url, html="<urlset />")
                if url == us_sitemap:
                    return Page(
                        url=url,
                        html=f"<urlset><url><loc>{base}/us/en/careers</loc></url></urlset>",
                    )
                if url == au_sitemap:
                    raise AssertionError("US evidence should stop later cross-region fanout")
                raise FetchError("missing")

        fetcher = RegionalFetcher()
        links, trace = JobSourceAgent(fetcher)._sitemap_candidates(base, target_region="us")

        self.assertEqual([link.url for link in links], [f"{base}/us/en/careers"])
        self.assertEqual(trace["stopped_reason"], "target_region_candidates_found")
        self.assertNotIn(au_sitemap, fetcher.urls)

    def test_target_location_region_outweighs_cross_region_sitemap_keyword_density(self):
        agent = JobSourceAgent(Fetcher(offline=True))
        us_candidate = agent._score_career_candidate(
            RawLink(
                url="https://global.example/us/en/careers",
                text="Careers",
                source_url="https://global.example/sitemap.xml",
                origin="sitemap",
            ),
            "https://global.example",
            target_location="Grand Rapids, MI",
        )
        au_candidate = agent._score_career_candidate(
            RawLink(
                url="https://global.example/au/en/careers/work-with-us",
                text="Careers and opportunities",
                source_url="https://global.example/sitemap.xml",
                origin="sitemap",
            ),
            "https://global.example",
            target_location="Grand Rapids, MI",
        )

        self.assertGreater(us_candidate.score, au_candidate.score)
        self.assertIn("matches target location region 'us'", us_candidate.reasons)

    def test_language_only_locale_is_not_treated_as_a_conflicting_region(self):
        candidate = JobSourceAgent(Fetcher(offline=True))._score_career_candidate(
            RawLink(
                url="https://global.example/en/careers",
                text="Careers",
                source_url="https://global.example",
                origin="page_link",
            ),
            "https://global.example",
            target_location="United States",
        )

        self.assertFalse(
            any("conflicts with target location region" in reason for reason in candidate.reasons)
        )

    def test_nested_job_index_reaches_target_region_before_static_sitemap_fanout(self):
        base = "https://inventory.example"
        jobs_index = f"{base}/jobsindex.xml"
        us_jobs = f"{base}/sitemap-company-jobs-unitedstates-en.xml"
        au_jobs = f"{base}/sitemap-company-jobs-australia-en.xml"
        static_children = [f"{base}/sitemap-static-{index}.xml" for index in range(20)]

        class NestedInventoryFetcher:
            def __init__(self):
                self.urls = []

            def fetch(self, url, data=None, headers=None):
                self.urls.append(url)
                if url.endswith("/robots.txt"):
                    raise FetchError("missing")
                if url == f"{base}/sitemap.xml":
                    children = [*static_children, jobs_index]
                    return Page(
                        url=url,
                        html="<sitemapindex>"
                        + "".join(f"<sitemap><loc>{child}</loc></sitemap>" for child in children)
                        + "</sitemapindex>",
                    )
                if url == jobs_index:
                    return Page(
                        url=url,
                        html=(
                            "<sitemapindex>"
                            f"<sitemap><loc>{au_jobs}</loc></sitemap>"
                            f"<sitemap><loc>{us_jobs}</loc></sitemap>"
                            "</sitemapindex>"
                        ),
                    )
                if url == us_jobs:
                    return Page(
                        url=url,
                        html=(
                            "<urlset><url><loc>"
                            f"{base}/en-us/careers/jobs/artificial-intelligence-engineer"
                            "</loc></url></urlset>"
                        ),
                    )
                if url == au_jobs:
                    raise AssertionError("target-region inventory should be fetched first")
                if url == f"{base}/sitemap_index.xml" or url in static_children:
                    return Page(url=url, html="<urlset />")
                raise AssertionError(f"unexpected fetch: {url}")

        fetcher = NestedInventoryFetcher()
        links, trace = JobSourceAgent(fetcher)._sitemap_candidates(base, target_region="us")

        self.assertEqual(
            [link.url for link in links],
            [f"{base}/en-us/careers/jobs/artificial-intelligence-engineer"],
        )
        self.assertIn(jobs_index, fetcher.urls)
        self.assertIn(us_jobs, fetcher.urls)
        self.assertEqual(trace["stopped_reason"], "target_region_candidates_found")

    def test_primary_homepage_career_link_is_verified_before_sitemap_fanout(self):
        base = "https://primary.example"

        class PrimaryFetcher:
            def __init__(self):
                self.urls = []

            def fetch(self, url, data=None, headers=None):
                self.urls.append(url)
                if url.rstrip("/") == base:
                    return Page(url=url, html='<a href="/careers">Careers</a>')
                if url == f"{base}/careers":
                    return Page(url=url, html="<title>Careers</title><h1>Join our team</h1>")
                if "sitemap" in url or url.endswith("/robots.txt"):
                    raise AssertionError("sitemap discovery should not run before the primary candidate")
                raise FetchError("missing")

        fetcher = PrimaryFetcher()
        career_url, trace = JobSourceAgent(fetcher).find_career_page(base)

        self.assertEqual(career_url, f"{base}/careers")
        self.assertTrue(trace["sitemap_discovery"]["skipped"])
        self.assertNotIn(f"{base}/sitemap.xml", fetcher.urls)

    def test_sitemap_ignores_static_assets_and_unrelated_external_urls(self):
        base = "https://example.com"

        class MappingFetcher:
            def __init__(self, pages):
                self.pages = pages

            def fetch(self, url, data=None, headers=None):
                if url not in self.pages:
                    raise FetchError("missing")
                return Page(url=url, html=self.pages[url])

        fetcher = MappingFetcher(
            {
                f"{base}/robots.txt": "",
                f"{base}/sitemap.xml": """
                    <urlset>
                      <url><loc>https://example.com/assets/careers-team.webp</loc></url>
                      <url><loc>https://outside.example/articles/careers-advice</loc></url>
                      <url><loc>https://careers.example.com/jobs</loc></url>
                    </urlset>
                """,
                f"{base}/sitemap_index.xml": "<urlset />",
            }
        )

        links, trace = JobSourceAgent(fetcher)._sitemap_candidates(base)

        self.assertEqual([link.url for link in links], ["https://careers.example.com/jobs"])
        self.assertEqual(trace["candidate_count"], 1)

    def test_career_page_can_be_discovered_from_sitemap(self):
        agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        career_url, trace = agent.find_career_page("https://sitemapco.example")

        self.assertEqual(career_url, "https://sitemapco.example/careers")
        self.assertTrue(
            trace["sitemap_discovery"].get("skipped")
            or trace["sitemap_discovery"]["candidate_count"] > 0
        )

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
