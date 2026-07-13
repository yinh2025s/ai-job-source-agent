from __future__ import annotations

import time
from typing import Protocol

from ..contracts import PipelineContext, StageExecution
from ..errors import DiscoveryError
from ..models import (
    STAGE_HIRING_IDENTITY_RESOLUTION,
    STAGE_LINKEDIN_DISCOVERY,
    STAGE_WEBSITE_RESOLUTION,
)
from ..reasons import canonical_reason_code, classify_fetch_error, make_stage_result
from ..web import FetchError, normalize_url


class WebsiteResolutionService(Protocol):
    def resolve(
        self,
        company_name: str,
        linkedin_company_url: str | None = None,
        job_location: str | None = None,
    ) -> tuple[str | None, dict]:
        ...


class HiringIdentity(Protocol):
    hiring_entity_name: str
    career_root_url: str | None
    official_website_url: str | None


class HiringIdentityResolutionService(Protocol):
    def resolve(
        self,
        company_name: str,
        website_url: str | None = None,
        linkedin_company_url: str | None = None,
    ) -> tuple[HiringIdentity | None, dict]:
        ...


class InputDiscoveryStage:
    """Represent the already-completed input/discovery boundary as S1."""

    name = STAGE_LINKEDIN_DISCOVERY

    def run(self, context: PipelineContext) -> StageExecution:
        company = context.company
        has_linkedin_input = bool(company.linkedin_job_url or company.linkedin_company_url)
        evidence: list[dict] = []
        if company.linkedin_job_url:
            evidence.append({"field": "linkedin_job_url", "url": company.linkedin_job_url})
        if company.linkedin_company_url:
            evidence.append({"field": "linkedin_company_url", "url": company.linkedin_company_url})
        return StageExecution(
            result=make_stage_result(
                self.name,
                "success" if has_linkedin_input else "not_applicable",
                input_count=1 if has_linkedin_input else 0,
                output_count=1 if has_linkedin_input else 0,
                evidence=evidence,
                detail=(
                    None
                    if has_linkedin_input
                    else "Direct company input; LinkedIn discovery was upstream or not required."
                ),
            ),
            trace={"source": company.source, "source_trace": company.source_trace},
        )


class WebsiteResolutionStage:
    name = STAGE_WEBSITE_RESOLUTION

    def __init__(self, service: WebsiteResolutionService) -> None:
        self.service = service

    def run(self, context: PipelineContext) -> StageExecution:
        started = time.perf_counter()
        try:
            if context.company_website_url or context.company.company_website_url:
                website_url = normalize_url(
                    context.company_website_url or context.company.company_website_url
                )
                trace = {
                    "selected": {
                        "url": website_url,
                        "reason": "provided by input record",
                    }
                }
                detail = "Official website supplied by the input record."
            else:
                website_url, trace = self.service.resolve(
                    context.company.company_name,
                    context.company.linkedin_company_url,
                    context.company.job_location,
                )
                website_url = normalize_url(website_url) if website_url else None
                detail = None
        except FetchError as exc:
            return _failed_execution(classify_fetch_error(str(exc)), started, str(exc))
        except DiscoveryError as exc:
            return _failed_execution(
                canonical_reason_code(exc.code),
                started,
                str(exc),
                trace=exc.trace,
            )
        except (TypeError, ValueError) as exc:
            return _failed_execution("WEBSITE_NOT_RESOLVED", started, str(exc))

        if not website_url:
            return _failed_execution(
                "WEBSITE_NOT_RESOLVED",
                started,
                "No official company website could be resolved.",
                trace=trace,
            )

        return StageExecution(
            result=make_stage_result(
                self.name,
                "success",
                duration_ms=_elapsed_ms(started),
                input_count=1,
                output_count=1,
                evidence=[{"field": "company_website_url", "url": website_url}],
                detail=detail,
            ),
            updates={"company_website_url": website_url},
            trace=trace,
        )


class HiringIdentityResolutionStage:
    name = STAGE_HIRING_IDENTITY_RESOLUTION

    def __init__(self, service: HiringIdentityResolutionService) -> None:
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
            identity, trace = self.service.resolve(
                context.company.company_name,
                context.company_website_url,
                context.company.linkedin_company_url,
            )
        except FetchError as exc:
            return _identity_failed_execution(
                classify_fetch_error(str(exc)), started, str(exc)
            )
        except DiscoveryError as exc:
            return _identity_failed_execution(
                canonical_reason_code(exc.code),
                started,
                str(exc),
                trace=exc.trace,
            )

        try:
            evidence: list[dict] = []
            updates: dict[str, str | None] = {}
            detail = (
                "No alternate hiring entity was found; the input company remains the hiring entity."
            )
            if identity:
                hiring_entity_name = identity.hiring_entity_name
                career_root_url = identity.career_root_url
                official_website_url = identity.official_website_url
                if hiring_entity_name:
                    updates["hiring_entity_name"] = hiring_entity_name
                    evidence.append(
                        {"field": "hiring_entity_name", "value": hiring_entity_name}
                    )
                if career_root_url:
                    normalized_career_root = normalize_url(career_root_url)
                    updates["career_root_url"] = normalized_career_root
                    evidence.append(
                        {"field": "career_root_url", "url": normalized_career_root}
                    )
                if official_website_url:
                    normalized_website = normalize_url(official_website_url)
                    updates["company_website_url"] = normalized_website
                    evidence.append(
                        {"field": "official_website_url", "url": normalized_website}
                    )
                detail = "An explicit hiring identity or career root was resolved."
            else:
                if context.company.hiring_entity_name:
                    evidence.append(
                        {
                            "field": "hiring_entity_name",
                            "value": context.company.hiring_entity_name,
                        }
                    )
                if context.company.career_root_url:
                    evidence.append(
                        {
                            "field": "career_root_url",
                            "url": normalize_url(context.company.career_root_url),
                        }
                    )
        except (AttributeError, TypeError, ValueError) as exc:
            return _identity_failed_execution(
                "COMPANY_IDENTITY_AMBIGUOUS",
                started,
                str(exc),
                trace=trace,
            )

        return StageExecution(
            result=make_stage_result(
                self.name,
                "success",
                duration_ms=_elapsed_ms(started),
                input_count=1,
                output_count=1,
                evidence=evidence,
                detail=detail,
            ),
            updates=updates,
            trace=trace,
        )


def _failed_execution(
    reason_code: str,
    started: float,
    detail: str,
    trace: dict | None = None,
) -> StageExecution:
    return StageExecution(
        result=make_stage_result(
            STAGE_WEBSITE_RESOLUTION,
            "failed",
            reason_code=reason_code,
            duration_ms=_elapsed_ms(started),
            input_count=1,
            detail=detail,
        ),
        trace=trace or {"error": detail},
    )


def _identity_failed_execution(
    reason_code: str,
    started: float,
    detail: str,
    trace: dict | None = None,
) -> StageExecution:
    return StageExecution(
        result=make_stage_result(
            STAGE_HIRING_IDENTITY_RESOLUTION,
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
