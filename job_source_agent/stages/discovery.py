from __future__ import annotations

import time
import re
from typing import Protocol
from urllib.parse import urlsplit

from ..contracts import PipelineContext, StageExecution
from ..errors import DiscoveryError
from ..homepage_navigation import HomepageNavigationEvidence
from ..identity_continuity import OpeningIdentity, ProviderIdentity
from ..job_board import DiscoveredJobBoard, JobBoard, JobBoardPortfolio
from ..models import (
    STAGE_CAREER_DISCOVERY,
    STAGE_HIRING_IDENTITY_RESOLUTION,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
)
from ..opening_availability import diagnose_opening_availability
from ..providers import DEFAULT_PROVIDER_REGISTRY, ProviderRegistry
from ..reasons import canonical_reason_code, make_stage_result
from ..result_identity import canonicalize_identity_url, tenant_locator
from ..source_posting import trusted_linkedin_native_posting
from ..web import FetchError, normalize_url
from ..fetch_failure import project_fetch_error


class CareerDiscoveryService(Protocol):
    def find_career_page(
        self,
        company_website_url: str,
        company_name: str | None = None,
        preferred_url: str | None = None,
        target_title: str | None = None,
        target_location: str | None = None,
        homepage_navigation_evidence: HomepageNavigationEvidence | None = None,
    ) -> tuple[str, dict]:
        ...


class JobBoardDiscoveryService(Protocol):
    def find_job_board(
        self,
        career_page_url: str,
        company_name: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str, dict]:
        ...

    def find_job_board_with_evidence(
        self,
        career_page_url: str,
        company_name: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str, dict, DiscoveredJobBoard | None]:
        ...

    def find_job_board_portfolio(
        self,
        career_page_url: str,
        company_name: str | None = None,
        target_title: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str, dict, JobBoardPortfolio | None]:
        ...


class OpeningMatchService(Protocol):
    def match_opening(
        self,
        job_list_url: str,
        target_title: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str | None, str, dict]:
        ...

    def match_discovered_board(
        self,
        discovered_board: DiscoveredJobBoard,
        target_title: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str | None, str, dict]:
        ...


class CareerDiscoveryStage:
    name = STAGE_CAREER_DISCOVERY

    def __init__(self, service: CareerDiscoveryService) -> None:
        self.service = service

    def run(self, context: PipelineContext) -> StageExecution:
        if _upstream_stage_failed(context, STAGE_HIRING_IDENTITY_RESOLUTION):
            return StageExecution(
                make_stage_result(
                    self.name,
                    "not_run",
                    detail=(
                        "Hiring identity resolution did not produce a safe hiring entity."
                    ),
                ),
                trace={
                    "scheduler": {
                        "status": "not_run",
                        "reason": "hiring_identity_unresolved",
                    }
                },
            )
        if not context.company_website_url:
            return StageExecution(
                make_stage_result(
                    self.name,
                    "not_run",
                    detail="Website resolution did not produce an input.",
                )
            )

        started = time.perf_counter()
        try:
            replay_trace = context.company.source_trace.get("replay")
            replay_root = context.company.source == "replay_input" or isinstance(replay_trace, dict)
            trusted_identity_root = _identity_stage_resolved_career_root(context)
            if context.career_root_url and (not replay_root or trusted_identity_root):
                career_url = normalize_url(context.career_root_url)
                trace = {
                    "homepage_url": context.company_website_url,
                    "selected": {
                        "url": career_url,
                        "reason": "trusted direct-input or identity career root",
                    },
                    "preferred_root_validation": "trusted_provenance",
                }
                detail = "Career root supplied by a trusted direct input or identity rule."
            else:
                find_kwargs = {
                    "company_name": context.hiring_entity_name or context.company.company_name,
                    "preferred_url": context.career_root_url,
                    "target_title": context.company.job_title,
                    "target_location": context.company.job_location,
                }
                if context.homepage_navigation_evidence is not None:
                    find_kwargs["homepage_navigation_evidence"] = (
                        context.homepage_navigation_evidence
                    )
                career_url, trace = self.service.find_career_page(
                    context.company_website_url,
                    **find_kwargs,
                )
                detail = (
                    "Replay career root was revalidated."
                    if context.career_root_url
                    and career_url.rstrip("/") == normalize_url(context.career_root_url).rstrip("/")
                    else None
                )
        except FetchError as exc:
            failure = project_fetch_error(exc)
            return _failed_execution(
                self.name,
                failure["reason_code"],
                started,
                str(exc),
                trace={"fetch_failure": failure},
            )
        except DiscoveryError as exc:
            return _failed_execution(
                self.name,
                canonical_reason_code(exc.code),
                started,
                str(exc),
                trace=exc.trace,
            )

        return StageExecution(
            result=make_stage_result(
                self.name,
                "success",
                duration_ms=_elapsed_ms(started),
                input_count=1,
                output_count=1,
                evidence=[{"field": "career_page_url", "url": career_url}],
                detail=detail,
            ),
            updates={"career_page_url": career_url},
            trace=trace,
        )


class JobBoardDiscoveryStage:
    name = STAGE_JOB_BOARD_DISCOVERY

    def __init__(
        self,
        service: JobBoardDiscoveryService,
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self.service = service
        self.provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY

    def run(self, context: PipelineContext) -> StageExecution:
        if not context.career_page_url:
            if context.company.external_apply_url:
                return self._from_external_apply(context)
            if self._career_path_is_definitively_missing(context):
                native_execution = self._from_linkedin_native_source(
                    context,
                    fallback_trace={"career_path": "definitively_missing"},
                )
                if native_execution is not None:
                    return native_execution
            return StageExecution(
                make_stage_result(
                    self.name,
                    "not_run",
                    detail="Career discovery did not produce an input.",
                )
            )

        started = time.perf_counter()
        try:
            find_portfolio = getattr(self.service, "find_job_board_portfolio", None)
            if callable(find_portfolio):
                job_list_url, trace, portfolio = find_portfolio(
                    context.career_page_url,
                    company_name=(
                        context.hiring_entity_name or context.company.company_name
                    ),
                    target_title=context.company.job_title,
                    target_location=context.company.job_location,
                )
                discovered_board = portfolio.primary if portfolio is not None else None
            else:
                portfolio = None
                find_with_evidence = getattr(
                    self.service,
                    "find_job_board_with_evidence",
                    None,
                )
            if not callable(find_portfolio) and callable(find_with_evidence):
                job_list_url, trace, discovered_board = find_with_evidence(
                    context.career_page_url,
                    company_name=context.hiring_entity_name or context.company.company_name,
                    target_location=context.company.job_location,
                )
            elif not callable(find_portfolio):
                job_list_url, trace = self.service.find_job_board(
                    context.career_page_url,
                    company_name=context.hiring_entity_name or context.company.company_name,
                    target_location=context.company.job_location,
                )
                discovered_board = None
        except FetchError as exc:
            failure = project_fetch_error(exc)
            if context.company.external_apply_url:
                return self._from_external_apply(
                    context,
                    fallback_trace={
                        "career_job_board_error": str(exc),
                        "career_job_board_failure": failure,
                    },
                )
            return _failed_execution(
                self.name,
                failure["reason_code"],
                started,
                str(exc),
                trace={"fetch_failure": failure},
            )
        except DiscoveryError as exc:
            if context.company.external_apply_url:
                return self._from_external_apply(
                    context,
                    fallback_trace={
                        "career_job_board_error": str(exc),
                        "career_job_board_trace": exc.trace,
                    },
                )
            reason_code = canonical_reason_code(exc.code)
            if reason_code == "JOB_BOARD_NOT_FOUND" and not _trace_has_discovery_errors(exc.trace):
                native_execution = self._from_linkedin_native_source(
                    context,
                    fallback_trace={
                        "career_job_board_error": str(exc),
                        "career_job_board_trace": exc.trace,
                    },
                )
                if native_execution is not None:
                    return native_execution
            return _failed_execution(
                self.name,
                reason_code,
                started,
                str(exc),
                trace=exc.trace,
            )

        provider = trace.get("provider") or self.provider_registry.detect(job_list_url)
        provider = None if provider == "generic" else provider
        updates = {"job_list_page_url": job_list_url, "provider": provider}
        if discovered_board is not None:
            updates["discovered_job_board"] = discovered_board
        updates["provider_identity"] = _provider_identity(
            context,
            job_list_url,
            discovered_board,
            self.provider_registry,
        )
        if (
            portfolio is not None
            and (
                len(portfolio.boards) > 1
                or not portfolio.eligible_set_complete
            )
        ):
            updates["job_board_portfolio"] = portfolio
        return StageExecution(
            result=make_stage_result(
                self.name,
                "success",
                provider=provider,
                duration_ms=_elapsed_ms(started),
                input_count=1,
                output_count=1,
                evidence=[{"field": "job_list_page_url", "url": job_list_url}],
            ),
            updates=updates,
            trace=trace,
        )

    def _from_linkedin_native_source(
        self,
        context: PipelineContext,
        *,
        fallback_trace: dict | None = None,
    ) -> StageExecution | None:
        posting = trusted_linkedin_native_posting(
            context.company.source_trace,
            expected_job_url=context.company.linkedin_job_url or None,
        )
        if posting is None:
            return None

        evidence = {
            "type": "source_posting_availability",
            "disposition": "linkedin_native_only",
            "availability": posting.availability,
            "apply_mode": posting.apply_mode,
            "evidence_source": posting.evidence_source,
            "source_posting_url": posting.job_url,
        }
        return StageExecution(
            result=make_stage_result(
                self.name,
                "partial",
                reason_code="LINKEDIN_NATIVE_ONLY",
                input_count=1,
                evidence=[evidence],
                detail=(
                    "The source posting is active and uses LinkedIn-native apply, while no "
                    "public company job board was verified."
                ),
            ),
            trace={
                "method": "source_posting_availability",
                **evidence,
                **(fallback_trace or {}),
            },
        )

    @staticmethod
    def _career_path_is_definitively_missing(context: PipelineContext) -> bool:
        for result in reversed(context.stage_results):
            if result.stage == STAGE_CAREER_DISCOVERY:
                return (
                    result.status == "failed"
                    and result.reason_code == "CAREER_PAGE_NOT_FOUND"
                    and not result.retryable
                )
        return False

    def _from_external_apply(
        self,
        context: PipelineContext,
        fallback_trace: dict | None = None,
    ) -> StageExecution:
        source_url = context.company.external_apply_url or ""
        try:
            source_url = normalize_url(source_url)
        except (TypeError, ValueError) as exc:
            return StageExecution(
                make_stage_result(
                    self.name,
                    "unsupported",
                    reason_code="PROVIDER_UNSUPPORTED",
                    input_count=1,
                    detail=f"External Apply URL is malformed: {exc}",
                ),
                trace={"method": "external_apply_url", "error": str(exc)},
            )

        adapter = self.provider_registry.adapter_for(source_url)
        board = adapter.identify_board(source_url) if adapter else None
        if adapter is None or board is None or not adapter.supports_listing:
            return StageExecution(
                make_stage_result(
                    self.name,
                    "unsupported",
                    reason_code="PROVIDER_UNSUPPORTED",
                    input_count=1,
                    evidence=[{"field": "external_apply_url", "url": source_url}],
                    detail="External Apply URL did not identify a supported native provider board.",
                ),
                trace={
                    "method": "external_apply_url",
                    "source_url": source_url,
                    "provider": adapter.name if adapter else None,
                    **(fallback_trace or {}),
                },
            )

        trace = {
            "method": "external_apply_url",
            "source_url": source_url,
            "job_list_page_url": board.url,
            "provider": adapter.name,
            "provider_detection": {
                "method": "external_apply_url",
                "provider": adapter.name,
                "url": board.url,
            },
            **(fallback_trace or {}),
        }
        discovered = DiscoveredJobBoard(
            board=board,
            detection_method="external_apply_url",
            evidence_url=source_url,
        )
        return StageExecution(
            result=make_stage_result(
                self.name,
                "success",
                provider=adapter.name,
                input_count=1,
                output_count=1,
                evidence=[
                    {"field": "external_apply_url", "url": source_url},
                    {"field": "job_list_page_url", "url": board.url},
                ],
                detail="Native provider board derived from the LinkedIn External Apply URL.",
            ),
            updates={
                "job_list_page_url": board.url,
                "provider": adapter.name,
                "discovered_job_board": discovered,
                "provider_identity": _provider_identity(
                    context,
                    board.url,
                    discovered,
                    self.provider_registry,
                ),
            },
            trace=trace,
        )


class OpeningMatchStage:
    name = STAGE_OPENING_MATCH

    def __init__(
        self,
        service: OpeningMatchService,
        provider_registry: ProviderRegistry | None = None,
        max_job_board_attempts: int = 1,
    ) -> None:
        self.service = service
        self.provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY
        if not isinstance(max_job_board_attempts, int) or isinstance(
            max_job_board_attempts, bool
        ) or not 1 <= max_job_board_attempts <= 8:
            raise ValueError("max_job_board_attempts must be between one and eight")
        self.max_job_board_attempts = max_job_board_attempts

    def run(self, context: PipelineContext) -> StageExecution:
        if not context.job_list_page_url:
            return StageExecution(
                make_stage_result(
                    self.name,
                    "not_run",
                    detail="Job-board discovery did not produce an input.",
                )
            )
        if (
            context.job_board_portfolio is not None
            and (
                len(context.job_board_portfolio.boards) > 1
                or not context.job_board_portfolio.eligible_set_complete
            )
        ):
            return self._run_portfolio(context)
        started = time.perf_counter()
        try:
            match_discovered = getattr(self.service, "match_discovered_board", None)
            if context.discovered_job_board is not None and callable(match_discovered):
                opening_url, job_list_url, trace = match_discovered(
                    context.discovered_job_board,
                    context.company.job_title,
                    context.company.job_location,
                )
            else:
                opening_url, job_list_url, trace = self.service.match_opening(
                    context.job_list_page_url,
                    context.company.job_title,
                    context.company.job_location,
                )
        except FetchError as exc:
            failure = project_fetch_error(exc)
            return _failed_execution(
                self.name,
                failure["reason_code"],
                started,
                str(exc),
                trace={"fetch_failure": failure},
            )
        except DiscoveryError as exc:
            return _failed_execution(
                self.name,
                canonical_reason_code(exc.code),
                started,
                str(exc),
                trace=exc.trace,
            )

        updates = {"job_list_page_url": job_list_url}
        if opening_url:
            updates["open_position_url"] = opening_url
            opening_identity = _opening_identity(
                context,
                opening_url,
                self.provider_registry,
            )
            if opening_identity is not None:
                updates["opening_identity"] = opening_identity
            return StageExecution(
                result=make_stage_result(
                    self.name,
                    "success",
                    provider=(
                        self.provider_registry.detect(opening_url)
                        if self.provider_registry.detect(opening_url) != "generic"
                        else context.provider
                    ),
                    duration_ms=_elapsed_ms(started),
                    input_count=1,
                    output_count=1,
                    evidence=[{"field": "open_position_url", "url": opening_url}],
                ),
                updates=updates,
                trace=trace,
            )

        if not context.company.job_title:
            return StageExecution(
                make_stage_result(
                    self.name,
                    "not_applicable",
                    provider=context.provider,
                    duration_ms=_elapsed_ms(started),
                    input_count=1,
                    detail="No target title was provided; job-board discovery was the requested outcome.",
                ),
                updates=updates,
                trace=trace,
            )

        diagnostic = diagnose_opening_availability(trace, context.company.source_trace)
        trace["availability_diagnostic"] = {
            "disposition": diagnostic.disposition,
            "confidence": diagnostic.confidence,
            "reason_code": diagnostic.reason_code,
            **diagnostic.evidence,
        }
        return StageExecution(
            result=make_stage_result(
                self.name,
                "partial",
                reason_code=diagnostic.reason_code,
                provider=context.provider,
                duration_ms=_elapsed_ms(started),
                input_count=1,
                evidence=[
                    {
                        "type": "availability_diagnostic",
                        "disposition": diagnostic.disposition,
                        "confidence": diagnostic.confidence,
                        **diagnostic.evidence,
                    }
                ],
                detail=diagnostic.detail,
            ),
            updates=updates,
            trace=trace,
        )

    def _run_portfolio(self, context: PipelineContext) -> StageExecution:
        portfolio = context.job_board_portfolio
        assert portfolio is not None
        started = time.perf_counter()
        attempts: list[dict] = []
        diagnostics = []
        match_discovered = getattr(self.service, "match_discovered_board", None)
        for position, discovered in enumerate(
            portfolio.boards[: self.max_job_board_attempts]
        ):
            board = discovered.board
            try:
                if callable(match_discovered):
                    opening_url, job_list_url, trace = match_discovered(
                        discovered,
                        context.company.job_title,
                        context.company.job_location,
                    )
                else:
                    opening_url, job_list_url, trace = self.service.match_opening(
                        board.url,
                        context.company.job_title,
                        context.company.job_location,
                    )
            except FetchError as exc:
                failure = project_fetch_error(exc)
                reason_code = failure["reason_code"]
                attempts.append(
                    {
                        "position": position,
                        "provider": board.provider,
                        "board_url": board.url,
                        "status": "incomplete",
                        "reason_code": reason_code,
                        "fetch_failure": failure,
                    }
                )
                diagnostics.append((reason_code, None))
                continue
            except DiscoveryError as exc:
                reason_code = canonical_reason_code(exc.code)
                attempts.append(
                    {
                        "position": position,
                        "provider": board.provider,
                        "board_url": board.url,
                        "status": "incomplete",
                        "reason_code": reason_code,
                        "trace": exc.trace,
                    }
                )
                diagnostics.append((reason_code, None))
                continue

            if opening_url:
                attempts.append(
                    {
                        "position": position,
                        "provider": board.provider,
                        "board_url": job_list_url,
                        "status": "exact",
                        "trace": trace,
                    }
                )
                portfolio_trace = self._portfolio_trace(portfolio, attempts, "exact")
                return StageExecution(
                    result=make_stage_result(
                        self.name,
                        "success",
                        provider=board.provider,
                        duration_ms=_elapsed_ms(started),
                        input_count=len(attempts),
                        output_count=1,
                        evidence=[{"field": "open_position_url", "url": opening_url}],
                    ),
                    updates={
                        "job_list_page_url": job_list_url,
                        "discovered_job_board": discovered,
                        "provider": board.provider,
                        "open_position_url": opening_url,
                        **_opening_identity_update(
                            context,
                            opening_url,
                            self.provider_registry,
                        ),
                    },
                    trace=portfolio_trace,
                )

            diagnostic = diagnose_opening_availability(
                trace,
                context.company.source_trace,
            )
            diagnostics.append((diagnostic.reason_code, diagnostic))
            attempts.append(
                {
                    "position": position,
                    "provider": board.provider,
                    "board_url": job_list_url,
                    "status": diagnostic.disposition,
                    "reason_code": diagnostic.reason_code,
                    "trace": trace,
                }
            )

        attempted_all = len(attempts) == len(portfolio.boards)
        portfolio_complete = portfolio.eligible_set_complete and attempted_all
        incomplete = next(
            (
                (reason_code, diagnostic)
                for reason_code, diagnostic in diagnostics
                if reason_code
                not in {
                    "OPENING_DISCOVERY_INCOMPLETE",
                    "OPENING_NOT_FOUND",
                    "NO_PUBLIC_OPENINGS",
                }
            ),
            None,
        )
        if incomplete is not None:
            reason_code, diagnostic = incomplete
            detail = (
                diagnostic.detail
                if diagnostic is not None
                else "A verified job board could not be checked conclusively."
            )
        elif not portfolio_complete:
            reason_code = "JOB_BOARD_PORTFOLIO_INCOMPLETE"
            detail = (
                "Eligible job boards remain unattempted or the bounded portfolio was "
                "truncated; company-wide opening absence is not established."
            )
        elif any(
            reason_code == "OPENING_DISCOVERY_INCOMPLETE"
            for reason_code, _diagnostic in diagnostics
        ):
            reason_code = "OPENING_DISCOVERY_INCOMPLETE"
            detail = (
                "Every eligible job board was attempted, but at least one inventory "
                "could not be verified as complete."
            )
        elif diagnostics and all(
            reason_code == "NO_PUBLIC_OPENINGS"
            for reason_code, _diagnostic in diagnostics
        ):
            reason_code = "NO_PUBLIC_OPENINGS"
            detail = "Every eligible verified job board returned a complete empty inventory."
        else:
            reason_code = "OPENING_NOT_FOUND"
            detail = (
                "Every eligible verified job board was checked completely, but no title "
                "met the match threshold."
            )

        trace = self._portfolio_trace(portfolio, attempts, "no_exact")
        return StageExecution(
            result=make_stage_result(
                self.name,
                "partial",
                reason_code=reason_code,
                provider=context.provider,
                duration_ms=_elapsed_ms(started),
                input_count=len(attempts),
                evidence=[
                    {
                        "type": "job_board_portfolio",
                        "attempted_count": len(attempts),
                        "eligible_count": len(portfolio.boards),
                        "eligible_set_complete": portfolio.eligible_set_complete,
                    }
                ],
                detail=detail,
            ),
            updates={"job_list_page_url": portfolio.primary.board.url},
            trace=trace,
        )

    @staticmethod
    def _portfolio_trace(
        portfolio: JobBoardPortfolio,
        attempts: list[dict],
        stopped_reason: str,
    ) -> dict:
        return {
            "board_portfolio": {
                "eligible_count": len(portfolio.boards),
                "eligible_set_complete": portfolio.eligible_set_complete,
                "attempted_count": len(attempts),
                "unattempted_count": max(0, len(portfolio.boards) - len(attempts)),
                "stopped_reason": stopped_reason,
                "attempts": attempts,
            }
        }


def _failed_execution(
    stage: str,
    reason_code: str,
    started: float,
    detail: str,
    trace: dict | None = None,
) -> StageExecution:
    return StageExecution(
        result=make_stage_result(
            stage,
            "failed",
            reason_code=reason_code,
            duration_ms=_elapsed_ms(started),
            input_count=1,
            detail=detail,
        ),
        trace=trace or {"error": detail},
    )


def _trace_has_discovery_errors(value: object, key: str = "") -> bool:
    """Keep source-channel classification from hiding incomplete network/provider work."""

    normalized_key = key.lower()
    if normalized_key.endswith("_error") and value not in (None, "", [], {}):
        return True
    if normalized_key.endswith("_errors") and value not in (None, "", [], {}):
        return True
    if isinstance(value, dict):
        return any(_trace_has_discovery_errors(item, str(name)) for name, item in value.items())
    if isinstance(value, list):
        return any(_trace_has_discovery_errors(item) for item in value)
    return False


def _upstream_stage_failed(context: PipelineContext, stage: str) -> bool:
    return any(
        result.stage == stage and result.status in {"failed", "unsupported"}
        for result in context.stage_results
    )


def _identity_stage_resolved_career_root(context: PipelineContext) -> bool:
    identity_results = [
        result
        for result in context.stage_results
        if result.stage == STAGE_HIRING_IDENTITY_RESOLUTION
    ]
    if len(identity_results) != 1 or identity_results[0].status != "success":
        return False
    if not context.career_root_url or not isinstance(identity_results[0].evidence, list):
        return False

    stage_trace = context.trace.get("stages", {}).get(
        STAGE_HIRING_IDENTITY_RESOLUTION
    )
    selected = stage_trace.get("selected") if isinstance(stage_trace, dict) else None
    selected_root = (
        selected.get("career_root_url") if isinstance(selected, dict) else None
    )
    if not isinstance(selected_root, str):
        return False

    root_evidence = []
    for item in identity_results[0].evidence:
        if not isinstance(item, dict):
            return False
        if item.get("field") == "career_root_url":
            if set(item) != {"field", "url"} or not isinstance(item["url"], str):
                return False
            root_evidence.append(item["url"])

    if len(root_evidence) != 1:
        return False
    try:
        normalized_root = normalize_url(context.career_root_url)
        return (
            normalize_url(root_evidence[0]) == normalized_root
            and normalize_url(selected_root) == normalized_root
        )
    except (TypeError, ValueError):
        return False


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _provider_identity(
    context: PipelineContext,
    job_list_url: str,
    discovered: DiscoveredJobBoard | None,
    registry: ProviderRegistry,
) -> ProviderIdentity:
    board = discovered.board if discovered is not None else None
    adapter = registry.adapter_for(job_list_url)
    if board is None and adapter is not None:
        board = adapter.identify_board(job_list_url)
    if board is None:
        canonical_board = canonicalize_identity_url(job_list_url)
        provider = "generic"
        tenant = tenant_locator(canonical_board)
        evidence_url = context.career_page_url or canonical_board
    else:
        canonical_board = canonicalize_identity_url(board.url)
        provider = board.provider
        tenant = board.identifier or tenant_locator(canonical_board)
        evidence_url = discovered.evidence_url if discovered is not None else job_list_url
    verified, method = _authorize_provider_board(
        context,
        provider,
        tenant,
        canonical_board,
    )
    return ProviderIdentity(
        hiring_entity_name=(
            context.hiring_identity_evidence.hiring_entity_name
            if context.hiring_identity_evidence is not None
            else context.hiring_entity_name or context.company.company_name
        ),
        provider=provider,
        tenant=tenant,
        canonical_board_url=canonical_board,
        evidence_url=canonicalize_identity_url(evidence_url),
        verification_method=method,
        relationship_verified=verified,
    )


def _authorize_provider_board(
    context: PipelineContext,
    provider: str,
    tenant: str,
    canonical_board: str,
) -> tuple[bool, str]:
    hiring = context.hiring_identity_evidence
    if hiring is None or not hiring.verified:
        return False, "linked_url_only"
    if context.career_root_url and _same_url(context.career_root_url, canonical_board):
        return True, "identity_career_root"
    if _identity_aliases(hiring.hiring_entity_name) & _identity_aliases(tenant):
        return True, "tenant_name_match"
    if provider == "generic" and context.career_page_url and _same_site(
        context.career_page_url, canonical_board
    ):
        return True, "first_party_same_site"
    return False, "linked_url_only"


def _opening_identity(
    context: PipelineContext,
    opening_url: str,
    registry: ProviderRegistry,
) -> OpeningIdentity | None:
    provider_identity = context.provider_identity
    if provider_identity is None:
        return None
    canonical_opening = canonicalize_identity_url(opening_url)
    if provider_identity.provider == "generic":
        if not _same_site(provider_identity.canonical_board_url, canonical_opening):
            return None
        return OpeningIdentity(
            hiring_entity_name=provider_identity.hiring_entity_name,
            provider="generic",
            tenant=provider_identity.tenant,
            canonical_board_url=provider_identity.canonical_board_url,
            canonical_opening_url=canonical_opening,
        )
    adapter = registry.adapter_named(provider_identity.provider)
    board = adapter.identify_board(opening_url) if adapter is not None else None
    if board is None:
        if not _same_site(provider_identity.canonical_board_url, canonical_opening):
            return None
        tenant = provider_identity.tenant
    else:
        if board.provider != provider_identity.provider:
            return None
        canonical_board = canonicalize_identity_url(board.url)
        tenant = board.identifier or tenant_locator(canonical_board)
        if tenant != provider_identity.tenant and not (
            _identity_aliases(tenant) & _identity_aliases(provider_identity.tenant)
        ):
            return None
        tenant = provider_identity.tenant
    return OpeningIdentity(
        hiring_entity_name=provider_identity.hiring_entity_name,
        provider=provider_identity.provider,
        tenant=tenant,
        canonical_board_url=provider_identity.canonical_board_url,
        canonical_opening_url=canonical_opening,
    )


def _opening_identity_update(
    context: PipelineContext,
    opening_url: str,
    registry: ProviderRegistry,
) -> dict[str, OpeningIdentity]:
    identity = _opening_identity(context, opening_url, registry)
    return {"opening_identity": identity} if identity is not None else {}


def _same_url(left: str, right: str) -> bool:
    try:
        return canonicalize_identity_url(left) == canonicalize_identity_url(right)
    except ValueError:
        return False


def _same_site(left: str, right: str) -> bool:
    try:
        left_host = urlsplit(canonicalize_identity_url(left)).hostname or ""
        right_host = urlsplit(canonicalize_identity_url(right)).hostname or ""
        return _site_key(left_host) == _site_key(right_host)
    except ValueError:
        return False


def _identity_aliases(value: str) -> set[str]:
    ignored = {
        "co",
        "company",
        "corp",
        "corporation",
        "group",
        "holdings",
        "inc",
        "incorporated",
        "limited",
        "llc",
        "ltd",
        "plc",
        "the",
    }
    raw_tokens = re.findall(r"[a-z0-9]+", value.casefold())
    tokens = [
        token
        for token in raw_tokens
        if token not in ignored and len(token) >= 3
    ]
    aliases = {token for token in tokens if len(token) >= 4}
    if tokens:
        aliases.add("".join(tokens))
    if raw_tokens:
        aliases.add("".join(raw_tokens))
    return aliases


def _site_key(host: str) -> str:
    labels = [label for label in host.casefold().rstrip(".").split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    country_second_levels = {"ac", "co", "com", "gov", "net", "org"}
    width = 3 if len(labels[-1]) == 2 and labels[-2] in country_second_levels else 2
    return ".".join(labels[-width:])
