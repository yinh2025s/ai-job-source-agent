"""Stage-local, privacy-safe inputs for optional LLM candidate reasoning.

This module deliberately accepts only explicit public company fields and a
typed deterministic resolver outcome.  It has no dependency on pipeline
context, trace payloads, fetch exceptions, providers, or model clients.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .candidate_reasoning_contracts import QueryPlannerRequest, RejectedCandidateSummary
from .candidate_reasoning_policy import (
    CandidateReasoningEligibilityContext,
    GCondition,
    IdentityState,
    TransportCause,
)


_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){8,}\d(?!\d)")
_CREDENTIAL = re.compile(
    r"(?i)\bauthorization\b\s*(?::|=)\s*(?:bearer\s+)?[^\s,;]+|"
    r"\b(?:bearer|cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|password)"
    r"\b\s*(?::|=|\s)\s*[^\s,;]+"
)
_LOCAL_PATH = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\)[^\s,;]*")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class PublicCompanyReasoningInput:
    """The allowlisted public subset of a ``CompanyInput`` for LLM planning.

    The caller is responsible for taking these values from explicit public
    fields only.  ``source_trace`` and other free-form pipeline state are
    intentionally absent from this contract.
    """

    company_name: str
    linkedin_company_slug: str | None = None
    public_company_summary: str | None = None
    job_title: str | None = None
    job_location: str | None = None
    industry: str | None = None
    company_location: str | None = None
    rejected_candidates: tuple[RejectedCandidateSummary, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.company_name, str) or not self.company_name.strip():
            raise ValueError("company_name must be a non-empty public string")
        for name in (
            "linkedin_company_slug",
            "public_company_summary",
            "job_title",
            "job_location",
            "industry",
            "company_location",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{name} must be a string or None")
        if not isinstance(self.rejected_candidates, tuple):
            raise TypeError("rejected_candidates must be an immutable tuple")
        if not all(isinstance(item, RejectedCandidateSummary) for item in self.rejected_candidates):
            raise TypeError("rejected_candidates must contain RejectedCandidateSummary values")


@dataclass(frozen=True, slots=True)
class DeterministicResolverOutcome:
    """Typed facts produced by deterministic resolution, never parsed text."""

    has_verified_website: bool = False
    has_verified_provider_relationship: bool = False
    has_official_external_apply: bool = False
    identity_state: IdentityState = "resolved"
    transport_cause: TransportCause | None = None
    has_sufficient_budget: bool = True
    replay_mode: bool = False
    has_compatible_replay_fixture: bool = False
    later_stage_started: bool = False
    has_verified_terminal_decision: bool = False
    g_conditions: tuple[GCondition, ...] = ()

    def __post_init__(self) -> None:
        # Delegate enum and tuple validation to the frozen policy DTO.  This
        # keeps the input layer aligned without interpreting any error text.
        CandidateReasoningEligibilityContext(
            feature_enabled=False,
            has_verified_website=self.has_verified_website,
            has_verified_provider_relationship=self.has_verified_provider_relationship,
            has_official_external_apply=self.has_official_external_apply,
            identity_state=self.identity_state,
            transport_cause=self.transport_cause,
            has_sufficient_budget=self.has_sufficient_budget,
            replay_mode=self.replay_mode,
            has_compatible_replay_fixture=self.has_compatible_replay_fixture,
            later_stage_started=self.later_stage_started,
            has_verified_terminal_decision=self.has_verified_terminal_decision,
            g_conditions=self.g_conditions,
        )


def build_query_planner_request(
    company: PublicCompanyReasoningInput,
) -> QueryPlannerRequest:
    """Build a bounded request after redacting common sensitive fragments."""

    if not isinstance(company, PublicCompanyReasoningInput):
        raise TypeError("company must use PublicCompanyReasoningInput")
    return QueryPlannerRequest(
        normalized_company_name=_required_public_text(company.company_name, "company_name"),
        linkedin_company_slug=_sanitize_optional_public_text(company.linkedin_company_slug),
        public_company_summary=_sanitize_optional_public_text(company.public_company_summary),
        job_title=_sanitize_optional_public_text(company.job_title),
        job_location=_sanitize_optional_public_text(company.job_location),
        industry=_sanitize_optional_public_text(company.industry),
        company_location=_sanitize_optional_public_text(company.company_location),
        rejected_candidates=company.rejected_candidates,
    )


def build_candidate_reasoning_eligibility_context(
    *,
    feature_enabled: bool,
    outcome: DeterministicResolverOutcome,
) -> CandidateReasoningEligibilityContext:
    """Project typed resolver facts into policy input without inference."""

    if not isinstance(feature_enabled, bool):
        raise TypeError("feature_enabled must be boolean")
    if not isinstance(outcome, DeterministicResolverOutcome):
        raise TypeError("outcome must use DeterministicResolverOutcome")
    return CandidateReasoningEligibilityContext(
        feature_enabled=feature_enabled,
        has_verified_website=outcome.has_verified_website,
        has_verified_provider_relationship=outcome.has_verified_provider_relationship,
        has_official_external_apply=outcome.has_official_external_apply,
        identity_state=outcome.identity_state,
        transport_cause=outcome.transport_cause,
        has_sufficient_budget=outcome.has_sufficient_budget,
        replay_mode=outcome.replay_mode,
        has_compatible_replay_fixture=outcome.has_compatible_replay_fixture,
        later_stage_started=outcome.later_stage_started,
        has_verified_terminal_decision=outcome.has_verified_terminal_decision,
        g_conditions=outcome.g_conditions,
    )


def sanitize_public_text(value: str | None) -> str | None:
    """Remove common private fragments while leaving ordinary text unchanged.

    Prompt-injection-like wording is not a secret and intentionally remains
    ordinary untrusted data.  Empty results are represented as ``None``.
    """

    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("public text must be a string or None")
    sanitized = value
    for pattern in (_EMAIL, _PHONE, _CREDENTIAL, _LOCAL_PATH):
        sanitized = pattern.sub(" ", sanitized)
    sanitized = _WHITESPACE.sub(" ", sanitized).strip()
    return sanitized or None


def linkedin_company_slug(url: str | None) -> str | None:
    """Extract only a bounded public LinkedIn company slug from a URL."""
    if not url:
        return None
    if not isinstance(url, str):
        raise TypeError("LinkedIn company URL must be text or None")
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host not in {"linkedin.com", "www.linkedin.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].casefold() != "company":
        return None
    slug = parts[1].strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", slug):
        return None
    return slug


def _sanitize_optional_public_text(value: str | None) -> str | None:
    return sanitize_public_text(value)


def _required_public_text(value: str, field_name: str) -> str:
    sanitized = sanitize_public_text(value)
    if sanitized is None:
        raise ValueError(f"{field_name} is empty after redaction")
    return sanitized
