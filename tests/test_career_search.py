from pathlib import Path
import unittest

from job_source_agent.career_search import (
    CareerSearchResolver,
    build_ats_search_queries,
    build_search_queries,
    clean_search_result_url,
)
from job_source_agent.career_transport_budget import CareerTransportBudgetFetcher
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


class BudgetMappingFetcher(MappingFetcher):
    def __init__(self, handler, remaining):
        super().__init__(handler)
        self.remaining = iter(remaining)
        self.budget_checks = 0

    def remaining_fetch_seconds(self):
        self.budget_checks += 1
        value = next(self.remaining)
        if isinstance(value, Exception):
            raise value
        return value


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
        self.assertEqual(build_ats_search_queries("Glean")[0], '"glean" careers jobs')
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

        result = CareerSearchResolver(fetcher, max_queries=5, max_source_fetches=6).search(
            "Zillow, Inc.",
            "https://zillow.com",
            ats_only=True,
        )

        self.assertEqual(len(fetcher.calls), 5)
        self.assertTrue(all("format=rss" in url for url in fetcher.calls))
        self.assertIn("site%3Ajob-boards.greenhouse.io", fetcher.calls[1])
        self.assertIn("site%3Amyworkdayjobs.com", fetcher.calls[2])
        self.assertNotIn("Inc", fetcher.calls[0])
        self.assertFalse(result.trace["fetch_budget_supported"])
        self.assertEqual(result.trace["fetch_budget_checks"], 0)

    def test_ats_only_invalid_rss_uses_secondary_candidate(self):
        rss = """<rss><channel>
          <item><link>https://unrelated.example/careers</link></item>
        </channel></rss>"""
        secondary = """<html><body>
          <a class="result__a" href="https://jobs.lever.co/acme">Acme jobs</a>
        </body></html>"""

        def handler(url):
            body = rss if "format=rss" in url else secondary
            return Page(url, body, final_url=url)

        fetcher = MappingFetcher(handler)
        result = CareerSearchResolver(
            fetcher,
            max_queries=1,
            max_source_fetches=2,
        ).search("Acme", "https://acme.example", ats_only=True)

        self.assertEqual([item.url for item in result.candidates], ["https://jobs.lever.co/acme"])
        self.assertEqual(len(fetcher.calls), 2)
        self.assertEqual(len(set(fetcher.calls)), 2)
        self.assertEqual(
            [item["source"] for item in result.trace["queries"]],
            ["bing_rss", "duckduckgo_html"],
        )
        self.assertEqual(result.trace["queries"][0]["result_count"], 1)
        self.assertEqual(result.trace["queries"][0]["candidates"], [])
        self.assertEqual(
            result.trace["queries"][0]["skipped_sources"],
            [
                {
                    "source": "bing_html",
                    "reason": "rss_returned_results_without_valid_candidate",
                }
            ],
        )
        self.assertEqual(result.trace["stopped_reason"], "search_candidate_found")
        self.assertFalse(result.trace["source_fetch_budget_exhausted"])

    def test_ats_only_invalid_rss_and_secondary_report_no_valid_candidates(self):
        rss = """<rss><channel>
          <item><link>https://unrelated.example/careers</link></item>
        </channel></rss>"""
        secondary = """<html><body>
          <a class="result__a" href="https://jobs.lever.co/other-company">Other jobs</a>
        </body></html>"""

        def handler(url):
            body = rss if "format=rss" in url else secondary
            return Page(url, body, final_url=url)

        fetcher = MappingFetcher(handler)
        result = CareerSearchResolver(
            fetcher,
            max_queries=1,
            max_source_fetches=2,
        ).search("Acme", "https://acme.example", ats_only=True)

        self.assertEqual(result.candidates, [])
        self.assertEqual(len(fetcher.calls), 2)
        self.assertEqual(len(set(fetcher.calls)), 2)
        self.assertEqual(
            [item["source"] for item in result.trace["queries"]],
            ["bing_rss", "duckduckgo_html"],
        )
        self.assertTrue(all(item["result_count"] == 1 for item in result.trace["queries"]))
        self.assertTrue(all(item["candidates"] == [] for item in result.trace["queries"]))
        self.assertEqual(result.trace["stopped_reason"], "no_valid_candidates")
        self.assertFalse(result.trace["source_fetch_budget_exhausted"])

    def test_fetch_budget_exhaustion_stops_ats_fanout_before_next_fetch(self):
        fetcher = BudgetMappingFetcher(
            lambda url: Page(url, "<rss><channel /></rss>", final_url=url),
            [1.0, 0.0],
        )

        result = CareerSearchResolver(fetcher, max_queries=5).search(
            "Zillow, Inc.",
            "https://zillow.com",
            ats_only=True,
        )

        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual(fetcher.budget_checks, 2)
        self.assertEqual(result.trace["stopped_reason"], "deadline_exhausted")
        self.assertTrue(result.trace["fetch_budget_supported"])
        self.assertEqual(result.trace["fetch_budget_checks"], 2)
        self.assertTrue(result.trace["fetch_budget_unavailable"])
        self.assertFalse(result.trace["fetch_budget_invalid"])
        self.assertFalse(any("remaining" in key for key in result.trace))

    def test_invalid_fetch_budget_stops_before_any_source_fetch(self):
        invalid_values = [
            True,
            "1",
            float("nan"),
            float("inf"),
            -1.0,
            RuntimeError("bad budget"),
        ]

        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                fetcher = BudgetMappingFetcher(
                    lambda url: Page(url, "<rss><channel /></rss>", final_url=url),
                    [invalid],
                )

                result = CareerSearchResolver(fetcher, max_queries=5).search(
                    "Acme Co", "https://acme.example", ats_only=True
                )

                self.assertEqual(fetcher.calls, [])
                self.assertEqual(result.trace["stopped_reason"], "deadline_exhausted")
                self.assertEqual(result.trace["fetch_budget_checks"], 1)
                self.assertTrue(result.trace["fetch_budget_unavailable"])
                self.assertTrue(result.trace["fetch_budget_invalid"])

    def test_bing_rss_filters_drift_and_accepts_official_result(self):
        def handler(url):
            if "format=rss" in url:
                return Page(url, fixture("bing_rss_mixed.xml"), final_url=url)
            raise AssertionError(url)

        fetcher = MappingFetcher(handler)
        result = CareerSearchResolver(fetcher, max_queries=1).search(
            "Acme Co", "https://acme.example"
        )

        self.assertEqual([item.url for item in result.candidates], ["https://acme.example/company/careers"])
        self.assertEqual(len(fetcher.calls), 1)
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

    def test_nonempty_rss_drift_falls_back_to_bing_html_for_same_query(self):
        rss = "<rss><channel><item><link>https://unrelated.example/careers</link></item></channel></rss>"

        def handler(url):
            if "format=rss" in url:
                return Page(url, rss, final_url=url)
            if "bing.com" in url:
                html = '<html><h2><a href="https://acme.example/careers">Careers</a></h2></html>'
                return Page(url, html, final_url=url)
            raise AssertionError(url)

        fetcher = MappingFetcher(handler)

        result = CareerSearchResolver(fetcher, max_queries=1).search(
            "Acme Co", "https://acme.example"
        )

        self.assertEqual(
            [item.url for item in result.candidates],
            ["https://acme.example/careers"],
        )
        self.assertEqual(len(fetcher.calls), 2)
        self.assertTrue(any("bing.com" in url and "format=rss" not in url for url in fetcher.calls))
        self.assertEqual(
            [item["source"] for item in result.trace["queries"]],
            ["bing_rss", "bing_html"],
        )
        self.assertEqual(
            result.trace["queries"][0]["query"],
            result.trace["queries"][1]["query"],
        )

    def test_nonempty_rss_drift_respects_transport_deadline_before_html(self):
        rss = "<rss><channel><item><link>https://unrelated.example/careers</link></item></channel></rss>"
        fetcher = BudgetMappingFetcher(
            lambda url: Page(url, rss, final_url=url),
            [1.0, 0.0],
        )

        result = CareerSearchResolver(fetcher, max_queries=1).search(
            "Acme Co", "https://acme.example"
        )

        self.assertEqual(len(fetcher.calls), 1)
        self.assertIn("format=rss", fetcher.calls[0])
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["stopped_reason"], "deadline_exhausted")
        self.assertEqual(result.trace["fetch_budget_checks"], 2)

    def test_nonempty_rss_drift_and_empty_duckduckgo_stay_bounded(self):
        rss = "<rss><channel><item><link>https://unrelated.example/careers</link></item></channel></rss>"

        def handler(url):
            body = rss if "format=rss" in url else "<html></html>"
            return Page(url, body, final_url=url)

        fetcher = MappingFetcher(handler)
        result = CareerSearchResolver(
            fetcher,
            max_queries=3,
            max_source_fetches=3,
        ).search("Acme Co", "https://acme.example")

        self.assertEqual(result.candidates, [])
        self.assertEqual(len(fetcher.calls), 3)
        self.assertTrue(result.trace["source_fetch_budget_exhausted"])
        self.assertTrue(any("bing.com" in url and "format=rss" not in url for url in fetcher.calls))

    def test_generic_search_caps_redundant_queries_but_ats_search_keeps_provider_sweep(self):
        fetcher = MappingFetcher(
            lambda url: Page(
                url,
                "<rss><channel><item><link>https://unrelated.example/</link></item></channel></rss>",
                final_url=url,
            )
        )

        generic = CareerSearchResolver(fetcher, max_queries=5, max_source_fetches=6).search(
            "Acme Co", "https://acme.example"
        )

        self.assertEqual(len(generic.trace["queries"]), 6)
        self.assertEqual(generic.trace["effective_query_limit"], 3)
        self.assertEqual(sum("format=rss" in url for url in fetcher.calls), 2)
        self.assertEqual(
            sum("bing.com" in url and "format=rss" not in url for url in fetcher.calls),
            2,
        )
        self.assertEqual(sum("duckduckgo.com" in url for url in fetcher.calls), 2)

        fetcher.calls.clear()
        ats = CareerSearchResolver(fetcher, max_queries=5, max_source_fetches=6).search(
            "Acme Co", "https://acme.example", ats_only=True
        )

        self.assertEqual(len(ats.trace["queries"]), 6)
        self.assertEqual(ats.trace["effective_query_limit"], 5)
        self.assertTrue(ats.trace["source_fetch_budget_exhausted"])
        self.assertEqual(sum("format=rss" in url for url in fetcher.calls), 3)
        self.assertEqual(sum("duckduckgo.com" in url for url in fetcher.calls), 3)

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

    def test_non_retryable_error_disables_source_without_spending_later_budgets(self):
        def handler(url):
            if "format=rss" in url:
                raise FetchError(
                    "request rejected",
                    reason_code="HTTP_FORBIDDEN",
                    retryable=False,
                )
            return Page(url, "<html></html>", final_url=url)

        fetcher = BudgetMappingFetcher(handler, [1.0] * 5)
        result = CareerSearchResolver(
            fetcher,
            max_queries=2,
            max_source_fetches=5,
        ).search("Acme Co", "https://acme.example")

        self.assertEqual(len(fetcher.calls), 5)
        self.assertEqual(fetcher.budget_checks, 5)
        self.assertEqual(sum("format=rss" in url for url in fetcher.calls), 1)
        self.assertEqual(len(result.trace["queries"]), 5)
        self.assertFalse(result.trace["source_fetch_budget_exhausted"])
        self.assertEqual(
            result.trace["source_circuit_breaks"],
            [{"source": "bing_rss", "reason": "non_retryable_fetch_error"}],
        )
        self.assertEqual(
            result.trace["source_circuit_skips"],
            [{"source": "bing_rss", "reason": "non_retryable_fetch_error"}],
        )

    def test_retryable_and_untyped_fetch_errors_do_not_disable_source(self):
        for retryable in (True, None):
            with self.subTest(retryable=retryable):
                def handler(url):
                    if "format=rss" in url:
                        raise FetchError("search unavailable", retryable=retryable)
                    return Page(url, "<html></html>", final_url=url)

                fetcher = MappingFetcher(handler)
                result = CareerSearchResolver(
                    fetcher,
                    max_queries=2,
                    max_source_fetches=6,
                ).search("Acme Co", "https://acme.example")

                self.assertEqual(len(fetcher.calls), 6)
                self.assertEqual(sum("format=rss" in url for url in fetcher.calls), 2)
                self.assertEqual(result.trace["source_circuit_breaks"], [])
                self.assertEqual(result.trace["source_circuit_skips"], [])

    def test_non_retryable_disable_is_isolated_to_the_failed_source(self):
        def handler(url):
            if "bing.com" in url and "format=rss" not in url:
                raise FetchError("blocked", retryable=False)
            return Page(url, "<html></html>", final_url=url)

        fetcher = MappingFetcher(handler)
        result = CareerSearchResolver(
            fetcher,
            max_queries=2,
            max_source_fetches=6,
        ).search("Acme Co", "https://acme.example")

        self.assertEqual(len(fetcher.calls), 5)
        self.assertEqual(sum("format=rss" in url for url in fetcher.calls), 2)
        self.assertEqual(sum("duckduckgo.com" in url for url in fetcher.calls), 2)
        self.assertEqual(
            result.trace["source_circuit_breaks"],
            [{"source": "bing_html", "reason": "non_retryable_fetch_error"}],
        )

    def test_source_circuit_resets_for_each_search_invocation(self):
        def handler(url):
            if "format=rss" in url:
                raise FetchError("request rejected", retryable=False)
            return Page(url, "<html></html>", final_url=url)

        fetcher = MappingFetcher(handler)
        resolver = CareerSearchResolver(fetcher, max_queries=2, max_source_fetches=6)

        first = resolver.search("Acme Co", "https://acme.example")
        first_call_count = len(fetcher.calls)
        second = resolver.search("Beta Co", "https://beta.example")

        self.assertEqual(first_call_count, 5)
        self.assertEqual(len(fetcher.calls), 10)
        self.assertEqual(sum("format=rss" in url for url in fetcher.calls), 2)
        self.assertEqual(len(first.trace["source_circuit_breaks"]), 1)
        self.assertEqual(len(second.trace["source_circuit_breaks"]), 1)

    def test_circuit_skips_do_not_consume_transport_dispatch_budget(self):
        def handler(url):
            if "format=rss" in url:
                raise FetchError("request rejected", retryable=False)
            return Page(url, "<html></html>", final_url=url)

        base = MappingFetcher(handler)
        fetcher = CareerTransportBudgetFetcher(base)
        with fetcher.career_discovery_scope(5) as budget:
            with fetcher.career_discovery_phase("career_search"):
                result = CareerSearchResolver(
                    fetcher,
                    max_queries=2,
                    max_source_fetches=5,
                ).search("Acme Co", "https://acme.example")
            budget_trace = budget.snapshot()

        self.assertEqual(len(base.calls), 5)
        self.assertEqual(budget_trace["dispatched"], 5)
        self.assertEqual(budget_trace["remaining"], 0)
        self.assertEqual(budget_trace["rejected"], 0)
        self.assertEqual(budget_trace["by_phase"], {"career_search": 5})
        self.assertEqual(len(result.trace["source_circuit_skips"]), 1)


if __name__ == "__main__":
    unittest.main()
