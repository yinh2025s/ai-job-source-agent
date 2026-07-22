"""Provider-neutral invocation boundary for optional candidate reasoning."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, replace
from typing import Callable

from .candidate_reasoning_contracts import (
    CandidateEvidence,
    CandidateRankerRequest,
    LLMAdvisoryFailure,
    LLMDecisionStore,
    QueryPlannerRequest,
)
from .candidate_reasoning_coordinator import (
    CandidateReasoningCoordinator,
    CandidateReasoningMetadata,
    CandidateReasoningResult,
)
from .candidate_reasoning_inputs import (
    DeterministicResolverOutcome,
    PublicCompanyReasoningInput,
    build_candidate_reasoning_eligibility_context,
    build_query_planner_request,
)
from .candidate_reasoning_policy import evaluate_candidate_reasoning_eligibility
from .candidate_reasoning_search import ResolverCandidateSearchBackend
from .run_configuration import DeterministicRunConfig
from .website_resolver import CompanyWebsiteResolver


@dataclass(frozen=True, slots=True)
class CandidateReasoningRuntime:
    feature_enabled: bool
    llm_provider: str = ""
    model_id: str = ""
    prompt_version: str = ""
    timeout_seconds: float = 8.0
    adapter_version: str = ""
    execution_fingerprint: str = ""
    replay_mode: bool = False
    has_compatible_replay_fixture: bool = False
    planner_timeout_seconds: float = 3.0
    search_timeout_seconds: float = 2.0
    ranker_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        if not isinstance(self.feature_enabled, bool):
            raise TypeError("feature_enabled must be boolean")
        if not isinstance(self.replay_mode, bool):
            raise TypeError("replay_mode must be boolean")
        if not isinstance(self.has_compatible_replay_fixture, bool):
            raise TypeError("has_compatible_replay_fixture must be boolean")
        if self.has_compatible_replay_fixture and not self.replay_mode:
            raise ValueError("compatible replay fixture requires replay_mode")
        for name in (
            "timeout_seconds",
            "planner_timeout_seconds",
            "search_timeout_seconds",
            "ranker_timeout_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be numeric")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if (
            self.planner_timeout_seconds
            + self.search_timeout_seconds
            + self.ranker_timeout_seconds
            > self.timeout_seconds
        ):
            raise ValueError("phase timeouts cannot exceed timeout_seconds")
        if self.feature_enabled:
            # Reuse the metadata contract for public identifiers and digests.
            CandidateReasoningMetadata(
                "0" * 64,
                "0" * 64,
                self.llm_provider,
                self.model_id,
                self.prompt_version,
                self.adapter_version,
                self.execution_fingerprint,
                0.0,
            )


class CandidateReasoningInvocationService:
    """Build sanitized input and one shared deadline around the coordinator."""

    def __init__(
        self,
        coordinator: CandidateReasoningCoordinator,
        runtime: CandidateReasoningRuntime,
        *,
        monotonic_clock: Callable[[], float],
        wall_clock: Callable[[], float],
    ) -> None:
        if not isinstance(coordinator, CandidateReasoningCoordinator):
            raise TypeError("coordinator must use CandidateReasoningCoordinator")
        if not isinstance(runtime, CandidateReasoningRuntime):
            raise TypeError("runtime must use CandidateReasoningRuntime")
        self._coordinator = coordinator
        self._runtime = runtime
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock

    @property
    def enabled(self) -> bool:
        return self._runtime.feature_enabled

    def reason(
        self,
        company: PublicCompanyReasoningInput,
        outcome: DeterministicResolverOutcome,
        *,
        baseline_candidates: tuple[CandidateEvidence, ...] = (),
    ) -> CandidateReasoningResult:
        outcome = replace(
            outcome,
            replay_mode=self._runtime.replay_mode,
            has_compatible_replay_fixture=(
                self._runtime.has_compatible_replay_fixture
                if self._runtime.replay_mode
                else False
            ),
        )
        eligibility_context = build_candidate_reasoning_eligibility_context(
            feature_enabled=self._runtime.feature_enabled,
            outcome=outcome,
        )
        eligibility = evaluate_candidate_reasoning_eligibility(eligibility_context)
        if not eligibility.eligible:
            return CandidateReasoningResult(eligibility, baseline_candidates[:3])

        try:
            request = build_query_planner_request(company)
        except (TypeError, ValueError):
            return CandidateReasoningResult(
                eligibility,
                baseline_candidates[:3],
                LLMAdvisoryFailure("INPUT_POLICY_REJECTED", "query_plan"),
            )
        request_payload = query_planner_request_payload(request)
        identity_payload = {
            "normalized_company_name": request.normalized_company_name.casefold(),
            "linkedin_company_slug": request.linkedin_company_slug,
        }
        metadata = CandidateReasoningMetadata(
            normalized_company_identity_digest=_digest(identity_payload),
            input_evidence_digest=_digest(request_payload),
            llm_provider=self._runtime.llm_provider,
            model_id=self._runtime.model_id,
            prompt_version=self._runtime.prompt_version,
            adapter_version=self._runtime.adapter_version,
            execution_fingerprint=self._runtime.execution_fingerprint,
            created_at_epoch=self._wall_clock(),
        )
        deadline = self._monotonic_clock() + self._runtime.timeout_seconds
        return self._coordinator.run(
            eligibility_context=eligibility_context,
            planner_request=request,
            metadata=metadata,
            deadline=deadline,
            planner_timeout_seconds=self._runtime.planner_timeout_seconds,
            search_timeout_seconds=self._runtime.search_timeout_seconds,
            ranker_timeout_seconds=self._runtime.ranker_timeout_seconds,
            baseline_candidates=baseline_candidates,
        )


def candidate_reasoning_input_evidence_digest(
    company: PublicCompanyReasoningInput,
) -> str:
    """Return the exact answer-free invocation digest used by live and bundle selection."""
    request = build_query_planner_request(company)
    return _digest(query_planner_request_payload(request))


def query_planner_request_payload(
    request: QueryPlannerRequest,
) -> dict[str, object]:
    return {
        "normalized_company_name": request.normalized_company_name,
        "linkedin_company_slug": request.linkedin_company_slug,
        "public_company_summary": request.public_company_summary,
        "job_title": request.job_title,
        "job_location": request.job_location,
        "industry": request.industry,
        "company_location": request.company_location,
        "rejected_candidates": [
            {
                "candidate_id": item.candidate_id,
                "source": item.source,
                "rejection_reason": item.rejection_reason,
                "display_domain": item.display_domain,
            }
            for item in request.rejected_candidates
        ],
    }


def build_replay_candidate_reasoning_service(
    resolver: CompanyWebsiteResolver,
    decision_store: LLMDecisionStore,
    run_configuration: DeterministicRunConfig,
    *,
    execution_identity: str,
    adapter_version: str,
) -> CandidateReasoningInvocationService:
    """Build a fixture-only service that has no real model client to call."""
    if not run_configuration.enable_llm_candidate_reasoning:
        raise ValueError("replay candidate reasoning requires an enabled run configuration")
    coordinator = CandidateReasoningCoordinator(
        planner=_ReplayOnlyPlanner(),
        ranker=_ReplayOnlyRanker(),
        search_backend=ResolverCandidateSearchBackend(resolver),
        decision_store=decision_store,
        clock=time.monotonic,
        max_candidates=run_configuration.llm_max_candidates,
        max_calls_per_company=run_configuration.llm_max_calls_per_company,
    )
    runtime = CandidateReasoningRuntime(
        feature_enabled=True,
        llm_provider=run_configuration.llm_provider,
        model_id=run_configuration.llm_model,
        prompt_version=run_configuration.llm_prompt_version,
        timeout_seconds=run_configuration.llm_timeout,
        planner_timeout_seconds=run_configuration.llm_planner_timeout,
        search_timeout_seconds=run_configuration.llm_search_timeout,
        ranker_timeout_seconds=run_configuration.llm_ranker_timeout,
        adapter_version=adapter_version,
        execution_fingerprint=execution_identity,
        replay_mode=True,
        has_compatible_replay_fixture=True,
    )
    return CandidateReasoningInvocationService(
        coordinator,
        runtime,
        monotonic_clock=time.monotonic,
        wall_clock=time.time,
    )


class _ReplayOnlyPlanner:
    def plan(self, request: QueryPlannerRequest, *, timeout_seconds: float):
        raise AssertionError("replay attempted to call a query-planner model")


class _ReplayOnlyRanker:
    def rank(self, request: CandidateRankerRequest, *, timeout_seconds: float):
        raise AssertionError("replay attempted to call a candidate-ranker model")


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
