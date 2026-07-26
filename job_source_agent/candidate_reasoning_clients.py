"""Provider-neutral structured adapters for optional candidate reasoning.

These adapters deliberately do not know about prompts, provider SDKs, network
transport, or persistence.  They project already-sanitized DTOs into a small,
allowlisted structured request and strictly parse the returned structured data.
"""

from __future__ import annotations

from typing import Any, Mapping

from .candidate_reasoning_contracts import (
    CandidateRankerDecision,
    CandidateRankerRequest,
    CompanyCandidateRanker,
    CompanyQueryPlanner,
    LLMReasoningClient,
    QueryPlannerDecision,
    QueryPlannerRequest,
    StructuredLLMRequest,
    StructuredLLMResponse,
    TokenUsage,
)


QUERY_PLANNER_SCHEMA_NAME = "company_candidate_planner_v2"
CANDIDATE_RANKER_SCHEMA_NAME = "company_candidate_ranker_v1"


class StructuredCompanyQueryPlanner(CompanyQueryPlanner):
    """Adapts a structured client to the planner contract without prompting."""

    def __init__(self, client: LLMReasoningClient) -> None:
        self._client = _require_client(client)
        self._last_token_usage = TokenUsage(0, 0, 0)

    @property
    def last_token_usage(self) -> TokenUsage:
        return self._last_token_usage

    def plan(
        self,
        request: QueryPlannerRequest,
        *,
        timeout_seconds: float = 8.0,
    ) -> QueryPlannerDecision:
        if not isinstance(request, QueryPlannerRequest):
            raise TypeError("request must use QueryPlannerRequest")
        self._last_token_usage = TokenUsage(0, 0, 0)
        response = self._client.complete(
            StructuredLLMRequest(
                decision_kind="query_plan",
                schema_name=QUERY_PLANNER_SCHEMA_NAME,
                payload=_planner_payload(request),
            ),
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(response, StructuredLLMResponse):
            raise TypeError("client must return StructuredLLMResponse")
        self._last_token_usage = response.token_usage
        return QueryPlannerDecision.from_payload(_response_payload(response))


class StructuredCompanyCandidateRanker(CompanyCandidateRanker):
    """Adapts a structured client to rank only existing candidate evidence."""

    def __init__(self, client: LLMReasoningClient) -> None:
        self._client = _require_client(client)
        self._last_token_usage = TokenUsage(0, 0, 0)

    @property
    def last_token_usage(self) -> TokenUsage:
        return self._last_token_usage

    def rank(
        self,
        request: CandidateRankerRequest,
        *,
        timeout_seconds: float = 8.0,
    ) -> CandidateRankerDecision:
        if not isinstance(request, CandidateRankerRequest):
            raise TypeError("request must use CandidateRankerRequest")
        self._last_token_usage = TokenUsage(0, 0, 0)
        response = self._client.complete(
            StructuredLLMRequest(
                decision_kind="candidate_rank",
                schema_name=CANDIDATE_RANKER_SCHEMA_NAME,
                payload=_ranker_payload(request),
            ),
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(response, StructuredLLMResponse):
            raise TypeError("client must return StructuredLLMResponse")
        self._last_token_usage = response.token_usage
        return CandidateRankerDecision.from_payload(_response_payload(response), request)


def _require_client(client: LLMReasoningClient) -> LLMReasoningClient:
    if not isinstance(client, LLMReasoningClient):
        raise TypeError("client must implement LLMReasoningClient")
    return client


def _response_payload(response: StructuredLLMResponse) -> Mapping[str, Any]:
    if not isinstance(response, StructuredLLMResponse):
        raise TypeError("client must return StructuredLLMResponse")
    return _thaw_json(response.payload)


def _thaw_json(value: Any) -> Any:
    """Turn the contract's immutable JSON tree back into parser input."""
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _planner_payload(request: QueryPlannerRequest) -> dict[str, object]:
    return {
        "schema_version": request.schema_version,
        "normalized_company_name": request.normalized_company_name,
        "linkedin_company_slug": request.linkedin_company_slug,
        "public_company_summary": request.public_company_summary,
        "job_title": request.job_title,
        "job_location": request.job_location,
        "industry": request.industry,
        "company_location": request.company_location,
        "rejected_candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "source": candidate.source,
                "rejection_reason": candidate.rejection_reason,
                "display_domain": candidate.display_domain,
            }
            for candidate in request.rejected_candidates
        ],
    }


def _ranker_payload(request: CandidateRankerRequest) -> dict[str, object]:
    return {
        "schema_version": request.schema_version,
        "normalized_company_name": request.normalized_company_name,
        "industry": request.industry,
        "company_location": request.company_location,
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "url": candidate.url,
                "title": candidate.title,
                "snippet": candidate.snippet,
                "source": candidate.source,
                "query_id": candidate.query_id,
                "rank": candidate.rank,
            }
            for candidate in request.candidates
        ],
        "context_evidence_ids": list(request.context_evidence_ids),
    }
