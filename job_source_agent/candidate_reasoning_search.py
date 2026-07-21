"""Search-backed evidence adapter for LLM-planned company queries."""

from __future__ import annotations

import hashlib

from .candidate_reasoning_contracts import CandidateEvidence, SearchQuerySpec
from .website_resolver import CompanyWebsiteResolver


class ResolverCandidateSearchBackend:
    """Run bounded queries through existing resolver search safety filters."""

    def __init__(
        self,
        resolver: CompanyWebsiteResolver,
        *,
        max_results_per_query: int = 5,
    ) -> None:
        if not isinstance(resolver, CompanyWebsiteResolver):
            raise TypeError("resolver must use CompanyWebsiteResolver")
        if (
            isinstance(max_results_per_query, bool)
            or not isinstance(max_results_per_query, int)
            or not 1 <= max_results_per_query <= 10
        ):
            raise ValueError("max_results_per_query must be between 1 and 10")
        self._resolver = resolver
        self._max_results = max_results_per_query

    def search(
        self,
        query: SearchQuerySpec,
        *,
        query_id: str,
        remaining_seconds: float,
    ) -> tuple[CandidateEvidence, ...]:
        if not isinstance(query, SearchQuerySpec):
            raise TypeError("query must use SearchQuerySpec")
        if remaining_seconds <= 0:
            raise TimeoutError("candidate reasoning deadline exhausted")
        evidence = self._resolver.search_bounded_candidate_query(
            query.query,
            max_results=self._max_results,
        )
        return tuple(
            CandidateEvidence(
                candidate_id=_candidate_id(query_id, item.url),
                url=item.url,
                title=item.title,
                snippet=item.snippet,
                source="resolver-search",
                query_id=query_id,
                rank=index,
            )
            for index, item in enumerate(evidence, start=1)
        )


def _candidate_id(query_id: str, url: str) -> str:
    digest = hashlib.sha256(f"{query_id}\n{url}".encode("utf-8")).hexdigest()[:20]
    return f"search-{digest}"
