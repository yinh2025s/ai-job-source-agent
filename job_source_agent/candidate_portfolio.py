from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .job_board import DiscoveredJobBoard, JobBoardPortfolio
from .provider_candidates import (
    CandidateDiscovery,
    CandidateDiscoveryRequest,
    ProviderCandidate,
    ProviderCandidatePool,
    VerifiedProviderCandidate,
)
from .providers import ProviderRegistry


@dataclass(frozen=True)
class CandidatePortfolioResult:
    pool: ProviderCandidatePool
    verified: tuple[VerifiedProviderCandidate, ...]
    portfolio: JobBoardPortfolio | None
    trace: dict[str, Any]


class CompositeCandidateDiscovery:
    """Merge independent lead sources before any provider verification."""

    def __init__(
        self,
        discoveries: Iterable[CandidateDiscovery],
        *,
        limit: int,
    ) -> None:
        self._discoveries = tuple(discoveries)
        self._limit = limit

    def discover(self, request: CandidateDiscoveryRequest) -> tuple[ProviderCandidatePool, dict]:
        candidates: list[ProviderCandidate] = []
        source_traces: list[dict[str, Any]] = []
        for discovery in self._discoveries:
            source_name = type(discovery).__name__
            try:
                result = discovery.discover(request)
            except Exception as exc:
                source_traces.append(
                    {
                        "source": source_name,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                    }
                )
                continue
            candidates.extend(result.candidates)
            source_traces.append(
                {
                    "source": source_name,
                    "status": "success",
                    "candidate_count": len(result.candidates),
                    "trace": result.trace,
                }
            )
        pool = ProviderCandidatePool.build(candidates, limit=self._limit)
        return pool, {
            "sources": source_traces,
            "pool": pool.to_trace_payload(),
        }


class ProviderCandidatePortfolioBuilder:
    """Identify provider boards; this does not authorize a hiring relationship."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    def build(
        self,
        pool: ProviderCandidatePool,
        *,
        portfolio_limit: int = 8,
    ) -> CandidatePortfolioResult:
        verified: list[VerifiedProviderCandidate] = []
        rejected: list[dict[str, Any]] = []
        seen_boards: set[tuple[str, str]] = set()
        for candidate in pool.candidates:
            adapter = self._registry.adapter_for(candidate.url)
            board = adapter.identify_board(candidate.url) if adapter is not None else None
            if adapter is None or board is None or not adapter.supports_listing:
                rejected.append(
                    {
                        "url": candidate.url,
                        "source_kind": candidate.source_kind,
                        "reason": "provider_not_listable",
                    }
                )
                continue
            identity = (board.provider, board.url.rstrip("/").casefold())
            if identity in seen_boards:
                continue
            seen_boards.add(identity)
            detection_method = (
                "external_apply_url"
                if candidate.source_kind == "external_apply"
                else "targeted_search"
                if candidate.source_kind.startswith("targeted_")
                else "linked_url_evidence"
            )
            discovered = DiscoveredJobBoard(
                board=board,
                detection_method=detection_method,
                evidence_url=candidate.url,
            )
            try:
                verified.append(VerifiedProviderCandidate(candidate, discovered))
            except (TypeError, ValueError):
                rejected.append(
                    {
                        "url": candidate.url,
                        "source_kind": candidate.source_kind,
                        "reason": "provider_hint_conflict",
                    }
                )

        truncated = len(verified) > portfolio_limit
        selected = tuple(verified[:portfolio_limit])
        portfolio = (
            JobBoardPortfolio(
                boards=tuple(item.discovered_board for item in selected),
                eligible_set_complete=(
                    not pool.truncated
                    and not truncated
                    and not any(
                        item.candidate.source_kind.startswith("targeted_")
                        for item in selected
                    )
                ),
            )
            if selected
            else None
        )
        return CandidatePortfolioResult(
            pool=pool,
            verified=selected,
            portfolio=portfolio,
            trace={
                "verified_candidate_count": len(selected),
                "rejected_candidates": rejected,
                "portfolio_truncated": truncated,
            },
        )
