from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import StageResult


@dataclass(frozen=True)
class ReasonSpec:
    retryable: bool
    owner: str


REASON_SPECS: dict[str, ReasonSpec] = {
    "NETWORK_TIMEOUT": ReasonSpec(True, "network"),
    "DNS_FAILED": ReasonSpec(True, "network"),
    "CONNECTION_FAILED": ReasonSpec(True, "network"),
    "FETCH_FAILED": ReasonSpec(True, "network"),
    "HTTP_FORBIDDEN": ReasonSpec(False, "external"),
    "RATE_LIMITED": ReasonSpec(True, "external"),
    "SERVER_ERROR": ReasonSpec(True, "external"),
    "BOT_PROTECTION": ReasonSpec(False, "external"),
    "LOGIN_REQUIRED": ReasonSpec(False, "external"),
    "CAPTCHA_REQUIRED": ReasonSpec(False, "external"),
    "WEBSITE_NOT_RESOLVED": ReasonSpec(False, "resolver"),
    "COMPANY_IDENTITY_AMBIGUOUS": ReasonSpec(False, "resolver"),
    "CAREER_PAGE_NOT_FOUND": ReasonSpec(False, "resolver"),
    "JOB_BOARD_NOT_FOUND": ReasonSpec(False, "provider"),
    "PROVIDER_UNKNOWN": ReasonSpec(False, "provider"),
    "PROVIDER_UNSUPPORTED": ReasonSpec(False, "provider"),
    "PROVIDER_VARIANT_UNSUPPORTED": ReasonSpec(False, "provider"),
    "PROVIDER_FETCH_FAILED": ReasonSpec(True, "network"),
    "PARSING_FAILED": ReasonSpec(False, "parser"),
    "INVALID_STRUCTURED_DATA": ReasonSpec(False, "parser"),
    "EMPTY_PROVIDER_RESPONSE": ReasonSpec(False, "provider"),
    "OPENING_NOT_FOUND": ReasonSpec(False, "matcher"),
    "TITLE_MISMATCH": ReasonSpec(False, "matcher"),
    "LOCATION_MISMATCH": ReasonSpec(False, "matcher"),
    "NO_PUBLIC_OPENINGS": ReasonSpec(False, "matcher"),
    "OPENING_CLOSED": ReasonSpec(False, "matcher"),
    "COMPANY_TIME_BUDGET_EXHAUSTED": ReasonSpec(True, "budget"),
    "FETCH_BUDGET_EXHAUSTED": ReasonSpec(True, "budget"),
    "RESULT_VALIDATION_FAILED": ReasonSpec(False, "parser"),
}


LEGACY_REASON_CODES = {
    "website_not_resolved": "WEBSITE_NOT_RESOLVED",
    "career_page_not_found": "CAREER_PAGE_NOT_FOUND",
    "job_board_not_found": "JOB_BOARD_NOT_FOUND",
    "open_position_not_found": "OPENING_NOT_FOUND",
    "specific_opening_not_found": "OPENING_NOT_FOUND",
    "fetch_failed": "FETCH_FAILED",
    "company_time_budget_exhausted": "COMPANY_TIME_BUDGET_EXHAUSTED",
}


def reason_spec(reason_code: str | None) -> ReasonSpec:
    return REASON_SPECS.get(reason_code or "", ReasonSpec(False, "unknown"))


def canonical_reason_code(value: str | None) -> str:
    if not value:
        return "FETCH_FAILED"
    normalized = value.strip()
    if normalized in REASON_SPECS:
        return normalized
    return LEGACY_REASON_CODES.get(normalized.lower(), "FETCH_FAILED")


def classify_fetch_error(detail: str) -> str:
    text = detail.lower()
    if any(marker in text for marker in ("timed out", "timeout", "time out")):
        return "NETWORK_TIMEOUT"
    if any(marker in text for marker in ("name or service not known", "nodename nor servname", "getaddrinfo")):
        return "DNS_FAILED"
    if any(marker in text for marker in ("429", "too many requests", "rate limit")):
        return "RATE_LIMITED"
    if any(marker in text for marker in ("401", "login", "sign in")):
        return "LOGIN_REQUIRED"
    if any(marker in text for marker in ("403", "forbidden")):
        return "HTTP_FORBIDDEN"
    if any(marker in text for marker in ("captcha", "challenge", "cloudflare")):
        return "BOT_PROTECTION"
    if any(marker in text for marker in ("500", "502", "503", "504", "server error")):
        return "SERVER_ERROR"
    if any(marker in text for marker in ("connection refused", "connection reset", "network is unreachable")):
        return "CONNECTION_FAILED"
    return "FETCH_FAILED"


def make_stage_result(
    stage: str,
    status: str,
    *,
    reason_code: str | None = None,
    provider: str | None = None,
    duration_ms: int = 0,
    input_count: int = 0,
    output_count: int = 0,
    evidence: list[dict[str, Any]] | None = None,
    detail: str | None = None,
) -> StageResult:
    canonical_code = canonical_reason_code(reason_code) if reason_code else None
    spec = reason_spec(canonical_code)
    return StageResult(
        stage=stage,
        status=status,
        reason_code=canonical_code,
        retryable=spec.retryable if canonical_code else False,
        owner=spec.owner if canonical_code else None,
        provider=provider,
        duration_ms=duration_ms,
        input_count=input_count,
        output_count=output_count,
        evidence=evidence or [],
        detail=detail,
    )
