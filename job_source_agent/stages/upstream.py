from __future__ import annotations

import time
from typing import Protocol

from ..contracts import PipelineContext, StageExecution
from ..identity_continuity import HiringIdentityEvidence
from ..result_identity import canonicalize_identity_url
from ..errors import DiscoveryError
from ..homepage_navigation import HomepageNavigationEvidence
from ..models import (
    STAGE_HIRING_IDENTITY_RESOLUTION,
    STAGE_LINKEDIN_DISCOVERY,
    STAGE_WEBSITE_RESOLUTION,
)
from ..reasons import canonical_reason_code, make_stage_result
from ..web import FetchError, normalize_url
from ..fetch_failure import project_fetch_error


class WebsiteResolutionService(Protocol):
    def resolve(
        self,
        company_name: str,
        linkedin_company_url: str | None = None,
        job_location: str | None = None,
        preferred_url: str | None = None,
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
        linkedin_job_url: str | None = None,
        job_location: str | None = None,
    ) -> tuple[HiringIdentity | None, dict]:
        ...


class InputDiscoveryStage:
    """Represent the already-completed input/discovery boundary as S1."""

    name = STAGE_LINKEDIN_DISCOVERY

    def run(self, context: PipelineContext) -> StageExecution:
        company = context.company
        has_linkedin_input = bool(
            company.linkedin_job_url
            or company.linkedin_company_url
            or company.external_apply_url
        )
        evidence: list[dict] = []
        if company.linkedin_job_url:
            evidence.append({"field": "linkedin_job_url", "url": company.linkedin_job_url})
        if company.linkedin_company_url:
            evidence.append({"field": "linkedin_company_url", "url": company.linkedin_company_url})
        if company.external_apply_url:
            evidence.append({"field": "external_apply_url", "url": company.external_apply_url})
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
        clear_unverified_website = not bool(
            context.company.career_root_url or context.company.external_apply_url
        )
        try:
            provided_website = (
                context.company_website_url or context.company.company_website_url
            )
            resolve_with_evidence = getattr(
                self.service, "resolve_with_navigation_evidence", None
            )
            if callable(resolve_with_evidence):
                website_url, trace, navigation_evidence = resolve_with_evidence(
                    context.company.company_name,
                    context.company.linkedin_company_url,
                    context.company.job_location,
                    preferred_url=provided_website,
                )
            else:
                website_url, trace = self.service.resolve(
                    context.company.company_name,
                    context.company.linkedin_company_url,
                    context.company.job_location,
                    preferred_url=provided_website,
                )
                navigation_evidence = None
            website_url = normalize_url(website_url) if website_url else None
            detail = (
                "Provided website was revalidated before use."
                if provided_website
                else None
            )
        except FetchError as exc:
            failure = project_fetch_error(exc)
            return _failed_execution(
                failure["reason_code"],
                started,
                str(exc),
                trace={"fetch_failure": failure},
                clear_unverified_website=clear_unverified_website,
            )
        except DiscoveryError as exc:
            return _failed_execution(
                canonical_reason_code(exc.code),
                started,
                str(exc),
                trace=exc.trace,
                clear_unverified_website=clear_unverified_website,
            )
        except (TypeError, ValueError) as exc:
            return _failed_execution(
                "WEBSITE_NOT_RESOLVED",
                started,
                str(exc),
                clear_unverified_website=clear_unverified_website,
            )

        if not website_url:
            return _failed_execution(
                "WEBSITE_NOT_RESOLVED",
                started,
                "No official company website could be resolved.",
                trace=trace,
                clear_unverified_website=clear_unverified_website,
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
            updates={
                "company_website_url": website_url,
                **(
                    {"homepage_navigation_evidence": navigation_evidence}
                    if isinstance(navigation_evidence, HomepageNavigationEvidence)
                    else {}
                ),
            },
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
                context.company.linkedin_job_url,
                context.company.job_location,
            )
        except FetchError as exc:
            failure = project_fetch_error(exc)
            return _identity_failed_execution(
                failure["reason_code"],
                started,
                str(exc),
                trace={"fetch_failure": failure},
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
            updates: dict[str, object] = {}
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
                posting_identity = trace.get("posting_identity", {})
                if posting_identity.get("classification") == "agency_unresolved":
                    return StageExecution(
                        result=make_stage_result(
                            self.name,
                            "failed",
                            reason_code="COMPANY_IDENTITY_AMBIGUOUS",
                            duration_ms=_elapsed_ms(started),
                            input_count=1,
                            evidence=[
                                {
                                    "field": "publisher_role",
                                    "value": "recruiting_agency",
                                }
                            ],
                            detail=(
                                "Publisher is recruiting for an undisclosed client; "
                                "no safe hiring entity can be selected."
                            ),
                        ),
                        trace=trace,
                    )
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
            hiring_entity_name = (
                updates.get("hiring_entity_name")
                or context.company.hiring_entity_name
                or context.company.company_name
            )
            relationship_type = (
                getattr(identity, "relationship_type", None) if identity else None
            )
            relationship_verified = (
                getattr(identity, "relationship_verified", None) if identity else None
            )
            verification_method = (
                getattr(identity, "verification_method", None) if identity else None
            )
            relationship_evidence_url = (
                getattr(identity, "evidence_url", None) if identity else None
            )
            if _same_entity(context.company.company_name, str(hiring_entity_name)):
                relationship_type = "same_entity"
                relationship_verified = True
                verification_method = "same_entity"
            elif not identity:
                relationship_type = "input_asserted"
                relationship_verified = False
                verification_method = "input_asserted"
            if relationship_type not in {
                "same_entity",
                "brand_parent",
                "acquired_brand",
                "alternate_employer",
                "input_asserted",
            }:
                relationship_type = "alternate_employer"
            if verification_method is None:
                verification_method = "unverified_identity"
            relationship_evidence_url = relationship_evidence_url or (
                updates.get("career_root_url") or context.company_website_url
            )
            updates["hiring_identity_evidence"] = HiringIdentityEvidence(
                source_company_name=context.company.company_name,
                hiring_entity_name=str(hiring_entity_name),
                relationship_type=relationship_type,
                verification_method=verification_method,
                verified=bool(relationship_verified),
                evidence_url=canonicalize_identity_url(relationship_evidence_url)
                if relationship_evidence_url
                else None,
            )
            evidence.append(
                {
                    "type": "hiring_identity",
                    "relationship_type": relationship_type,
                    "verified": bool(relationship_verified),
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
    *,
    clear_unverified_website: bool = False,
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
        updates=(
            {"company_website_url": ""}
            if clear_unverified_website
            else {}
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


def _same_entity(left: str, right: str) -> bool:
    normalized_left = "".join(
        character.casefold() for character in left if character.isalnum()
    )
    normalized_right = "".join(
        character.casefold() for character in right if character.isalnum()
    )
    return normalized_left == normalized_right
