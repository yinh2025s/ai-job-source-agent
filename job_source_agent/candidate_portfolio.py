from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable
from urllib.parse import urlsplit

from .job_board import DiscoveredJobBoard, JobBoardPortfolio
from .provider_candidates import (
    CandidateDiscovery,
    CandidateDiscoveryOutcome,
    CandidateDiscoveryRequest,
    CandidateDiscoveryStatus,
    ProviderCandidate,
    ProviderCandidatePool,
    STORED_PROVIDER_CANDIDATE_SOURCE_KINDS,
    VerifiedProviderCandidate,
)
from .contracts import FetchClient
from .fetch_failure import project_fetch_error
from .providers import (
    CandidateBootstrapProviderAdapter,
    JobQuery,
    ProviderRegistry,
)
from .web import FetchError


DIRECT_CANDIDATE_WAVE = "direct"
SEARCH_CANDIDATE_WAVE = "search"
_CANDIDATE_WAVES = (DIRECT_CANDIDATE_WAVE, SEARCH_CANDIDATE_WAVE)


@dataclass(frozen=True)
class CandidateDiscoverySourceOutcome:
    source: str
    wave: str
    outcome: CandidateDiscoveryOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("Candidate discovery source is invalid")
        if self.wave not in _CANDIDATE_WAVES:
            raise ValueError("Candidate discovery source wave is invalid")
        if not isinstance(self.outcome, CandidateDiscoveryOutcome):
            raise TypeError("Candidate discovery source outcome is invalid")


@dataclass(frozen=True)
class CandidateDiscoveryWaveResult:
    pool: ProviderCandidatePool
    outcome: CandidateDiscoveryOutcome
    source_outcomes: tuple[CandidateDiscoverySourceOutcome, ...]
    trace: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.pool, ProviderCandidatePool):
            raise TypeError("Candidate discovery wave pool is invalid")
        if not isinstance(self.outcome, CandidateDiscoveryOutcome):
            raise TypeError("Candidate discovery wave outcome is invalid")
        if not isinstance(self.source_outcomes, tuple):
            raise TypeError("Candidate discovery source outcomes must be immutable")
        if any(
            not isinstance(item, CandidateDiscoverySourceOutcome)
            for item in self.source_outcomes
        ):
            raise TypeError("Candidate discovery wave contains an invalid outcome")
        if bool(self.pool.candidates) != (
            self.outcome.status is CandidateDiscoveryStatus.CANDIDATES_PRODUCED
        ):
            raise ValueError("Candidate discovery wave outcome conflicts with its pool")
        if not isinstance(self.trace, dict):
            raise TypeError("Candidate discovery wave trace is invalid")

    def __iter__(self):
        """Preserve the historical pool/trace unpacking API."""
        yield self.pool
        yield self.trace

    def __getitem__(self, index: int):
        return (self.pool, self.trace)[index]


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

    def discover(self, request: CandidateDiscoveryRequest) -> CandidateDiscoveryWaveResult:
        return self._discover(request, wave=None)

    def discover_wave(
        self,
        request: CandidateDiscoveryRequest,
        wave: str,
    ) -> CandidateDiscoveryWaveResult:
        """Run one discovery wave while reporting every deferred/skipped source."""
        if wave not in _CANDIDATE_WAVES:
            raise ValueError("Candidate discovery wave is invalid")
        return self._discover(request, wave=wave)

    def _discover(
        self,
        request: CandidateDiscoveryRequest,
        *,
        wave: str | None,
    ) -> CandidateDiscoveryWaveResult:
        candidates: list[ProviderCandidate] = []
        source_traces: list[dict[str, Any]] = []
        source_outcomes: list[CandidateDiscoverySourceOutcome] = []
        for discovery in self._discoveries:
            source_name = type(discovery).__name__
            source_wave = getattr(discovery, "candidate_wave", DIRECT_CANDIDATE_WAVE)
            if source_wave not in _CANDIDATE_WAVES:
                raise ValueError(f"Invalid candidate wave for {source_name}")
            if wave is not None and source_wave != wave:
                status = (
                    "deferred"
                    if wave == DIRECT_CANDIDATE_WAVE
                    and source_wave == SEARCH_CANDIDATE_WAVE
                    else "skipped"
                )
                source_traces.append(
                    {
                        "source": source_name,
                        "wave": source_wave,
                        "status": status,
                        "reason": (
                            "awaiting_direct_verification"
                            if status == "deferred"
                            else "direct_wave_complete"
                        ),
                    }
                )
                source_outcomes.append(
                    CandidateDiscoverySourceOutcome(
                        source_name,
                        source_wave,
                        CandidateDiscoveryOutcome(
                            CandidateDiscoveryStatus.NOT_APPLICABLE
                        ),
                    )
                )
                continue
            try:
                result = discovery.discover(request)
            except Exception as exc:
                outcome = _exception_outcome(exc)
                source_traces.append(
                    {
                        "source": source_name,
                        "wave": source_wave,
                        **outcome.to_trace_payload(),
                        "error_type": type(exc).__name__,
                    }
                )
                source_outcomes.append(
                    CandidateDiscoverySourceOutcome(
                        source_name,
                        source_wave,
                        outcome,
                    )
                )
                continue
            candidates.extend(result.candidates)
            outcome = result.outcome
            assert outcome is not None
            source_traces.append(
                {
                    "source": source_name,
                    "wave": source_wave,
                    **outcome.to_trace_payload(),
                    "candidate_count": len(result.candidates),
                    "trace": result.trace,
                }
            )
            source_outcomes.append(
                CandidateDiscoverySourceOutcome(
                    source_name,
                    source_wave,
                    outcome,
                )
            )
        pool = ProviderCandidatePool.build(candidates, limit=self._limit)
        aggregate = _aggregate_wave_outcome(pool, source_outcomes)
        trace = {
            "wave": wave or "all",
            "outcome": aggregate.to_trace_payload(),
            "sources": source_traces,
            "pool": pool.to_trace_payload(),
        }
        return CandidateDiscoveryWaveResult(
            pool,
            aggregate,
            tuple(source_outcomes),
            trace,
        )


def _exception_outcome(error: Exception) -> CandidateDiscoveryOutcome:
    if isinstance(error, FetchError):
        failure = project_fetch_error(error)
        if failure["retryable"]:
            return CandidateDiscoveryOutcome(
                CandidateDiscoveryStatus.SOURCE_FAILED,
                failure["reason_code"],
                True,
            )
        return CandidateDiscoveryOutcome(
            CandidateDiscoveryStatus.SOURCE_REJECTED,
            failure["reason_code"],
            False,
        )
    if isinstance(error, (TimeoutError, OSError)):
        return CandidateDiscoveryOutcome(
            CandidateDiscoveryStatus.SOURCE_FAILED,
            "FETCH_FAILED",
            True,
        )
    if isinstance(error, (TypeError, ValueError)):
        return CandidateDiscoveryOutcome(
            CandidateDiscoveryStatus.SOURCE_REJECTED,
            "PARSING_FAILED",
            False,
        )
    return CandidateDiscoveryOutcome(
        CandidateDiscoveryStatus.SOURCE_FAILED,
        "FETCH_FAILED",
        True,
    )


def _aggregate_wave_outcome(
    pool: ProviderCandidatePool,
    source_outcomes: list[CandidateDiscoverySourceOutcome],
) -> CandidateDiscoveryOutcome:
    if pool.candidates:
        return CandidateDiscoveryOutcome(
            CandidateDiscoveryStatus.CANDIDATES_PRODUCED
        )
    outcomes = [item.outcome for item in source_outcomes]
    for status in (
        CandidateDiscoveryStatus.BUDGET_EXHAUSTED,
        CandidateDiscoveryStatus.SOURCE_FAILED,
        CandidateDiscoveryStatus.SOURCE_REJECTED,
        CandidateDiscoveryStatus.CANDIDATE_REJECTED,
        CandidateDiscoveryStatus.COMPLETED_EMPTY,
    ):
        selected = next(
            (outcome for outcome in outcomes if outcome.status is status),
            None,
        )
        if selected is not None:
            return selected
    return CandidateDiscoveryOutcome(CandidateDiscoveryStatus.NOT_APPLICABLE)


class ProviderCandidatePortfolioBuilder:
    """Identify provider boards; this does not authorize a hiring relationship."""

    def __init__(
        self,
        registry: ProviderRegistry,
        fetcher: FetchClient | None = None,
    ) -> None:
        self._registry = registry
        self._fetcher = fetcher

    def build(
        self,
        pool: ProviderCandidatePool,
        *,
        portfolio_limit: int = 8,
    ) -> CandidatePortfolioResult:
        verified: list[VerifiedProviderCandidate] = []
        rejected: list[dict[str, Any]] = []
        bootstrap_attempts: list[dict[str, Any]] = []
        seen_boards: set[tuple[str, str]] = set()
        for candidate in pool.candidates:
            adapter = self._registry.adapter_for(candidate.url)
            board = adapter.identify_board(candidate.url) if adapter is not None else None
            bootstrapped = False
            if (
                adapter is not None
                and board is None
                and self._fetcher is not None
                and isinstance(adapter, CandidateBootstrapProviderAdapter)
                and candidate.source_kind == "targeted_opening_search"
            ):
                try:
                    bootstrap = adapter.bootstrap_candidate(
                        self._fetcher,
                        candidate.url,
                        JobQuery(
                            title=candidate.target_title,
                            location=candidate.target_location,
                        ),
                    )
                except (FetchError, OSError, TimeoutError, TypeError, ValueError) as exc:
                    bootstrap_attempts.append(
                        {
                            "url": candidate.url,
                            "provider": adapter.name,
                            "status": "failed",
                            "error_type": type(exc).__name__,
                        }
                    )
                    bootstrap = None
                if bootstrap is not None and bootstrap.board.provider == adapter.name:
                    board = bootstrap.board
                    candidate = replace(
                        candidate,
                        provider_hint=adapter.name,
                        provider_employer_evidence=bootstrap.employer_evidence,
                    )
                    bootstrapped = True
                    bootstrap_attempts.append(
                        {
                            "url": candidate.url,
                            "provider": adapter.name,
                            "status": "verified",
                            "board_url": board.url,
                        }
                    )
                elif not bootstrap_attempts or bootstrap_attempts[-1].get("url") != candidate.url:
                    bootstrap_attempts.append(
                        {
                            "url": candidate.url,
                            "provider": adapter.name,
                            "status": "rejected",
                        }
                    )
            if adapter is None or board is None or not adapter.supports_listing:
                rejected.append(
                    {
                        "url": candidate.url,
                        "source_kind": candidate.source_kind,
                        "reason": "provider_not_listable",
                    }
                )
                continue
            canonicalize_board = getattr(adapter, "canonicalize_board", None)
            if callable(canonicalize_board):
                board = canonicalize_board(board)
            identity = (board.provider, board.url.rstrip("/").casefold())
            if identity in seen_boards:
                continue
            seen_boards.add(identity)
            detection_method = (
                "provider_opening_bootstrap"
                if bootstrapped
                else (
                    "external_apply_url"
                    if candidate.source_kind == "external_apply"
                    else (
                        "verified_tenant_probe"
                        if candidate.source_kind == "verified_tenant_probe"
                        else (
                            "targeted_search"
                            if candidate.source_kind.startswith("targeted_")
                            else "linked_url_evidence"
                        )
                    )
                )
            )
            candidate_origin = urlsplit(candidate.url).netloc.casefold()
            board_origin = urlsplit(board.url).netloc.casefold()
            relationship_evidence_url = (
                None
                if candidate.source_kind == "verified_tenant_probe"
                else (
                    candidate.source_url
                    if candidate.source_url != candidate.url
                    else candidate.url
                    if candidate_origin != board_origin
                    else None
                )
            )
            discovered = DiscoveredJobBoard(
                board=board,
                detection_method=detection_method,
                evidence_url=(candidate.url if candidate_origin == board_origin else board.url),
                relationship_evidence_url=relationship_evidence_url,
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
                        or item.candidate.source_kind
                        in STORED_PROVIDER_CANDIDATE_SOURCE_KINDS
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
                "bootstrap_attempts": bootstrap_attempts,
            },
        )
