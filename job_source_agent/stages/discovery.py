from __future__ import annotations

import time
from typing import Protocol

from ..contracts import PipelineContext, StageExecution
from ..errors import DiscoveryError
from ..homepage_navigation import HomepageNavigationEvidence
from ..job_board import DiscoveredJobBoard
from ..models import (
    STAGE_CAREER_DISCOVERY,
    STAGE_HIRING_IDENTITY_RESOLUTION,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
)
from ..opening_availability import diagnose_opening_availability
from ..providers import DEFAULT_PROVIDER_REGISTRY, ProviderRegistry
from ..reasons import canonical_reason_code, classify_fetch_error, make_stage_result
from ..source_posting import trusted_linkedin_native_posting
from ..web import FetchError, normalize_url


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
                    "company_name": context.company.company_name,
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
            return _failed_execution(self.name, classify_fetch_error(str(exc)), started, str(exc))
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
            find_with_evidence = getattr(self.service, "find_job_board_with_evidence", None)
            if callable(find_with_evidence):
                job_list_url, trace, discovered_board = find_with_evidence(
                    context.career_page_url,
                    company_name=context.company.company_name,
                    target_location=context.company.job_location,
                )
            else:
                job_list_url, trace = self.service.find_job_board(
                    context.career_page_url,
                    company_name=context.company.company_name,
                    target_location=context.company.job_location,
                )
                discovered_board = None
        except FetchError as exc:
            if context.company.external_apply_url:
                return self._from_external_apply(
                    context,
                    fallback_trace={"career_job_board_error": str(exc)},
                )
            return _failed_execution(self.name, classify_fetch_error(str(exc)), started, str(exc))
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
            updates={"job_list_page_url": board.url, "provider": adapter.name},
            trace=trace,
        )


class OpeningMatchStage:
    name = STAGE_OPENING_MATCH

    def __init__(
        self,
        service: OpeningMatchService,
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self.service = service
        self.provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY

    def run(self, context: PipelineContext) -> StageExecution:
        if not context.job_list_page_url:
            return StageExecution(
                make_stage_result(
                    self.name,
                    "not_run",
                    detail="Job-board discovery did not produce an input.",
                )
            )
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
            return _failed_execution(self.name, classify_fetch_error(str(exc)), started, str(exc))
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
