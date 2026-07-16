import unittest

from job_source_agent.candidate_portfolio import (
    CompositeCandidateDiscovery,
    ProviderCandidatePortfolioBuilder,
)
from job_source_agent.provider_candidates import (
    CandidateDiscoveryRequest,
    CandidateDiscoveryResult,
    ProviderCandidate,
)
from job_source_agent.providers import DEFAULT_PROVIDER_REGISTRY


class _Discovery:
    def __init__(self, *candidates, error=None):
        self.candidates = candidates
        self.error = error

    def discover(self, request):
        if self.error:
            raise self.error
        return CandidateDiscoveryResult(tuple(self.candidates), {"request": request.company_name})


def _candidate(url, source_kind, **kwargs):
    values = {
        "source_url": url,
        "company_name": "Acme",
        "target_title": "Engineer",
    }
    values.update(kwargs)
    return ProviderCandidate(url=url, source_kind=source_kind, **values)


class CandidatePortfolioTests(unittest.TestCase):
    def test_composite_isolates_source_failure_and_ranks_all_leads(self):
        direct = _candidate(
            "https://jobs.lever.co/acme/123",
            "external_apply",
            provider_hint="lever",
        )
        search = _candidate(
            "https://jobs.ashbyhq.com/acme",
            "targeted_board_search",
            source_url="https://www.bing.com/search?q=acme",
            query='site:jobs.ashbyhq.com "Acme"',
            result_rank=1,
            provider_hint="ashby",
        )
        discovery = CompositeCandidateDiscovery(
            (_Discovery(search), _Discovery(error=RuntimeError("offline")), _Discovery(direct)),
            limit=12,
        )

        pool, trace = discovery.discover(CandidateDiscoveryRequest(company_name="Acme"))

        self.assertEqual(pool.candidates, (direct, search))
        self.assertEqual([item["status"] for item in trace["sources"]], ["success", "failed", "success"])

    def test_adapter_verification_builds_bounded_typed_portfolio(self):
        direct = _candidate(
            "https://jobs.lever.co/acme/123",
            "external_apply",
            provider_hint="lever",
        )
        search = _candidate(
            "https://jobs.ashbyhq.com/acme",
            "targeted_board_search",
            source_url="https://www.bing.com/search?q=acme",
            query='site:jobs.ashbyhq.com "Acme"',
            result_rank=1,
            provider_hint="ashby",
        )
        pool, _trace = CompositeCandidateDiscovery(
            (_Discovery(search, direct),), limit=12
        ).discover(CandidateDiscoveryRequest(company_name="Acme"))

        result = ProviderCandidatePortfolioBuilder(DEFAULT_PROVIDER_REGISTRY).build(pool)

        self.assertIsNotNone(result.portfolio)
        self.assertEqual(result.portfolio.primary.detection_method, "external_apply_url")
        self.assertEqual(result.verified[1].discovered_board.detection_method, "targeted_search")
        self.assertEqual(result.verified[1].candidate.source_url, "https://www.bing.com/search?q=acme")

    def test_detection_only_provider_is_rejected(self):
        candidate = _candidate(
            "https://jobs.bamboohr.com/acme",
            "external_apply",
            provider_hint="bamboohr",
        )
        pool, _trace = CompositeCandidateDiscovery(
            (_Discovery(candidate),), limit=12
        ).discover(CandidateDiscoveryRequest(company_name="Acme"))

        result = ProviderCandidatePortfolioBuilder(DEFAULT_PROVIDER_REGISTRY).build(pool)

        self.assertIsNone(result.portfolio)
        self.assertEqual(result.trace["rejected_candidates"][0]["reason"], "provider_not_listable")


if __name__ == "__main__":
    unittest.main()
