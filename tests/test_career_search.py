from pathlib import Path
import unittest

from job_source_agent.career_search import (
    CareerSearchResolver,
    build_ats_search_queries,
    build_search_queries,
    clean_search_result_url,
)
from job_source_agent.web import FetchError, Fetcher, Page


FIXTURES = Path(__file__).parent / "fixtures" / "career_search"


class MappingFetcher(Fetcher):
    def __init__(self, handler):
        super().__init__(offline=True)
        self.handler = handler
        self.calls = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append(url)
        return self.handler(url)


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class CareerSearchTests(unittest.TestCase):
    def test_build_search_queries_prioritize_generic_and_site_queries(self):
        queries = build_search_queries("Acme Co", "acme.example")

        self.assertEqual(queries[:3], [
            "Acme Co careers jobs",
            "site:acme.example careers",
            "site:acme.example jobs",
        ])

    def test_ats_only_queries_and_results_exclude_first_party_career_page(self):
        self.assertTrue(build_ats_search_queries("Glean")[0].startswith("site:job-boards.greenhouse.io"))
        rss = """<rss><channel>
          <item><link>https://www.glean.com/careers</link></item>
          <item><link>https://job-boards.greenhouse.io/gleanwork/jobs/4006734005</link></item>
        </channel></rss>"""
        result = CareerSearchResolver(
            MappingFetcher(lambda url: Page(url, rss, final_url=url)),
            max_queries=1,
        ).search("Glean", "https://www.glean.com/careers", ats_only=True)

        self.assertEqual(
            [item.url for item in result.candidates],
            ["https://job-boards.greenhouse.io/gleanwork/jobs/4006734005"],
        )
        self.assertTrue(result.trace["ats_only"])

    def test_ats_only_search_gives_each_provider_query_a_bounded_rss_attempt(self):
        fetcher = MappingFetcher(
            lambda url: Page(url, "<rss><channel /></rss>", final_url=url)
        )

        CareerSearchResolver(fetcher, max_queries=5, max_source_fetches=6).search(
            "Zillow, Inc.",
            "https://zillow.com",
            ats_only=True,
        )

        self.assertEqual(len(fetcher.calls), 5)
        self.assertTrue(all("format=rss" in url for url in fetcher.calls))
        self.assertIn("site%3Amyworkdayjobs.com", fetcher.calls[1])
        self.assertNotIn("Inc", fetcher.calls[0])

    def test_bing_rss_filters_drift_and_accepts_official_result(self):
        def handler(url):
            if "format=rss" in url:
                return Page(url, fixture("bing_rss_mixed.xml"), final_url=url)
            raise AssertionError(url)

        result = CareerSearchResolver(MappingFetcher(handler), max_queries=1).search(
            "Acme Co", "https://acme.example"
        )

        self.assertEqual([item.url for item in result.candidates], ["https://acme.example/company/careers"])
        self.assertEqual(result.trace["queries"][0]["source"], "bing_rss")
        self.assertEqual(result.trace["queries"][0]["result_count"], 2)

    def test_bing_html_is_used_after_rss_fetch_error(self):
        def handler(url):
            if "format=rss" in url:
                raise FetchError("rss timed out")
            if "bing.com" in url:
                return Page(url, fixture("bing_results.html"), final_url=url)
            raise AssertionError(url)

        result = CareerSearchResolver(MappingFetcher(handler), max_queries=1).search(
            "Acme Co", "https://acme.example"
        )

        self.assertEqual([item.url for item in result.candidates], ["https://jobs.lever.co/acme-co"])
        self.assertEqual(len(result.trace["queries"]), 2)
        self.assertIn("timed out", result.trace["queries"][0]["error"])

    def test_duckduckgo_is_used_after_bing_sources_fail(self):
        def handler(url):
            if "bing.com" in url:
                raise FetchError("bing unavailable")
            if "duckduckgo.com" in url:
                return Page(url, fixture("duckduckgo_results.html"), final_url=url)
            raise AssertionError(url)

        result = CareerSearchResolver(MappingFetcher(handler), max_queries=1).search(
            "Acme Co", "https://acme.example"
        )

        self.assertEqual([item.url for item in result.candidates], ["https://acme.example/careers"])
        self.assertEqual(result.trace["queries"][-1]["source"], "duckduckgo_html")

    def test_challenge_page_yields_no_results(self):
        def handler(url):
            if "bing.com" in url:
                return Page(url, "<html></html>", final_url=url)
            if "duckduckgo.com" in url:
                return Page(url, fixture("duckduckgo_challenge.html"), final_url=url)

        result = CareerSearchResolver(MappingFetcher(handler), max_queries=1).search(
            "Acme Co", "https://acme.example"
        )

        self.assertEqual(result.candidates, [])

    def test_unrelated_external_career_result_is_rejected(self):
        html = '<html><h2><a href="https://unrelated.example/careers">Careers</a></h2></html>'

        def handler(url):
            if "format=rss" in url:
                return Page(url, "<rss><channel /></rss>", final_url=url)
            if "bing.com" in url:
                return Page(url, html, final_url=url)
            if "duckduckgo.com" in url:
                return Page(url, "<html></html>", final_url=url)
            raise FetchError("not found")

        result = CareerSearchResolver(MappingFetcher(handler), max_queries=1).search(
            "Acme Co", "https://acme.example"
        )

        self.assertEqual(result.candidates, [])

    def test_official_homepage_without_career_signal_is_rejected(self):
        rss = "<rss><channel><item><link>https://acme.example/</link></item></channel></rss>"

        def handler(url):
            if "format=rss" in url:
                return Page(url, rss, final_url=url)
            if "bing.com" in url or "duckduckgo.com" in url:
                return Page(url, "<html></html>", final_url=url)
            raise FetchError("not found")

        result = CareerSearchResolver(MappingFetcher(handler), max_queries=1).search(
            "Acme Co", "https://acme.example"
        )

        self.assertEqual(result.candidates, [])

    def test_brand_prefixed_official_career_path_is_accepted(self):
        rss = "<rss><channel><item><link>https://acme.example/real-careers</link></item></channel></rss>"

        def handler(url):
            if "format=rss" in url:
                return Page(url, rss, final_url=url)
            raise AssertionError(url)

        result = CareerSearchResolver(MappingFetcher(handler), max_queries=1).search(
            "Acme Co", "https://acme.example"
        )

        self.assertEqual(result.candidates[0].url, "https://acme.example/real-careers")

    def test_duplicate_urls_across_sources_are_normalized(self):
        rss = "<rss><channel><item><link>https://acme.example/careers/</link></item></channel></rss>"

        def handler(url):
            if "format=rss" in url:
                return Page(url, rss, final_url=url)
            raise AssertionError(url)

        result = CareerSearchResolver(MappingFetcher(handler), max_queries=1).search(
            "Acme Co", "https://acme.example"
        )

        self.assertEqual(len(result.candidates), 1)

    def test_ats_board_filter_queries_share_one_candidate_budget(self):
        rss = """<rss><channel>
          <item><link>https://jobs.lever.co/acme</link></item>
          <item><link>https://jobs.lever.co/acme?team=Engineering</link></item>
        </channel></rss>"""

        result = CareerSearchResolver(
            MappingFetcher(lambda url: Page(url, rss, final_url=url)),
            max_queries=1,
        ).search("Acme", "https://acme.example")

        self.assertEqual([item.url for item in result.candidates], ["https://jobs.lever.co/acme"])

    def test_parent_brand_ats_tenant_does_not_confirm_full_company_identity(self):
        rss = "<rss><channel><item><link>https://jobs.lever.co/google</link></item></channel></rss>"

        result = CareerSearchResolver(
            MappingFetcher(lambda url: Page(url, rss, final_url=url)),
            max_queries=1,
        ).search("Google DeepMind", "https://deepmind.google")

        self.assertEqual(result.candidates, [])

    def test_clean_search_result_decodes_bing_base64_and_duckduckgo_redirects(self):
        bing = "https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9hY21lLmV4YW1wbGUvY2FyZWVycw=="
        duck = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Facme.example%2Fjobs"

        self.assertEqual(clean_search_result_url(bing), "https://acme.example/careers")
        self.assertEqual(clean_search_result_url(duck), "https://acme.example/jobs")

    def test_clean_search_result_rejects_credentials_nonstandard_ports_and_malformed_urls(self):
        self.assertEqual(clean_search_result_url("https://user@example.com/careers"), "")
        self.assertEqual(clean_search_result_url("https://example.com:8443/careers"), "")
        self.assertEqual(clean_search_result_url("https://[invalid/careers"), "")

    def test_trace_records_each_failed_source_without_stopping_early(self):
        fetcher = MappingFetcher(lambda url: (_ for _ in ()).throw(FetchError("offline")))

        result = CareerSearchResolver(fetcher, max_queries=1).search(
            "Acme Co", "https://acme.example"
        )

        self.assertEqual(len(result.trace["queries"]), 3)
        self.assertEqual(result.trace["stopped_reason"], "no_valid_candidates")
        self.assertTrue(all(item["error"] == "offline" for item in result.trace["queries"]))

    def test_source_fetch_budget_bounds_multi_query_timeout_exposure(self):
        fetcher = MappingFetcher(lambda url: (_ for _ in ()).throw(FetchError("timed out")))

        result = CareerSearchResolver(
            fetcher,
            max_queries=5,
            max_source_fetches=4,
        ).search("Acme Co", "https://acme.example")

        self.assertEqual(len(fetcher.calls), 4)
        self.assertEqual(len(result.trace["queries"]), 4)
        self.assertTrue(result.trace["source_fetch_budget_exhausted"])


if __name__ == "__main__":
    unittest.main()
