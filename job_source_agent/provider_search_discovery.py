from __future__ import annotations

from typing import Any

from .career_search import CareerSearchResolver
from .provider_candidates import (
    MAX_PROVIDER_CANDIDATES,
    CandidateDiscovery,
    CandidateDiscoveryRequest,
    CandidateDiscoveryResult,
    ProviderCandidate,
    ProviderCandidatePool,
)
from .providers import DEFAULT_PROVIDER_REGISTRY, ProviderRegistry
from .scoring import is_likely_job_detail


class ProviderSearchCandidateDiscovery(CandidateDiscovery):
    """Turn ATS-only search links into untrusted provider candidate leads."""

    def __init__(
        self,
        resolver: CareerSearchResolver,
        *,
        provider_registry: ProviderRegistry = DEFAULT_PROVIDER_REGISTRY,
        max_candidates: int = MAX_PROVIDER_CANDIDATES,
    ) -> None:
        if (
            isinstance(max_candidates, bool)
            or not isinstance(max_candidates, int)
            or not 1 <= max_candidates <= MAX_PROVIDER_CANDIDATES
        ):
            raise ValueError("Provider search candidate limit is invalid")
        self.resolver = resolver
        self.provider_registry = provider_registry
        self.max_candidates = max_candidates

    def discover(self, request: CandidateDiscoveryRequest) -> CandidateDiscoveryResult:
        search_result = self.resolver.search(
            request.company_name,
            request.company_website_url or "",
            target_title=request.target_title,
            ats_only=True,
            exhaustive=True,
        )
        query_by_url = _query_by_url(search_result.trace)
        candidates: list[ProviderCandidate] = []
        skipped_count = 0

        for result_rank, link in enumerate(search_result.candidates, start=1):
            query = query_by_url.get(link.url)
            if query is None:
                skipped_count += 1
                continue
            provider_hint = self.provider_registry.detect(link.url)
            try:
                candidates.append(
                    ProviderCandidate(
                        url=link.url,
                        source_kind=(
                            "targeted_opening_search"
                            if is_likely_job_detail(link)
                            else "targeted_board_search"
                        ),
                        source_url=link.source_url,
                        company_name=request.company_name,
                        target_title=request.target_title,
                        target_location=request.target_location,
                        provider_hint=(
                            None if provider_hint == "generic" else provider_hint
                        ),
                        query=query,
                        result_rank=result_rank,
                    )
                )
            except (TypeError, ValueError):
                # Search results are leads only; malformed or non-public URLs
                # cannot enter the candidate contract.
                skipped_count += 1

        pool = ProviderCandidatePool.build(candidates, limit=self.max_candidates)
        return CandidateDiscoveryResult(
            candidates=pool.candidates,
            trace={
                "source": "provider_targeted_search",
                "search": search_result.trace,
                "candidate_count": len(pool.candidates),
                "truncated": pool.truncated,
                "skipped_candidate_count": skipped_count,
            },
        )


def _query_by_url(trace: dict[str, Any]) -> dict[str, str]:
    queries = trace.get("queries")
    if not isinstance(queries, list):
        return {}
    values: dict[str, str] = {}
    for query_trace in queries:
        if not isinstance(query_trace, dict):
            continue
        query = query_trace.get("query")
        candidates = query_trace.get("candidates")
        if not isinstance(query, str) or not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if isinstance(candidate, dict) and isinstance(candidate.get("url"), str):
                values.setdefault(candidate["url"], query)
    return values
