import unittest

from job_source_agent.career_search import CareerSearchResolver, CareerSearchResult
from job_source_agent.models import LinkCandidate
from job_source_agent.provider_candidates import CandidateDiscoveryRequest
from job_source_agent.provider_search_discovery import ProviderSearchCandidateDiscovery
from job_source_agent.web import Fetcher, Page


class MappingFetcher(Fetcher):
    def __init__(self, body):
        super().__init__(offline=True)
        self.body = body

    def fetch(self, url, data=None, headers=None):
        return Page(url, self.body, final_url=url)


class StaticResolver:
    def __init__(self, candidates, trace):
        self.candidates = candidates
        self.trace = trace
        self.calls = []

    def search(
        self,
        company_name,
        company_website_url,
        *,
        target_title=None,
        ats_only=False,
        exhaustive=False,
    ):
        self.calls.append(
            (company_name, company_website_url, target_title, ats_only, exhaustive)
        )
        return CareerSearchResult(self.candidates, self.trace)


class ProviderSearchCandidateDiscoveryTests(unittest.TestCase):
    def test_emits_multiple_ats_results_with_search_provenance_and_hints(self):
        rss = """<rss><channel>
          <item><link>https://jobs.lever.co/acme</link></item>
          <item><link>https://job-boards.greenhouse.io/acme/jobs/123</link></item>
          <item><link>https://jobs.ashbyhq.com/acme</link></item>
        </channel></rss>"""
        discovery = ProviderSearchCandidateDiscovery(
            CareerSearchResolver(MappingFetcher(rss), max_queries=1)
        )

        result = discovery.discover(
            CandidateDiscoveryRequest(
                company_name="Acme",
                company_website_url="https://acme.example",
                target_title="Software Engineer",
                target_location="Remote",
            )
        )

        self.assertEqual(len(result.candidates), 3)
        self.assertEqual(
            {candidate.provider_hint for candidate in result.candidates},
            {"ashby", "greenhouse", "lever"},
        )
        self.assertEqual(
            sorted(candidate.result_rank for candidate in result.candidates),
            [1, 2, 3],
        )
        self.assertTrue(
            all(
                candidate.query == '"acme" "Software Engineer" jobs'
                for candidate in result.candidates
            )
        )
        self.assertEqual(
            [candidate.source_kind for candidate in result.candidates].count(
                "targeted_opening_search"
            ),
            1,
        )
        self.assertTrue(all(candidate.source_url.startswith("https://www.bing.com/search?") for candidate in result.candidates))
        self.assertFalse(any(hasattr(candidate, "verified") for candidate in result.candidates))

    def test_skips_malformed_and_private_urls_without_treating_search_as_verification(self):
        source_url = "https://www.bing.com/search?q=acme"
        links = [
            LinkCandidate("https://127.0.0.1/jobs", "", source_url),
            LinkCandidate("not a url", "", source_url),
            LinkCandidate("https://jobs.lever.co/acme", "", source_url),
        ]
        resolver = StaticResolver(
            links,
            {
                "queries": [
                    {
                        "query": 'site:jobs.lever.co "acme" jobs',
                        "candidates": [{"url": item.url} for item in links],
                    }
                ]
            },
        )

        result = ProviderSearchCandidateDiscovery(resolver).discover(
            CandidateDiscoveryRequest(company_name="Acme")
        )

        self.assertEqual([candidate.url for candidate in result.candidates], ["https://jobs.lever.co/acme"])
        self.assertEqual(result.trace["skipped_candidate_count"], 2)
        self.assertEqual(resolver.calls, [("Acme", "", None, True, True)])

    def test_no_search_results_produces_an_empty_candidate_set(self):
        discovery = ProviderSearchCandidateDiscovery(
            CareerSearchResolver(MappingFetcher("<rss><channel /></rss>"), max_queries=1)
        )

        result = discovery.discover(CandidateDiscoveryRequest(company_name="Acme"))

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.trace["candidate_count"], 0)
        self.assertEqual(result.trace["skipped_candidate_count"], 0)

    def test_output_is_deterministic_and_respects_the_candidate_bound(self):
        source_url = "https://www.bing.com/search?q=acme"
        links = [
            LinkCandidate("https://jobs.lever.co/acme", "", source_url),
            LinkCandidate("https://jobs.ashbyhq.com/acme", "", source_url),
            LinkCandidate("https://job-boards.greenhouse.io/acme", "", source_url),
        ]
        trace = {
            "queries": [
                {
                    "query": '"acme" careers jobs',
                    "candidates": [{"url": item.url} for item in links],
                }
            ]
        }
        discovery = ProviderSearchCandidateDiscovery(
            StaticResolver(links, trace), max_candidates=2
        )

        first = discovery.discover(CandidateDiscoveryRequest(company_name="Acme"))
        second = discovery.discover(CandidateDiscoveryRequest(company_name="Acme"))

        self.assertEqual(first, second)
        self.assertEqual(len(first.candidates), 2)
        self.assertTrue(first.trace["truncated"])
        self.assertEqual([candidate.result_rank for candidate in first.candidates], [1, 2])

    def test_exact_provider_result_is_an_untrusted_opening_candidate(self):
        source_url = "https://www.bing.com/search?q=acme"
        exact = LinkCandidate(
            "https://acme.wd1.myworkdayjobs.com/jobs/job/Austin/Engineer_R123",
            "",
            source_url,
        )
        resolver = StaticResolver(
            [exact],
            {
                "queries": [
                    {
                        "query": '"acme" "Engineer" jobs',
                        "candidates": [{"url": exact.url}],
                    }
                ]
            },
        )

        result = ProviderSearchCandidateDiscovery(resolver).discover(
            CandidateDiscoveryRequest(company_name="Acme", target_title="Engineer")
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].source_kind, "targeted_opening_search")
        self.assertFalse(hasattr(result.candidates[0], "verified"))


if __name__ == "__main__":
    unittest.main()
