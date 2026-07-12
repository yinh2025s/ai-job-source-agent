from __future__ import annotations

import time
from typing import Protocol

from ..contracts import PipelineContext, StageExecution
from ..errors import DiscoveryError
from ..models import STAGE_CAREER_DISCOVERY, STAGE_JOB_BOARD_DISCOVERY, STAGE_OPENING_MATCH
from ..providers import DEFAULT_PROVIDER_REGISTRY, ProviderRegistry
from ..reasons import canonical_reason_code, classify_fetch_error, make_stage_result
from ..web import FetchError, normalize_url


class CareerDiscoveryService(Protocol):
    def find_career_page(self, company_website_url: str, company_name: str | None = None) -> tuple[str, dict]:
        ...


class JobBoardDiscoveryService(Protocol):
    def find_job_board(self, career_page_url: str) -> tuple[str, dict]:
        ...


class OpeningMatchService(Protocol):
    def match_opening(
        self,
        job_list_url: str,
        target_title: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str | None, str, dict]:
        ...


class CareerDiscoveryStage:
    name = STAGE_CAREER_DISCOVERY

    def __init__(self, service: CareerDiscoveryService) -> None:
        self.service = service

    def run(self, context: PipelineContext) -> StageExecution:
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
            if context.career_root_url:
                career_url = normalize_url(context.career_root_url)
                trace = {
                    "homepage_url": context.company_website_url,
                    "selected": {
                        "url": career_url,
                        "reason": "career root provided by company identity resolver",
                    },
                }
                detail = "Career root supplied by identity resolution."
            else:
                career_url, trace = self.service.find_career_page(
                    context.company_website_url,
                    company_name=context.company.company_name,
                )
                detail = None
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
            return StageExecution(
                make_stage_result(
                    self.name,
                    "not_run",
                    detail="Career discovery did not produce an input.",
                )
            )

        started = time.perf_counter()
        try:
            job_list_url, trace = self.service.find_job_board(context.career_page_url)
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

        provider = trace.get("provider") or self.provider_registry.detect(job_list_url)
        provider = None if provider == "generic" else provider
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
            updates={"job_list_page_url": job_list_url, "provider": provider},
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

        return StageExecution(
            result=make_stage_result(
                self.name,
                "partial",
                reason_code="OPENING_NOT_FOUND",
                provider=context.provider,
                duration_ms=_elapsed_ms(started),
                input_count=1,
                detail=trace.get("opening_error") or "No verified opening matched the requested title.",
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


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
