import unittest

from job_source_agent.career_search import CareerSearchResolver, CareerSearchResult
from job_source_agent.models import LinkCandidate
from job_source_agent.provider_candidates import CandidateDiscoveryRequest
from job_source_agent.provider_search_discovery import ProviderSearchCandidateDiscovery
from job_source_agent.providers import (
    AdapterResult,
    JobBoard,
    JobCandidate,
    ProviderRegistry,
)
from job_source_agent.web import FetchError, Fetcher, Page


class MappingFetcher(Fetcher):
    def __init__(self, body):
        super().__init__(offline=True)
        self.body = body

    def fetch(self, url, data=None, headers=None):
        return Page(url, self.body, final_url=url)


class SearchAndGreenhouseFetcher(Fetcher):
    def __init__(self):
        super().__init__(offline=True)

    def fetch(self, url, data=None, headers=None):
        if "boards-api.greenhouse.io" in url:
            return Page(
                url,
                '{"jobs":[{"id":123,"title":"Engineer",'
                '"absolute_url":"https://boards.greenhouse.io/acme/jobs/123",'
                '"location":{"name":"Remote"}}]}',
                final_url=url,
            )
        return Page(url, "<rss><channel /></rss>", final_url=url)


class SearchAndPinpointFetcher(Fetcher):
    def __init__(self):
        super().__init__(offline=True)

    def fetch(self, url, data=None, headers=None):
        if url == "https://skims.pinpointhq.com/postings.json":
            return Page(
                url,
                '{"data":[{"id":"138",'
                '"title":"Account Executive, Franchise Partnerships",'
                '"location":{"id":"9","name":"Los Angeles HQ",'
                '"city":"Los Angeles","province":"California"},'
                '"job":{"id":"139"},'
                '"path":"/en/postings/'
                '138e3bc0-c85e-40ac-9d0e-e4a7d693a7ac",'
                '"url":"https://skims.pinpointhq.com/en/postings/'
                '138e3bc0-c85e-40ac-9d0e-e4a7d693a7ac"}]}',
                final_url=url,
            )
        return Page(url, "<rss><channel /></rss>", final_url=url)


class RejectingProbeFetcher(Fetcher):
    def __init__(self):
        super().__init__(offline=True)

    def fetch(self, url, data=None, headers=None):
        if "bing.com" in url:
            return Page(url, "<rss><channel /></rss>", final_url=url)
        raise FetchError("tenant does not exist", status=404, retryable=False)


class IncompleteProbeAdapter:
    name = "probe"
    supports_listing = True

    def recognizes(self, url):
        return "greenhouse.io" in url

    def identify_board(self, url):
        return JobBoard(url=url, provider=self.name, identifier="acme")

    def list_jobs(self, fetcher, board, query):
        return AdapterResult(
            provider=self.name,
            board=board,
            retryable=True,
            inventory_complete=False,
            reason_code="FETCH_BUDGET_EXHAUSTED",
        )


class LinkedInSlugProbeAdapter:
    name = "ashby"
    supports_listing = True

    def recognizes(self, url):
        return url.startswith("https://jobs.ashbyhq.com/")

    def identify_board(self, url):
        tenant = url.rstrip("/").rsplit("/", 1)[-1]
        return JobBoard(url=url, provider=self.name, identifier=tenant)

    def list_jobs(self, fetcher, board, query):
        candidates = []
        if board.identifier == "hadrian-automation":
            candidates.append(
                JobCandidate(
                    title="Fullstack Software Engineer, New Grad",
                    url=(
                        "https://jobs.ashbyhq.com/hadrian-automation/"
                        "41472a42-c3c3-40bd-a784-8a3fbab47be3"
                    ),
                    provider=self.name,
                    location="Los Angeles, CA",
                )
            )
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            inventory_complete=True,
        )


class MismatchedTenantProbeAdapter(LinkedInSlugProbeAdapter):
    def list_jobs(self, fetcher, board, query):
        return AdapterResult(
            provider=self.name,
            board=JobBoard(
                url="https://jobs.ashbyhq.com/different-company",
                provider=self.name,
                identifier="different-company",
            ),
            candidates=[
                JobCandidate(
                    title="Engineer",
                    url="https://jobs.ashbyhq.com/different-company/job-id",
                    provider=self.name,
                )
            ],
            inventory_complete=True,
        )


class LegalSlugWorkableAdapter:
    name = "workable"
    supports_listing = True

    def recognizes(self, url):
        return url.startswith("https://apply.workable.com/")

    def identify_board(self, url):
        tenant = url.rstrip("/").rsplit("/", 1)[-1]
        return JobBoard(url=url, provider=self.name, identifier=tenant)

    def list_jobs(self, fetcher, board, query):
        candidates = []
        if board.identifier == "garan-incorporated":
            candidates.append(
                JobCandidate(
                    title="Junior Financial Operations Analyst",
                    url=(
                        "https://apply.workable.com/garan-incorporated/"
                        "j/6EA77C8F89/"
                    ),
                    provider=self.name,
                    location="New York, NY",
                )
            )
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            inventory_scope="title_filtered",
            inventory_complete=False,
        )


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
        query_diversity_first=False,
    ):
        self.calls.append(
            (
                company_name,
                company_website_url,
                target_title,
                ats_only,
                exhaustive,
                query_diversity_first,
            )
        )
        return CareerSearchResult(self.candidates, self.trace)


class ProviderSearchCandidateDiscoveryTests(unittest.TestCase):
    def test_full_legal_slug_reaches_workable_within_bounded_probe_wave(self):
        discovery = ProviderSearchCandidateDiscovery(
            CareerSearchResolver(
                MappingFetcher("<rss><channel /></rss>"),
                max_queries=1,
                max_source_fetches=1,
            ),
            provider_registry=ProviderRegistry((LegalSlugWorkableAdapter(),)),
            max_probe_attempts=4,
        )

        result = discovery.discover(
            CandidateDiscoveryRequest(
                company_name="Garan, Incorporated",
                linkedin_company_url="https://www.linkedin.com/company/garan",
                target_title="Junior Financial Operations Analyst",
                target_location="New York, NY",
            )
        )

        self.assertEqual(
            [candidate.url for candidate in result.candidates],
            ["https://apply.workable.com/garan-incorporated"],
        )
        self.assertEqual(result.trace["tenant_probe_fallback"]["status"], "used")
        self.assertEqual(
            result.trace["tenant_probe_fallback"]["attempts"][-1]["provider"],
            "workable",
        )
        self.assertEqual(
            result.trace["tenant_probe_fallback"]["attempts"][-1]["reason"],
            "provider_target_opening_verified",
        )

    def test_tenant_probe_has_one_global_attempt_cap(self):
        discovery = ProviderSearchCandidateDiscovery(
            CareerSearchResolver(
                RejectingProbeFetcher(), max_queries=1, max_source_fetches=1
            ),
            max_probe_attempts=4,
        )
        result = discovery.discover(
            CandidateDiscoveryRequest(
                company_name="Redlands Community Hospital",
                linkedin_company_url=(
                    "https://www.linkedin.com/company/redlands-community-hospital"
                ),
            )
        )

        probe = result.trace["tenant_probe_fallback"]
        self.assertEqual(probe["reason"], "provider_tenant_probe_limit_reached")
        self.assertEqual(len(probe["attempts"]), 4)

    def test_stops_after_first_valid_ats_result_with_search_provenance(self):
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

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].provider_hint, "ashby")
        self.assertEqual(result.candidates[0].result_rank, 1)
        self.assertFalse(result.trace["search"]["exhaustive"])
        self.assertEqual(
            result.trace["search"]["stopped_reason"],
            "search_candidate_found",
        )
        self.assertTrue(
            all(
                candidate.query == '"acme" "Software Engineer" jobs'
                for candidate in result.candidates
            )
        )
        self.assertEqual(result.candidates[0].source_kind, "targeted_board_search")
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
        self.assertEqual(
            resolver.calls,
            [("Acme", "", None, True, False, True)],
        )

    def test_no_search_results_produces_an_empty_candidate_set(self):
        discovery = ProviderSearchCandidateDiscovery(
            CareerSearchResolver(MappingFetcher("<rss><channel /></rss>"), max_queries=1)
        )

        result = discovery.discover(CandidateDiscoveryRequest(company_name="Acme"))

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.trace["candidate_count"], 0)
        self.assertEqual(result.trace["skipped_candidate_count"], 0)
        self.assertEqual(
            result.trace["tenant_probe_fallback"]["reason"],
            "probe_source_unavailable",
        )

    def test_no_search_results_with_verified_website_emits_verified_tenant_probe(self):
        discovery = ProviderSearchCandidateDiscovery(
            CareerSearchResolver(SearchAndGreenhouseFetcher(), max_queries=1)
        )

        result = discovery.discover(
            CandidateDiscoveryRequest(
                company_name="Acme Group",
                company_website_url="https://www.acme.example",
                target_title="Engineer",
            )
        )

        self.assertEqual(result.trace["tenant_probe_fallback"]["status"], "used")
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].url, "https://boards.greenhouse.io/acme")
        self.assertTrue(
            all(
                candidate.source_kind == "verified_tenant_probe"
                for candidate in result.candidates
            )
        )
        self.assertTrue(
            all(candidate.query is None and candidate.result_rank is None for candidate in result.candidates)
        )
        self.assertEqual(
            result.trace["tenant_probe_fallback"]["attempts"][-1]["status"],
            "verified",
        )

    def test_verified_website_slug_can_probe_pinpoint_inventory(self):
        discovery = ProviderSearchCandidateDiscovery(
            CareerSearchResolver(SearchAndPinpointFetcher(), max_queries=1)
        )

        result = discovery.discover(
            CandidateDiscoveryRequest(
                company_name="SKIMS",
                company_website_url="https://skims.com/",
                target_title="Account Executive, Franchise Partnerships",
                target_location="Los Angeles, CA",
            )
        )

        self.assertEqual(result.trace["tenant_probe_fallback"]["status"], "used")
        self.assertEqual(
            [candidate.url for candidate in result.candidates],
            ["https://skims.pinpointhq.com"],
        )
        self.assertEqual(result.candidates[0].provider_hint, "pinpoint")

    def test_missing_website_can_revalidate_linkedin_company_slug(self):
        discovery = ProviderSearchCandidateDiscovery(
            CareerSearchResolver(MappingFetcher("<rss><channel /></rss>"), max_queries=1),
            provider_registry=ProviderRegistry((LinkedInSlugProbeAdapter(),)),
        )

        result = discovery.discover(
            CandidateDiscoveryRequest(
                company_name="Hadrian",
                linkedin_company_url=(
                    "https://www.linkedin.com/company/hadrian-automation/"
                ),
                target_title="Fullstack Software Engineer, New Grad",
                target_location="Los Angeles, CA",
            )
        )

        self.assertEqual(result.trace["tenant_probe_fallback"]["status"], "used")
        self.assertEqual(
            [candidate.url for candidate in result.candidates],
            ["https://jobs.ashbyhq.com/hadrian-automation"],
        )
        self.assertEqual(
            result.candidates[0].source_url,
            "https://www.linkedin.com/company/hadrian-automation",
        )

    def test_mismatched_tenant_inventory_does_not_authorize_probe(self):
        discovery = ProviderSearchCandidateDiscovery(
            CareerSearchResolver(MappingFetcher("<rss><channel /></rss>"), max_queries=1),
            provider_registry=ProviderRegistry((MismatchedTenantProbeAdapter(),)),
        )

        result = discovery.discover(
            CandidateDiscoveryRequest(
                company_name="Hadrian",
                linkedin_company_url=(
                    "https://www.linkedin.com/company/hadrian-automation/"
                ),
                target_title="Engineer",
            )
        )

        self.assertEqual(result.candidates, ())
        self.assertTrue(
            any(
                attempt["reason"] == "provider_tenant_mismatch"
                for attempt in result.trace["tenant_probe_fallback"]["attempts"]
            )
        )

    def test_nonexistent_guessed_tenants_do_not_emit_candidates(self):
        discovery = ProviderSearchCandidateDiscovery(
            CareerSearchResolver(RejectingProbeFetcher(), max_queries=1)
        )

        result = discovery.discover(
            CandidateDiscoveryRequest(
                company_name="No Such Tenant",
                company_website_url="https://nosuchtenant.example",
                target_title="Engineer",
            )
        )

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.trace["tenant_probe_fallback"]["status"], "rejected")
        self.assertTrue(result.trace["tenant_probe_fallback"]["attempts"])
        self.assertTrue(
            all(
                attempt["status"] == "rejected"
                for attempt in result.trace["tenant_probe_fallback"]["attempts"]
            )
        )

    def test_incomplete_provider_inventory_does_not_emit_guessed_board(self):
        discovery = ProviderSearchCandidateDiscovery(
            CareerSearchResolver(MappingFetcher("<rss><channel /></rss>"), max_queries=1),
            provider_registry=ProviderRegistry((IncompleteProbeAdapter(),)),
        )

        result = discovery.discover(
            CandidateDiscoveryRequest(
                company_name="Acme",
                company_website_url="https://acme.example",
                target_title="Engineer",
            )
        )

        self.assertEqual(result.candidates, ())
        attempts = result.trace["tenant_probe_fallback"]["attempts"]
        self.assertTrue(attempts)
        self.assertTrue(
            all(
                attempt["reason"] in {
                    "provider_inventory_incomplete",
                    "provider_inventory_retryable",
                    "provider_inventory_empty",
                    "provider_not_listable",
                }
                for attempt in attempts
            )
        )

    def test_complete_but_empty_guessed_inventory_does_not_prove_company_tenant(self):
        discovery = ProviderSearchCandidateDiscovery(
            CareerSearchResolver(MappingFetcher("<rss><channel /></rss>"), max_queries=1),
            provider_registry=ProviderRegistry((IncompleteProbeAdapter(),)),
        )
        adapter = discovery.provider_registry.adapters[0]
        adapter.list_jobs = lambda fetcher, board, query: AdapterResult(
            provider=adapter.name,
            board=board,
            candidates=[],
            inventory_complete=True,
        )

        result = discovery.discover(
            CandidateDiscoveryRequest(
                company_name="Acme",
                company_website_url="https://acme.example",
            )
        )

        self.assertEqual(result.candidates, ())
        self.assertTrue(
            all(
                attempt["reason"] in {"provider_inventory_empty", "provider_not_listable"}
                for attempt in result.trace["tenant_probe_fallback"]["attempts"]
            )
        )

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
        self.assertEqual(len(first.candidates), 1)
        self.assertFalse(first.trace["truncated"])
        self.assertEqual([candidate.result_rank for candidate in first.candidates], [1])

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
