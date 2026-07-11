import unittest

from job_source_agent.career_search import CareerSearchResolver, build_search_queries
from job_source_agent.web import Fetcher


class CareerSearchTests(unittest.TestCase):
    def test_build_search_queries_include_site_and_provider_templates(self):
        queries = build_search_queries("Acme Co", "acme.example")

        self.assertIn("Acme Co careers jobs", queries)
        self.assertIn("site:acme.example careers", queries)
        self.assertIn("Acme Co greenhouse jobs", queries)
        self.assertIn("Acme Co workday jobs", queries)

    def test_search_trace_records_query_metadata(self):
        resolver = CareerSearchResolver(Fetcher(offline=True))

        result = resolver.search("Acme Co", "https://acme.example")

        self.assertEqual(len(result.trace["queries"]), 1)
        self.assertTrue(result.trace["queries"][0]["query_url"].startswith("https://www.bing.com/search?"))

    def test_search_stops_after_endpoint_fetch_error(self):
        resolver = CareerSearchResolver(Fetcher(offline=True))

        result = resolver.search("Acme Co", "https://acme.example")

        self.assertEqual(len(result.trace["queries"]), 1)
        self.assertEqual(result.trace["stopped_reason"], "search_endpoint_fetch_failed")


if __name__ == "__main__":
    unittest.main()
