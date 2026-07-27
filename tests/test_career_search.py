from pathlib import Path
import unittest

from job_source_agent.career_search import (
    CareerSearchResolver,
    build_ats_search_query_plan,
    build_ats_search_queries,
    build_search_queries,
    clean_search_result_url,
    search_site_openings,
)
from job_source_agent.career_transport_budget import CareerTransportBudgetFetcher
from job_source_agent.searxng_search_backend import SearxngSearchBackend
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
    def test_injected_search_backend_keeps_candidates_untrusted_and_trace_private(self):
        endpoint = "https://private-search.example/internal"
        backend = SearxngSearchBackend(endpoint)
        body = """{"results": [
          {
            "url": "https://jobs.lever.co/acme/role-1",
            "title": "Acme Engineer",
            "content": "Acme engineering role"
          },
          {
            "url": "https://evil.example/jobs/role-2",
            "title": "Other Engineer",
            "content": "Different company"
          }
        ]}"""
        fetcher = MappingFetcher(lambda url: Page(url, body, final_url=url))

        result = CareerSearchResolver(
            fetcher,
            max_queries=1,
            max_source_fetches=1,
            search_backend=backend,
        ).search(
            "Acme",
            "https://acme.example",
            target_title="Engineer",
            ats_only=True,
        )

        self.assertEqual(
            [candidate.url for candidate in result.candidates],
            ["https://jobs.lever.co/acme/role-1"],
        )
        self.assertEqual(len(fetcher.calls), 1)
        self.assertIn("format=json", fetcher.calls[0])
        self.assertEqual(result.trace["queries"][0]["source"], "searxng")
        self.assertNotIn(endpoint, repr(result.trace))
        self.assertNotIn("Acme", result.trace["queries"][0]["query_url"])

    def test_injected_search_backend_invalid_response_is_not_a_candidate(self):
        backend = SearxngSearchBackend("https://search.example")
        fetcher = MappingFetcher(
            lambda url: Page(url, "<html>not json</html>", final_url=url)
        )

        result = CareerSearchResolver(
            fetcher,
            max_queries=1,
            max_source_fetches=1,
            search_backend=backend,
        ).search("Acme", "https://acme.example", ats_only=True)

        self.assertEqual(result.candidates, [])
        self.assertEqual(
            result.trace["queries"][0]["response_disposition"],
            "invalid_response",
        )
        self.assertEqual(result.trace["queries"][0]["error"], "malformed_json")

    def test_injected_search_backend_redacts_fetch_error_url(self):
        endpoint = "https://private-search.example/internal"
        backend = SearxngSearchBackend(endpoint)

        def fail(url):
            raise FetchError(
                f"request failed: {url}",
                reason_code="NETWORK_TIMEOUT",
                retryable=True,
                transport_phase="timeout",
            )

        result = CareerSearchResolver(
            MappingFetcher(fail),
            max_queries=1,
            max_source_fetches=1,
            search_backend=backend,
        ).search("Secret Company", "https://company.example", ats_only=True)

        self.assertEqual(result.candidates, [])
        self.assertEqual(
            result.trace["queries"][0]["error"],
            {
                "reason_code": "NETWORK_TIMEOUT",
                "status": None,
                "retryable": True,
                "transport_phase": "timeout",
            },
        )
        self.assertNotIn(endpoint, repr(result.trace))
        self.assertNotIn("Secret Company", repr(result.trace["error"]))

    def test_site_opening_search_accepts_injected_backend_only_on_same_site(self):
        official = "https://careers.acme.example/"
        valid = "https://www.acme.example/jobs/platform-engineer"
        backend = SearxngSearchBackend("https://search.example")
        body = (
            '{"results": ['
            f'{{"url": "{valid}"}},'
            '{"url": "https://evil.example/jobs/platform-engineer"}'
            "]}"
        )
        fetcher = MappingFetcher(lambda url: Page(url, body, final_url=url))

        result = search_site_openings(
            fetcher,
            official,
            "Platform Engineer",
            search_backend=backend,
        )

        self.assertEqual([candidate.url for candidate in result.candidates], [valid])
        self.assertNotIn("search.example", repr(result.trace))

    def test_unbound_career_lead_is_admitted_only_for_current_page_verification(self):
        rss = (
            "<rss><channel><item>"
            "<title>Careers | Redlands Community Hospital</title>"
            "<description>Jobs at Redlands Community Hospital</description>"
            "<link>https://www.redlandshospital.org/careers</link>"
            "</item></channel></rss>"
        )
        fetcher = MappingFetcher(lambda url: Page(url, rss, final_url=url))
        resolver = CareerSearchResolver(fetcher, max_queries=1, max_source_fetches=1)

        strict = resolver.search("Redlands Community Hospital", "")
        unbound = resolver.search(
            "Redlands Community Hospital",
            "",
            allow_unbound_career=True,
        )

        self.assertEqual(strict.candidates, [])
        self.assertEqual(
            [item.url for item in unbound.candidates],
            ["https://www.redlandshospital.org/careers"],
        )

    def test_site_opening_search_keeps_only_same_site_job_leads(self):
        official = "https://jobs.acme.example/"
        valid = "https://jobs.acme.example/job-3/123/platform-engineer/"
        rss = (
            "<rss><channel>"
            f"<item><link>{valid}</link></item>"
            "<item><link>https://evil.example/jobs/platform-engineer</link></item>"
            "<item><link>https://jobs.acme.example/about</link></item>"
            "</channel></rss>"
        )
        fetcher = MappingFetcher(lambda url: Page(url, rss))

        result = search_site_openings(fetcher, official, "Platform Engineer")

        self.assertEqual([candidate.url for candidate in result.candidates], [valid])
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual(result.trace["stopped_reason"], "query_plan_complete")

    def test_site_opening_search_accepts_verified_sibling_subdomain_lead(self):
        official = "https://careers.acme.example/"
        valid = "https://www.acme.example/talent/job-offers/platform-engineer/"
        rss = (
            "<rss><channel>"
            f"<item><link>{valid}</link></item>"
            "<item><link>https://www.other.example/jobs/platform-engineer</link></item>"
            "</channel></rss>"
        )
        fetcher = MappingFetcher(lambda url: Page(url, rss))

        result = search_site_openings(fetcher, official, "Platform Engineer")

        self.assertEqual([candidate.url for candidate in result.candidates], [valid])
        self.assertIn('site:acme.example', result.trace["query"])

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

    def test_title_targeted_queries_are_provider_bounded_without_or_bundle(self):
        queries = build_ats_search_queries(
            "Texas Children's Hospital",
            "RN - LDRP",
        )

        self.assertEqual(
            queries[:3],
            [
                '"texas children hospital" "RN - LDRP" jobs',
                'site:job-boards.greenhouse.io "texas children hospital" "RN - LDRP"',
                'site:jobs.lever.co "texas children hospital" "RN - LDRP"',
            ],
        )
        self.assertTrue(all(" OR " not in query for query in queries))

    def test_fixed_budget_plan_rotates_provider_families_from_normalized_identity(self):
        first = build_ats_search_query_plan(
            "Acme, Inc.", "Data Analyst", max_queries=5
        )
        equivalent = build_ats_search_query_plan(
            "Acme", "data analyst", max_queries=5
        )

        self.assertEqual(
            [item.provider_family for item in first],
            [item.provider_family for item in equivalent],
        )
        self.assertEqual(len(first), 5)
        self.assertIsNone(first[0].provider_family)
        self.assertEqual(len({item.provider_family for item in first[1:]}), 4)
        self.assertFalse(any("site:boards.greenhouse.io" in item.query for item in first))

    def test_rotation_covers_every_provider_family_across_a_deterministic_identity_cycle(self):
        seen = set()
        for index in range(100):
            plan = build_ats_search_query_plan(
                f"Example Company Provider{index}Alpha", "Data Analyst", max_queries=2
            )
            seen.add(plan[1].provider_family)

        self.assertEqual(
            seen,
            {
                "greenhouse",
                "lever",
                "ashby",
                "workable",
                "pinpoint",
                "smartrecruiters",
                "workday",
                "oracle",
                "eightfold",
            },
        )

    def test_title_targeted_search_keeps_opaque_ats_url_as_untrusted_lead(self):
        opaque = (
            "https://eohh.fa.us2.oraclecloud.com/hcmUI/"
            "CandidateExperience/en/sites/CX/job/425798"
        )
        rss = f"<rss><channel><item><link>{opaque}</link></item></channel></rss>"

        result = CareerSearchResolver(
            MappingFetcher(lambda url: Page(url, rss, final_url=url)),
            max_queries=1,
        ).search(
            "Texas Children's Hospital",
            "",
            target_title="Registered Nurse LDRP",
            ats_only=True,
        )

        self.assertEqual([item.url for item in result.candidates], [opaque])

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
        self.assertEqual(
            [entry["provider_family"] for entry in result.trace["ats_query_plan"]],
            [None, "eightfold", "greenhouse", "lever", "ashby"],
        )
        self.assertNotIn("Inc", fetcher.calls[0])
        self.assertFalse(result.trace["fetch_budget_supported"])
        self.assertEqual(result.trace["fetch_budget_checks"], 0)

    def test_ats_exhaustive_mode_runs_every_scheduled_query_after_an_early_lead(self):
        lead = "https://jobs.lever.co/acme"

        def handler(url):
            body = (
                f"<rss><channel><item><link>{lead}</link></item></channel></rss>"
                if 'q=%22acme%22' in url
                else "<rss><channel /></rss>"
            )
            return Page(url, body, final_url=url)

        fetcher = MappingFetcher(handler)
        result = CareerSearchResolver(
            fetcher, max_queries=5, max_source_fetches=5
        ).search(
            "Acme",
            "https://acme.example",
            target_title="Engineer",
            ats_only=True,
            exhaustive=True,
            query_diversity_first=True,
        )

        self.assertEqual(len(fetcher.calls), 5)
        self.assertEqual(len(result.trace["ats_query_plan"]), 5)
        self.assertEqual(result.trace["stopped_reason"], "query_plan_complete")
        self.assertEqual([item.url for item in result.candidates], [lead])

    def test_ats_bucket_selection_prevents_one_query_from_monopolizing_candidate_limit(self):
        general_leads = (
            "https://jobs.lever.co/acme/first",
            "https://jobs.lever.co/acme/second",
        )
        provider_lead = "https://jobs.ashbyhq.com/acme/third"

        def handler(url):
            if 'q=%22acme%22+%22Engineer%22+jobs' in url:
                links = general_leads
            else:
                links = (provider_lead,)
            body = "<rss><channel>" + "".join(
                f"<item><link>{link}</link></item>" for link in links
            ) + "</channel></rss>"
            return Page(url, body, final_url=url)

        result = CareerSearchResolver(
            MappingFetcher(handler), max_results=3, max_queries=3, max_source_fetches=3
        ).search(
            "Acme",
            "https://acme.example",
            target_title="Engineer",
            ats_only=True,
            exhaustive=True,
            query_diversity_first=True,
        )

        self.assertEqual(len(result.candidates), 3)
        self.assertEqual(result.trace["candidate_selection"], "bucket_round_robin")
        self.assertEqual(result.trace["candidate_bucket_counts"], [2, 1, 0])
        self.assertEqual(
            [item.url for item in result.candidates],
            [general_leads[0], provider_lead, general_leads[1]],
        )

    def test_ats_diversity_rescues_empty_rss_bucket_with_secondary_ats_lead(self):
        irrelevant_rss = """<rss><channel>
          <item><link>https://unrelated.example/careers</link></item>
        </channel></rss>"""
        secondary = """<html><body>
          <a class="result__a" href="https://jobs.lever.co/acme">Acme jobs</a>
        </body></html>"""

        def handler(url):
            body = irrelevant_rss if "format=rss" in url else secondary
            return Page(url, body, final_url=url)

        fetcher = MappingFetcher(handler)
        result = CareerSearchResolver(
            fetcher, max_queries=2, max_source_fetches=3
        ).search(
            "Acme",
            "https://acme.example",
            ats_only=True,
            exhaustive=True,
            query_diversity_first=True,
        )

        self.assertEqual([item.url for item in result.candidates], ["https://jobs.lever.co/acme"])
        self.assertEqual(
            [item["source"] for item in result.trace["queries"]],
            ["bing_rss", "bing_rss", "duckduckgo_html"],
        )
        self.assertEqual(result.trace["candidate_bucket_counts"], [1, 0])
        self.assertEqual(
            result.trace["ats_secondary_rescue"],
            {
                "attempt_count": 1,
                "rejection_count": 0,
                "attempts": [
                    {
                        "bucket_index": 0,
                        "source": "duckduckgo_html",
                        "result_count": 1,
                        "accepted_count": 1,
                        "rejection_count": 0,
                    }
                ],
            },
        )

    def test_ats_diversity_secondary_rescue_keeps_irrelevant_results_rejected(self):
        irrelevant_rss = """<rss><channel>
          <item><link>https://unrelated.example/careers</link></item>
        </channel></rss>"""
        secondary = """<html><body>
          <a class="result__a" href="https://jobs.lever.co/other-company">Other jobs</a>
        </body></html>"""

        def handler(url):
            body = irrelevant_rss if "format=rss" in url else secondary
            return Page(url, body, final_url=url)

        result = CareerSearchResolver(
            MappingFetcher(handler), max_queries=1, max_source_fetches=2
        ).search(
            "Acme",
            "https://acme.example",
            ats_only=True,
            query_diversity_first=True,
        )

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["ats_secondary_rescue"]["attempt_count"], 1)
        self.assertEqual(result.trace["ats_secondary_rescue"]["rejection_count"], 1)
        self.assertEqual(result.trace["queries"][-1]["rejection_count"], 1)
        self.assertNotIn("url", result.trace["ats_secondary_rescue"]["attempts"][0])

    def test_ats_diversity_secondary_rescue_honors_source_cap_and_deadline(self):
        empty_rss = "<rss><channel /></rss>"
        irrelevant_secondary = """<html><body>
          <a class="result__a" href="https://jobs.lever.co/other-company">Other jobs</a>
        </body></html>"""

        def handler(url):
            body = empty_rss if "format=rss" in url else irrelevant_secondary
            return Page(url, body, final_url=url)

        capped = CareerSearchResolver(
            MappingFetcher(handler), max_queries=2, max_source_fetches=3
        ).search(
            "Acme",
            "https://acme.example",
            ats_only=True,
            exhaustive=True,
            query_diversity_first=True,
        )
        self.assertEqual(len(capped.trace["queries"]), 3)
        self.assertEqual(capped.trace["ats_secondary_rescue"]["attempt_count"], 1)
        self.assertTrue(capped.trace["source_fetch_budget_exhausted"])

        budget_fetcher = BudgetMappingFetcher(handler, [1.0, 1.0, 0.0])
        deadline = CareerSearchResolver(
            budget_fetcher, max_queries=2, max_source_fetches=4
        ).search(
            "Acme",
            "https://acme.example",
            ats_only=True,
            exhaustive=True,
            query_diversity_first=True,
        )
        self.assertEqual(len(budget_fetcher.calls), 2)
        self.assertEqual(budget_fetcher.budget_checks, 3)
        self.assertEqual(deadline.trace["ats_secondary_rescue"]["attempt_count"], 0)
        self.assertEqual(deadline.trace["stopped_reason"], "deadline_exhausted")

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

    def test_branded_cross_domain_career_microsite_is_an_unverified_lead(self):
        microsite = "https://acmelabsjobs.com/open-roles"
        rss = f"""<rss><channel><item>
          <title>Careers and jobs at Acme Labs</title>
          <link>{microsite}</link>
          <description>Join our team and explore current openings.</description>
        </item></channel></rss>"""

        result = CareerSearchResolver(
            MappingFetcher(lambda url: Page(url, rss, final_url=url)),
            max_queries=1,
        ).search("Acme Labs", "https://acme.example")

        self.assertEqual([item.url for item in result.candidates], [microsite])
        self.assertIn(
            "unverified branded career microsite search lead",
            result.candidates[0].reasons,
        )

    def test_branded_microsite_can_use_html_search_snippet_hiring_semantics(self):
        microsite = "https://acmelabsjobs.com/"
        html = f"""<html><body><li class="b_algo">
          <h2><a href="{microsite}">Acme Labs</a></h2>
          <p>Search open positions and join our team.</p>
        </li></body></html>"""

        def handler(url):
            if "format=rss" in url:
                raise FetchError("rss unavailable")
            return Page(url, html, final_url=url)

        result = CareerSearchResolver(
            MappingFetcher(handler),
            max_queries=1,
        ).search("Acme Labs", "https://acme.example")

        self.assertEqual([item.url for item in result.candidates], [microsite])

    def test_cross_domain_microsite_rejects_brand_and_semantic_false_positives(self):
        cases = {
            "unbranded third party": (
                "Acme Labs",
                "https://career-pages.example/acme-labs/jobs",
                "Acme Labs careers and current jobs",
            ),
            "same-name partial brand": (
                "Acme Labs",
                "https://acmejobs.com/",
                "Acme careers and current jobs",
            ),
            "aggregator brand path": (
                "Acme Labs",
                "https://jobcatalog.example/acmelabs/jobs",
                "Acme Labs jobs and openings",
            ),
            "no hiring semantics": (
                "Acme Labs",
                "https://acmelabsjobs.com/",
                "Acme Labs products and company news",
            ),
        }

        for label, (company_name, url, title) in cases.items():
            with self.subTest(label=label):
                rss = (
                    "<rss><channel><item>"
                    f"<title>{title}</title><link>{url}</link>"
                    "</item></channel></rss>"
                )
                result = CareerSearchResolver(
                    MappingFetcher(
                        lambda search_url: Page(search_url, rss, final_url=search_url)
                    ),
                    max_queries=1,
                ).search(company_name, "https://acme.example")

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
