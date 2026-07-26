from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from json import JSONDecodeError
from collections.abc import Mapping
from typing import Any, Callable, Protocol, runtime_checkable

from .candidate_reasoning_contracts import (
    MAX_PLANNER_QUERIES,
    MAX_RANKER_CANDIDATES,
    MAX_URL_HYPOTHESES,
    CandidateEvidence,
    CandidateRankerDecision,
    CandidateRankerRequest,
    CompanyCandidateRanker,
    CompanyQueryPlanner,
    LLMAdvisoryFailure,
    LLMDecisionKey,
    LLMDecisionRecord,
    LLMDecisionStore,
    LLMOutputURLForbidden,
    QueryPlannerDecision,
    QueryPlannerRequest,
    SearchQuerySpec,
    TokenUsage,
    llm_decision_key_digest,
)
from .candidate_reasoning_policy import (
    CandidateReasoningEligibilityContext,
    CandidateReasoningEligibilityResult,
    evaluate_candidate_reasoning_eligibility,
)


MAX_OUTPUT_CANDIDATES = 3
_DIGEST = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@runtime_checkable
class CompanySearchBackend(Protocol):
    """Searches one system-identified query and returns search-backed evidence."""

    def search(
        self,
        query: SearchQuerySpec,
        *,
        query_id: str,
        remaining_seconds: float,
    ) -> tuple[CandidateEvidence, ...]:
        ...


@dataclass(frozen=True)
class CandidateReasoningMetadata:
    normalized_company_identity_digest: str
    input_evidence_digest: str
    llm_provider: str
    model_id: str
    prompt_version: str
    adapter_version: str
    execution_fingerprint: str
    created_at_epoch: float

    def __post_init__(self) -> None:
        for name in (
            "normalized_company_identity_digest",
            "input_evidence_digest",
            "execution_fingerprint",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _DIGEST.fullmatch(value):
                raise ValueError(f"{name} must be a SHA-256 digest")
        for name in ("llm_provider", "model_id", "prompt_version", "adapter_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"{name} is invalid")
        if (
            isinstance(self.created_at_epoch, bool)
            or not isinstance(self.created_at_epoch, (int, float))
            or not math.isfinite(self.created_at_epoch)
            or self.created_at_epoch < 0
        ):
            raise ValueError("created_at_epoch must be finite and nonnegative")


@dataclass(frozen=True)
class CandidateReasoningResult:
    eligibility: CandidateReasoningEligibilityResult
    candidates: tuple[CandidateEvidence, ...]
    advisory_failure: LLMAdvisoryFailure | None = None
    llm_plan_used: bool = False
    llm_rank_used: bool = False
    llm_hypothesis_used: bool = False

    def __post_init__(self) -> None:
        if len(self.candidates) > MAX_OUTPUT_CANDIDATES:
            raise ValueError("Coordinator output exceeds the Top 3 limit")
        if self.llm_rank_used and self.advisory_failure is not None:
            raise ValueError("A failed advisory cannot supply the adopted ranking")
        for name in ("llm_plan_used", "llm_rank_used", "llm_hypothesis_used"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be boolean")
        if self.llm_hypothesis_used and not self.llm_plan_used:
            raise ValueError("URL hypothesis use requires an adopted LLM plan")

    @property
    def used_llm_ranking(self) -> bool:
        """Compatibility alias for schema 1 decision artifacts."""
        return self.llm_rank_used


class CandidateReasoningCoordinator:
    def __init__(
        self,
        *,
        planner: CompanyQueryPlanner,
        ranker: CompanyCandidateRanker,
        search_backend: CompanySearchBackend,
        decision_store: LLMDecisionStore,
        clock: Callable[[], float],
        max_candidates: int = MAX_RANKER_CANDIDATES,
        max_calls_per_company: int = 2,
    ) -> None:
        self._planner = planner
        self._ranker = ranker
        self._search_backend = search_backend
        self._decision_store = decision_store
        self._clock = clock
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or not 1 <= max_candidates <= MAX_RANKER_CANDIDATES:
            raise ValueError("max_candidates must be between 1 and 10")
        if isinstance(max_calls_per_company, bool) or not isinstance(max_calls_per_company, int) or not 1 <= max_calls_per_company <= 2:
            raise ValueError("max_calls_per_company must be between 1 and 2")
        self._max_candidates = max_candidates
        self._max_calls_per_company = max_calls_per_company

    def run(
        self,
        *,
        eligibility_context: CandidateReasoningEligibilityContext,
        planner_request: QueryPlannerRequest,
        metadata: CandidateReasoningMetadata,
        deadline: float,
        planner_timeout_seconds: float = 3.0,
        search_timeout_seconds: float = 2.0,
        ranker_timeout_seconds: float = 3.0,
        baseline_candidates: tuple[CandidateEvidence, ...] = (),
    ) -> CandidateReasoningResult:
        eligibility = evaluate_candidate_reasoning_eligibility(eligibility_context)
        baseline = _stable_candidates(baseline_candidates)[: self._max_candidates]
        if not eligibility.eligible:
            return CandidateReasoningResult(eligibility, baseline[:MAX_OUTPUT_CANDIDATES])

        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            return self._fallback(eligibility, baseline, "SCHEMA_INVALID", "query_plan")
        phase_timeouts = (
            planner_timeout_seconds,
            search_timeout_seconds,
            ranker_timeout_seconds,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            for value in phase_timeouts
        ):
            return self._fallback(eligibility, baseline, "SCHEMA_INVALID", "query_plan")

        if self._expired(deadline):
            return self._fallback(eligibility, baseline, "TIMEOUT", "query_plan")

        planner_key = _decision_key("query_plan", metadata, metadata.input_evidence_digest)
        planner_record = self._load_record(
            planner_key,
            replay_mode=eligibility_context.replay_mode,
        )
        if planner_record is not None:
            if planner_record.execution_fingerprint != metadata.execution_fingerprint:
                if eligibility_context.replay_mode:
                    raise ValueError("LLM replay execution fingerprint is incompatible")
                planner_record = None
        if planner_record is not None and planner_record.status == "failure":
            return self._fallback(
                eligibility,
                baseline,
                planner_record.failure_code or "PROVIDER_ERROR",
                "query_plan",
            )
        if planner_record is not None:
            try:
                planner_decision = QueryPlannerDecision.from_payload(
                    _mutable_json_object(planner_record.sanitized_response)
                )
            except (TypeError, ValueError):
                if eligibility_context.replay_mode:
                    raise
                planner_record = None
        planner_was_loaded = planner_record is not None
        if planner_record is None:
            started = self._clock()
            planner_call_timeout = self._phase_timeout(
                deadline,
                cap=planner_timeout_seconds,
                reserve=search_timeout_seconds + ranker_timeout_seconds,
            )
            if planner_call_timeout <= 0:
                return self._fallback(eligibility, baseline, "TIMEOUT", "query_plan")
            planner_deadline = started + planner_call_timeout
            try:
                planner_decision = self._planner.plan(
                    planner_request,
                    timeout_seconds=planner_call_timeout,
                )
            except TimeoutError:
                return self._audited_fallback(
                    eligibility, baseline, "TIMEOUT", "query_plan", metadata,
                    _planner_request_payload(planner_request), (), (),
                    (self._clock() - started) * 1_000,
                )
            except JSONDecodeError:
                return self._audited_fallback(
                    eligibility, baseline, "MALFORMED_JSON", "query_plan", metadata,
                    _planner_request_payload(planner_request), (), (),
                    (self._clock() - started) * 1_000,
                )
            except LLMOutputURLForbidden:
                return self._audited_fallback(
                    eligibility, baseline, "OUTPUT_URL_FORBIDDEN", "query_plan", metadata,
                    _planner_request_payload(planner_request), (), (),
                    (self._clock() - started) * 1_000,
                )
            except (TypeError, ValueError):
                return self._audited_fallback(
                    eligibility, baseline, "SCHEMA_INVALID", "query_plan", metadata,
                    _planner_request_payload(planner_request), (), (),
                    (self._clock() - started) * 1_000,
                )
            except Exception:
                return self._audited_fallback(
                    eligibility, baseline, "PROVIDER_ERROR", "query_plan", metadata,
                    _planner_request_payload(planner_request), (), (),
                    (self._clock() - started) * 1_000,
                )
            if self._expired(planner_deadline):
                return self._audited_fallback(
                    eligibility, baseline, "TIMEOUT", "query_plan", metadata,
                    _planner_request_payload(planner_request), (), (),
                    (self._clock() - started) * 1_000,
                )
            if not isinstance(planner_decision, QueryPlannerDecision):
                return self._audited_fallback(
                    eligibility, baseline, "SCHEMA_INVALID", "query_plan", metadata,
                    _planner_request_payload(planner_request), (), (),
                    (self._clock() - started) * 1_000,
                )

            planner_record = _planner_record(
                request=planner_request,
                decision=planner_decision,
                metadata=metadata,
                duration_ms=(self._clock() - started) * 1_000,
                token_usage=_reported_token_usage(self._planner),
            )
            try:
                self._decision_store.save(planner_record)
            except Exception:
                return self._fallback(eligibility, baseline, "DECISION_STORE_ERROR", "query_plan")

        search_started = self._clock()
        if planner_was_loaded:
            search_started += planner_record.duration_ms / 1_000
        search_deadline = min(
            deadline - ranker_timeout_seconds,
            search_started + search_timeout_seconds,
        )

        queries = planner_decision.queries[:MAX_PLANNER_QUERIES]

        discovered: list[CandidateEvidence] = list(baseline)
        seen = {candidate.candidate_id for candidate in discovered}
        for index, hypothesis in enumerate(
            planner_decision.url_hypotheses[:MAX_URL_HYPOTHESES],
            start=1,
        ):
            if len(discovered) >= self._max_candidates:
                break
            candidate = _hypothesis_candidate(
                hypothesis.url,
                hypothesis.purpose,
                hypothesis.confidence,
                index,
            )
            if candidate.candidate_id in seen:
                continue
            discovered.append(candidate)
            seen.add(candidate.candidate_id)
        for index, query in enumerate(queries, start=1):
            if len(discovered) >= self._max_candidates:
                break
            search_seconds_remaining = max(0.0, search_deadline - self._clock())
            if search_seconds_remaining <= 0:
                return self._fallback(eligibility, baseline, "TIMEOUT", "candidate_rank")
            query_id = f"llm-query-{index}"
            try:
                results = self._search_backend.search(
                    query,
                    query_id=query_id,
                    remaining_seconds=search_seconds_remaining,
                )
            except TimeoutError:
                return self._fallback(eligibility, baseline, "TIMEOUT", "candidate_rank")
            except (TypeError, ValueError):
                return self._fallback(eligibility, baseline, "SCHEMA_INVALID", "candidate_rank")
            except Exception:
                return self._fallback(eligibility, baseline, "PROVIDER_ERROR", "candidate_rank")
            if not isinstance(results, tuple):
                return self._fallback(eligibility, baseline, "SCHEMA_INVALID", "candidate_rank")
            for candidate in results:
                if not isinstance(candidate, CandidateEvidence) or candidate.query_id != query_id:
                    return self._fallback(eligibility, baseline, "SCHEMA_INVALID", "candidate_rank")
                if candidate.candidate_id in seen:
                    continue
                discovered.append(candidate)
                seen.add(candidate.candidate_id)
                if len(discovered) >= self._max_candidates:
                    break

        baseline_order = _stable_candidates(tuple(discovered))[: self._max_candidates]
        llm_plan_used = any(
            candidate.candidate_id not in {item.candidate_id for item in baseline}
            for candidate in baseline_order
        )
        llm_hypothesis_used = any(
            candidate.source == "llm-url-hypothesis"
            for candidate in baseline_order[:MAX_OUTPUT_CANDIDATES]
        )
        if not baseline_order:
            return CandidateReasoningResult(eligibility, ())
        if self._max_calls_per_company == 1:
            return CandidateReasoningResult(
                eligibility,
                baseline_order[:MAX_OUTPUT_CANDIDATES],
                llm_plan_used=llm_plan_used,
                llm_hypothesis_used=llm_hypothesis_used,
            )
        ranker_call_timeout = self._phase_timeout(
            deadline,
            cap=ranker_timeout_seconds,
        )
        if ranker_call_timeout <= 0:
            return self._fallback(eligibility, baseline_order, "TIMEOUT", "candidate_rank")

        ranker_request = CandidateRankerRequest(
            normalized_company_name=planner_request.normalized_company_name,
            industry=planner_request.industry,
            company_location=planner_request.company_location,
            candidates=baseline_order,
            context_evidence_ids=("linkedin_slug",)
            if planner_request.linkedin_company_slug
            else (),
        )
        ranker_request_payload = _ranker_request_payload(
            ranker_request,
            invocation_input_evidence_digest=metadata.input_evidence_digest,
        )
        ranker_query_ids = tuple(
            dict.fromkeys(item.query_id for item in baseline_order)
        )[:MAX_PLANNER_QUERIES]
        rank_input_digest = _rank_input_digest(metadata.input_evidence_digest, baseline_order)
        ranker_key = _decision_key("candidate_rank", metadata, rank_input_digest)
        ranker_record = self._load_record(
            ranker_key,
            replay_mode=eligibility_context.replay_mode,
        )
        if ranker_record is not None:
            if ranker_record.execution_fingerprint != metadata.execution_fingerprint:
                if eligibility_context.replay_mode:
                    raise ValueError("LLM replay execution fingerprint is incompatible")
                ranker_record = None
        if ranker_record is not None and ranker_record.status == "failure":
            return self._fallback(
                eligibility,
                baseline_order,
                ranker_record.failure_code or "PROVIDER_ERROR",
                "candidate_rank",
            )
        if ranker_record is not None:
            try:
                ranker_decision = CandidateRankerDecision.from_payload(
                    _mutable_json_object(ranker_record.sanitized_response),
                    ranker_request,
                )
            except (TypeError, ValueError):
                if eligibility_context.replay_mode:
                    raise
                ranker_record = None
        if ranker_record is None:
            started = self._clock()
            ranker_deadline = started + ranker_call_timeout
            try:
                ranker_decision = self._ranker.rank(
                    ranker_request,
                    timeout_seconds=ranker_call_timeout,
                )
            except TimeoutError:
                return self._audited_fallback(
                    eligibility, baseline_order, "TIMEOUT", "candidate_rank", metadata,
                    ranker_request_payload, baseline_order, ranker_query_ids,
                    (self._clock() - started) * 1_000,
                )
            except JSONDecodeError:
                return self._audited_fallback(
                    eligibility, baseline_order, "MALFORMED_JSON", "candidate_rank", metadata,
                    ranker_request_payload, baseline_order, ranker_query_ids,
                    (self._clock() - started) * 1_000,
                )
            except (TypeError, ValueError):
                return self._audited_fallback(
                    eligibility, baseline_order, "SCHEMA_INVALID", "candidate_rank", metadata,
                    ranker_request_payload, baseline_order, ranker_query_ids,
                    (self._clock() - started) * 1_000,
                )
            except Exception:
                return self._audited_fallback(
                    eligibility, baseline_order, "PROVIDER_ERROR", "candidate_rank", metadata,
                    ranker_request_payload, baseline_order, ranker_query_ids,
                    (self._clock() - started) * 1_000,
                )
            if self._expired(ranker_deadline):
                return self._audited_fallback(
                    eligibility, baseline_order, "TIMEOUT", "candidate_rank", metadata,
                    ranker_request_payload, baseline_order, ranker_query_ids,
                    (self._clock() - started) * 1_000,
                )
            if not isinstance(ranker_decision, CandidateRankerDecision):
                return self._audited_fallback(
                    eligibility, baseline_order, "SCHEMA_INVALID", "candidate_rank", metadata,
                    ranker_request_payload, baseline_order, ranker_query_ids,
                    (self._clock() - started) * 1_000,
                )

        by_id = {candidate.candidate_id: candidate for candidate in baseline_order}
        ranked_ids = tuple(item.candidate_id for item in ranker_decision.ranked_candidates)
        if set(ranked_ids) != set(by_id):
            return self._fallback(eligibility, baseline_order, "UNKNOWN_CANDIDATE_ID", "candidate_rank")
        allowed_evidence_ids = set(by_id) | set(ranker_request.context_evidence_ids)
        if any(
            evidence_id not in allowed_evidence_ids
            for item in ranker_decision.ranked_candidates
            for evidence_id in item.evidence_ids
        ):
            return self._fallback(eligibility, baseline_order, "UNKNOWN_CANDIDATE_ID", "candidate_rank")
        ranked = tuple(by_id[candidate_id] for candidate_id in ranked_ids)
        if ranker_record is None:
            ranker_record = _ranker_record(
                request=ranker_request,
                decision=ranker_decision,
                metadata=metadata,
                duration_ms=(self._clock() - started) * 1_000,
                query_ids=ranker_query_ids,
                token_usage=_reported_token_usage(self._ranker),
            )
            try:
                self._decision_store.save(ranker_record)
            except Exception:
                return self._fallback(eligibility, baseline_order, "DECISION_STORE_ERROR", "candidate_rank")
        return CandidateReasoningResult(
            eligibility,
            ranked[:MAX_OUTPUT_CANDIDATES],
            llm_plan_used=llm_plan_used,
            llm_rank_used=True,
            llm_hypothesis_used=any(
                candidate.source == "llm-url-hypothesis"
                for candidate in ranked[:MAX_OUTPUT_CANDIDATES]
            ),
        )

    def _expired(self, deadline: float) -> bool:
        return self._clock() >= deadline

    def _phase_timeout(
        self,
        deadline: float,
        *,
        cap: float,
        reserve: float = 0.0,
    ) -> float:
        return max(0.0, min(float(cap), deadline - self._clock() - reserve))

    def _load_record(
        self,
        key: LLMDecisionKey,
        *,
        replay_mode: bool,
    ) -> LLMDecisionRecord | None:
        try:
            return self._decision_store.load(key)
        except Exception:
            if replay_mode:
                raise
            return None

    def _audited_fallback(
        self,
        eligibility: CandidateReasoningEligibilityResult,
        candidates: tuple[CandidateEvidence, ...],
        code: str,
        decision_kind: str,
        metadata: CandidateReasoningMetadata,
        request_payload: dict[str, object],
        evidence_candidates: tuple[CandidateEvidence, ...],
        query_ids: tuple[str, ...],
        duration_ms: float,
    ) -> CandidateReasoningResult:
        try:
            self._decision_store.save(
                _record(
                    decision_kind,
                    request_payload,
                    {},
                    tuple(item.candidate_id for item in evidence_candidates),
                    query_ids,
                    evidence_candidates,
                    metadata,
                    duration_ms,
                    status="failure",
                    failure_code=code,
                    token_usage=_reported_token_usage(
                        self._planner if decision_kind == "query_plan" else self._ranker
                    ),
                )
            )
        except Exception:
            pass
        return self._fallback(eligibility, candidates, code, decision_kind)

    @staticmethod
    def _fallback(
        eligibility: CandidateReasoningEligibilityResult,
        candidates: tuple[CandidateEvidence, ...],
        code: str,
        decision_kind: str,
    ) -> CandidateReasoningResult:
        return CandidateReasoningResult(
            eligibility,
            candidates[:MAX_OUTPUT_CANDIDATES],
            LLMAdvisoryFailure(code, decision_kind),
            llm_plan_used=any(
                candidate.query_id.startswith("llm-query-")
                or candidate.query_id == "llm-hypothesis"
                for candidate in candidates
            ),
            llm_hypothesis_used=any(
                candidate.source == "llm-url-hypothesis"
                for candidate in candidates[:MAX_OUTPUT_CANDIDATES]
            ),
        )


def _stable_candidates(candidates: tuple[CandidateEvidence, ...]) -> tuple[CandidateEvidence, ...]:
    unique: list[CandidateEvidence] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, CandidateEvidence):
            raise TypeError("baseline_candidates must contain CandidateEvidence")
        if candidate.candidate_id not in seen:
            unique.append(candidate)
            seen.add(candidate.candidate_id)
    return tuple(unique)


def _planner_record(
    *,
    request: QueryPlannerRequest,
    decision: QueryPlannerDecision,
    metadata: CandidateReasoningMetadata,
    duration_ms: float,
    token_usage: TokenUsage = TokenUsage(0, 0, 0),
) -> LLMDecisionRecord:
    request_payload = _planner_request_payload(request)
    response_payload = {
        "schema_version": decision.schema_version,
        "normalized_company_name": decision.normalized_company_name,
        "core_brand_tokens": list(decision.core_brand_tokens),
        "legal_or_descriptive_suffixes": list(decision.legal_or_descriptive_suffixes),
        "possible_aliases": list(decision.possible_aliases),
        "queries": [{"query": item.query, "purpose": item.purpose} for item in decision.queries],
        "ambiguous": decision.ambiguous,
        "reason_codes": list(decision.reason_codes),
        "url_hypotheses": [
            {
                "url": item.url,
                "purpose": item.purpose,
                "confidence": item.confidence,
            }
            for item in decision.url_hypotheses
        ],
    }
    query_ids = tuple(f"llm-query-{index}" for index in range(1, len(decision.queries) + 1))
    return _record(
        "query_plan",
        request_payload,
        response_payload,
        (),
        query_ids,
        (),
        metadata,
        duration_ms,
        token_usage=token_usage,
    )


def _planner_request_payload(request: QueryPlannerRequest) -> dict[str, object]:
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


def _ranker_record(
    *,
    request: CandidateRankerRequest,
    decision: CandidateRankerDecision,
    metadata: CandidateReasoningMetadata,
    duration_ms: float,
    query_ids: tuple[str, ...],
    token_usage: TokenUsage = TokenUsage(0, 0, 0),
) -> LLMDecisionRecord:
    request_payload = _ranker_request_payload(
        request,
        invocation_input_evidence_digest=metadata.input_evidence_digest,
    )
    response_payload = {
        "schema_version": decision.schema_version,
        "ranked_candidates": [
            {
                "candidate_id": item.candidate_id,
                "confidence_bucket": item.confidence_bucket,
                "evidence_ids": list(item.evidence_ids),
                "reason_codes": list(item.reason_codes),
            }
            for item in decision.ranked_candidates
        ],
        "ambiguous": decision.ambiguous,
    }
    return _record(
        "candidate_rank",
        request_payload,
        response_payload,
        tuple(item.candidate_id for item in request.candidates),
        query_ids,
        request.candidates,
        metadata,
        duration_ms,
        token_usage=token_usage,
    )


def _ranker_request_payload(
    request: CandidateRankerRequest,
    *,
    invocation_input_evidence_digest: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "normalized_company_name": request.normalized_company_name,
        "industry": request.industry,
        "company_location": request.company_location,
        "candidates": [_candidate_payload(item) for item in request.candidates],
        "context_evidence_ids": list(request.context_evidence_ids),
    }
    if invocation_input_evidence_digest is not None:
        payload["invocation_input_evidence_digest"] = invocation_input_evidence_digest
    return payload


def _record(
    decision_kind: str,
    request_payload: dict[str, object],
    response_payload: dict[str, object],
    candidate_ids: tuple[str, ...],
    query_ids: tuple[str, ...],
    candidates: tuple[CandidateEvidence, ...],
    metadata: CandidateReasoningMetadata,
    duration_ms: float,
    *,
    status: str = "success",
    failure_code: str | None = None,
    token_usage: TokenUsage = TokenUsage(0, 0, 0),
) -> LLMDecisionRecord:
    evidence_digest = _digest(
        {
            "candidates": [_candidate_payload(item) for item in candidates],
            "query_ids": query_ids,
        }
    )
    input_evidence_digest = (
        metadata.input_evidence_digest
        if decision_kind == "query_plan"
        else _rank_input_digest(metadata.input_evidence_digest, candidates)
    )
    key = _decision_key(decision_kind, metadata, input_evidence_digest)
    record_key = llm_decision_key_digest(key)
    return LLMDecisionRecord(
        record_key=record_key,
        execution_fingerprint=metadata.execution_fingerprint,
        key=key,
        sanitized_request=request_payload,
        sanitized_response=response_payload,
        candidate_ids=candidate_ids,
        query_ids=query_ids,
        candidate_evidence_digest=evidence_digest,
        duration_ms=max(0.0, duration_ms),
        token_usage=token_usage,
        created_at_epoch=metadata.created_at_epoch,
        status=status,
        failure_code=failure_code,
    )


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidate_payload(candidate: CandidateEvidence) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "url": candidate.url,
        "title": candidate.title,
        "snippet": candidate.snippet,
        "source": candidate.source,
        "query_id": candidate.query_id,
        "rank": candidate.rank,
    }


def _hypothesis_candidate(
    url: str,
    purpose: str,
    confidence: str,
    rank: int,
) -> CandidateEvidence:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    return CandidateEvidence(
        candidate_id=f"llm-hypothesis-{digest}",
        url=url,
        title="",
        snippet=f"Unverified {purpose} URL hypothesis ({confidence})",
        source="llm-url-hypothesis",
        query_id="llm-hypothesis",
        rank=rank,
    )


def _rank_input_digest(
    base_input_digest: str,
    candidates: tuple[CandidateEvidence, ...],
) -> str:
    return _digest(
        {
            "base_input_digest": base_input_digest,
            "candidates": [_candidate_payload(item) for item in candidates],
        }
    )


def _decision_key(
    decision_kind: str,
    metadata: CandidateReasoningMetadata,
    input_evidence_digest: str,
) -> LLMDecisionKey:
    return LLMDecisionKey(
        decision_kind,
        metadata.normalized_company_identity_digest,
        input_evidence_digest,
        metadata.llm_provider,
        metadata.model_id,
        metadata.prompt_version,
        "1",
        metadata.adapter_version,
    )


def _mutable_json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    def thaw(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {key: thaw(child) for key, child in item.items()}
        if isinstance(item, tuple):
            return [thaw(child) for child in item]
        return item

    return {key: thaw(item) for key, item in value.items()}


def _reported_token_usage(service: object) -> TokenUsage:
    usage = getattr(service, "last_token_usage", None)
    return usage if isinstance(usage, TokenUsage) else TokenUsage(0, 0, 0)
