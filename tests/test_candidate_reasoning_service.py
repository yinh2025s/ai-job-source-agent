from __future__ import annotations

import unittest

from job_source_agent.candidate_reasoning_contracts import (
    CandidateEvidence,
    CandidateRankerDecision,
    QueryPlannerDecision,
    RankedCandidate,
    SearchQuerySpec,
)
from job_source_agent.candidate_reasoning_coordinator import CandidateReasoningCoordinator
from job_source_agent.candidate_reasoning_inputs import (
    DeterministicResolverOutcome,
    PublicCompanyReasoningInput,
)
from job_source_agent.candidate_reasoning_service import (
    CandidateReasoningInvocationService,
    CandidateReasoningRuntime,
)


class Planner:
    def __init__(self):
        self.calls = 0

    def plan(self, request, *, timeout_seconds):
        self.calls += 1
        return QueryPlannerDecision(
            request.normalized_company_name,
            ("Example",),
            ("Labs",),
            (),
            (SearchQuerySpec('"Example Labs" careers', "career_site"),),
            False,
            ("DESCRIPTIVE_SUFFIX",),
        )


class Ranker:
    def __init__(self):
        self.calls = 0

    def rank(self, request, *, timeout_seconds):
        self.calls += 1
        return CandidateRankerDecision(
            tuple(
                RankedCandidate(item.candidate_id, "high", (item.candidate_id,), ("BRAND_MATCH",))
                for item in request.candidates
            ),
            False,
        )


class Search:
    def __init__(self):
        self.calls = 0

    def search(self, query, *, query_id, remaining_seconds):
        self.calls += 1
        return (
            CandidateEvidence(
                "search-1",
                "https://example.invalid/careers",
                "Careers",
                "Public search result",
                "fixture-search",
                query_id,
                1,
            ),
        )


class Store:
    def __init__(self):
        self.records = []

    def load(self, key):
        return None

    def save(self, record):
        self.records.append(record)


class CandidateReasoningServiceTest(unittest.TestCase):
    def setUp(self):
        self.planner = Planner()
        self.ranker = Ranker()
        self.search = Search()
        self.store = Store()

    def test_disabled_short_circuits_before_request_or_any_dependency(self):
        service = self._service(enabled=False)
        result = service.reason(
            PublicCompanyReasoningInput(
                company_name="person@example.com",
                public_company_summary="Authorization: Bearer secret",
            ),
            DeterministicResolverOutcome(g_conditions=("NO_SOURCE_BACKED_CANDIDATE",)),
        )
        self.assertEqual(result.eligibility.state, "DISABLED")
        self.assertEqual((self.planner.calls, self.ranker.calls, self.search.calls), (0, 0, 0))
        self.assertEqual(self.store.records, [])

    def test_eligible_call_uses_shared_deadline_and_content_addressed_metadata(self):
        service = self._service(enabled=True)
        result = service.reason(
            PublicCompanyReasoningInput(
                company_name="Example Labs",
                linkedin_company_slug="example-labs",
                job_title="AI Engineer",
                job_location="Seattle, WA",
            ),
            DeterministicResolverOutcome(g_conditions=("NO_SOURCE_BACKED_CANDIDATE",)),
        )
        self.assertTrue(result.used_llm_ranking)
        self.assertEqual((self.planner.calls, self.ranker.calls, self.search.calls), (1, 1, 1))
        self.assertEqual(len(self.store.records), 2)
        self.assertEqual(self.store.records[0].execution_fingerprint, "c" * 64)

    def test_one_call_budget_skips_ranker_and_preserves_deterministic_order(self):
        service = self._service(enabled=True, max_calls=1)
        result = service.reason(
            PublicCompanyReasoningInput(company_name="Example Labs"),
            DeterministicResolverOutcome(g_conditions=("NO_SOURCE_BACKED_CANDIDATE",)),
        )
        self.assertEqual(self.planner.calls, 1)
        self.assertEqual(self.ranker.calls, 0)
        self.assertFalse(result.used_llm_ranking)
        self.assertEqual([item.candidate_id for item in result.candidates], ["search-1"])

    def test_enabled_sensitive_only_identity_falls_back_without_dependencies(self):
        result = self._service(enabled=True).reason(
            PublicCompanyReasoningInput(company_name="person@example.com"),
            DeterministicResolverOutcome(g_conditions=("NO_SOURCE_BACKED_CANDIDATE",)),
        )
        self.assertEqual(result.advisory_failure.code, "INPUT_POLICY_REJECTED")
        self.assertEqual((self.planner.calls, self.ranker.calls, self.search.calls), (0, 0, 0))

    def test_replay_without_compatible_fixture_stops_before_dependencies(self):
        coordinator = CandidateReasoningCoordinator(
            planner=self.planner,
            ranker=self.ranker,
            search_backend=self.search,
            decision_store=self.store,
            clock=lambda: 10.0,
        )
        service = CandidateReasoningInvocationService(
            coordinator,
            CandidateReasoningRuntime(
                True,
                "fake-provider",
                "fake-model",
                "prompt-v1",
                8.0,
                "adapter-v1",
                "c" * 64,
                replay_mode=True,
                has_compatible_replay_fixture=False,
            ),
            monotonic_clock=lambda: 10.0,
            wall_clock=lambda: 1.0,
        )
        result = service.reason(
            PublicCompanyReasoningInput(company_name="Example Labs"),
            DeterministicResolverOutcome(g_conditions=("NO_SOURCE_BACKED_CANDIDATE",)),
        )
        self.assertEqual(result.eligibility.state, "REPLAY_REQUIRED")
        self.assertEqual((self.planner.calls, self.ranker.calls, self.search.calls), (0, 0, 0))

    def _service(self, *, enabled: bool, max_calls: int = 2):
        coordinator = CandidateReasoningCoordinator(
            planner=self.planner,
            ranker=self.ranker,
            search_backend=self.search,
            decision_store=self.store,
            clock=lambda: 10.0,
            max_candidates=10,
            max_calls_per_company=max_calls,
        )
        return CandidateReasoningInvocationService(
            coordinator,
            CandidateReasoningRuntime(
                enabled,
                "fake-provider" if enabled else "",
                "fake-model" if enabled else "",
                "prompt-v1" if enabled else "",
                8.0,
                "adapter-v1" if enabled else "",
                "c" * 64 if enabled else "",
            ),
            monotonic_clock=lambda: 10.0,
            wall_clock=lambda: 1.0,
        )


if __name__ == "__main__":
    unittest.main()
