from __future__ import annotations

import unittest

from job_source_agent.candidate_reasoning_contracts import CandidateEvidence
from job_source_agent.candidate_reasoning_coordinator import CandidateReasoningResult
from job_source_agent.candidate_reasoning_inputs import (
    build_candidate_reasoning_eligibility_context,
)
from job_source_agent.candidate_reasoning_policy import (
    evaluate_candidate_reasoning_eligibility,
)
from job_source_agent.composition import build_application_from_fetcher
from job_source_agent.contracts import PipelineContext
from job_source_agent.models import CompanyInput
from job_source_agent.run_configuration import AgentConfig
from job_source_agent.stages.upstream import WebsiteResolutionStage
from job_source_agent.web import Fetcher


class FailedDeterministicResolver:
    def __init__(self, *, reason_code="WEBSITE_NOT_RESOLVED", candidates=()):
        self.reason_code = reason_code
        self.candidates = list(candidates)
        self.verification_calls = []

    def resolve_with_navigation_evidence(self, *args, **kwargs):
        return (
            None,
            {
                "candidates": self.candidates,
                "fetch_errors": [],
                "resolution_failure": {
                    "reason_code": self.reason_code,
                    "error": "deterministic resolver retained failure",
                },
            },
            None,
        )

    def resolve_ranked_existing_candidates_with_navigation_evidence(
        self, candidates, company_name, linkedin_company_url, job_location
    ):
        self.verification_calls.append(tuple(candidates))
        return (
            "https://example.invalid",
            {"selected": {"url": "https://example.invalid", "reasons": ["homepage verified"]}},
            None,
        )


class ReasoningService:
    enabled = True

    def __init__(self, *, candidates=()):
        self.candidates = tuple(candidates)
        self.calls = []

    def reason(self, company, outcome, *, baseline_candidates=()):
        self.calls.append((company, outcome))
        eligibility = evaluate_candidate_reasoning_eligibility(
            build_candidate_reasoning_eligibility_context(
                feature_enabled=True,
                outcome=outcome,
            )
        )
        return CandidateReasoningResult(
            eligibility,
            self.candidates if eligibility.eligible else (),
            used_llm_ranking=bool(self.candidates and eligibility.eligible),
        )


class CandidateReasoningStageIntegrationTest(unittest.TestCase):
    def test_verified_top_three_candidate_can_succeed_only_after_resolver_verification(self):
        candidate = CandidateEvidence(
            "search-1",
            "https://example.invalid",
            "Example Labs",
            "Public search result",
            "fixture-search",
            "llm-query-1",
            1,
        )
        resolver = FailedDeterministicResolver()
        reasoning = ReasoningService(candidates=(candidate,))

        execution = WebsiteResolutionStage(
            resolver,
            candidate_reasoning_service=reasoning,
        ).run(self._context())

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.updates["company_website_url"], "https://example.invalid")
        self.assertEqual(resolver.verification_calls, [(candidate,)])
        self.assertEqual(len(reasoning.calls), 1)
        self.assertEqual(
            execution.trace["candidate_reasoning"]["verification_status"],
            "verified",
        )

    def test_transport_failure_is_not_sent_to_model_or_verified(self):
        resolver = FailedDeterministicResolver(reason_code="NETWORK_TIMEOUT")
        reasoning = ReasoningService(
            candidates=(
                CandidateEvidence(
                    "search-1", "https://example.invalid", "", "",
                    "fixture-search", "llm-query-1", 1,
                ),
            )
        )

        execution = WebsiteResolutionStage(
            resolver,
            candidate_reasoning_service=reasoning,
        ).run(self._context())

        self.assertEqual(execution.result.reason_code, "NETWORK_TIMEOUT")
        self.assertEqual(reasoning.calls[0][1].transport_cause, "NETWORK_TIMEOUT")
        self.assertEqual(resolver.verification_calls, [])
        self.assertEqual(
            execution.trace["candidate_reasoning"]["eligibility_state"],
            "TRANSPORT_FORBIDDEN",
        )

    def test_external_apply_and_disabled_service_do_not_invoke_reasoning(self):
        for external_apply_url, enabled in (
            ("https://apply.example.invalid/job/1", True),
            (None, False),
        ):
            with self.subTest(external_apply_url=external_apply_url, enabled=enabled):
                resolver = FailedDeterministicResolver()
                reasoning = ReasoningService()
                reasoning.enabled = enabled
                context = self._context(external_apply_url=external_apply_url)

                execution = WebsiteResolutionStage(
                    resolver,
                    candidate_reasoning_service=reasoning,
                ).run(context)

                self.assertEqual(reasoning.calls, [])
                self.assertNotIn("candidate_reasoning", execution.trace)

    def test_composition_fails_closed_when_enabled_without_injected_service(self):
        with self.assertRaisesRegex(ValueError, "no enabled provider-neutral service"):
            build_application_from_fetcher(
                Fetcher(offline=True),
                AgentConfig(
                    enable_parallel_candidate_discovery=True,
                    enable_llm_candidate_reasoning=True,
                    llm_provider="fake-provider",
                    llm_model="fake-model",
                    llm_prompt_version="prompt-v1",
                ),
            )

    @staticmethod
    def _context(*, external_apply_url=None):
        return PipelineContext.from_company(
            CompanyInput(
                company_name="Example Labs",
                linkedin_company_url="https://www.linkedin.com/company/example-labs/",
                external_apply_url=external_apply_url,
                job_title="AI Engineer",
                job_location="Seattle, WA",
            )
        )


if __name__ == "__main__":
    unittest.main()
