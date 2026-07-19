from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlparse


_AVAILABILITY_VALUES = {
    "active",
    "listed",
    "closed",
    "expired",
    "unavailable",
    "unknown",
}
_APPLY_MODE_VALUES = {"linkedin_native", "external", "unknown"}
_TRUSTED_NATIVE_SOURCE = "authenticated_detail_dom"


@dataclass(frozen=True)
class SourcePostingEvidence:
    availability: str
    apply_mode: str
    evidence_source: str
    job_url: str


def trusted_linkedin_native_posting(
    source_trace: dict[str, Any],
    *,
    expected_job_url: str | None = None,
) -> SourcePostingEvidence | None:
    """Return explicit authenticated LinkedIn-native evidence, never an inference."""

    posting = source_trace.get("linkedin_posting")
    if not isinstance(posting, dict):
        return None

    availability = _enum_value(posting.get("availability"), _AVAILABILITY_VALUES)
    apply_mode = _enum_value(posting.get("apply_mode"), _APPLY_MODE_VALUES)
    evidence_source = _string_value(posting.get("evidence_source"))
    job_url = canonical_linkedin_job_url(posting.get("job_url"))
    if (
        availability != "active"
        or apply_mode != "linkedin_native"
        or evidence_source != _TRUSTED_NATIVE_SOURCE
        or not job_url
    ):
        return None

    if expected_job_url:
        expected = canonical_linkedin_job_url(expected_job_url)
        if not expected or expected != job_url:
            return None

    return SourcePostingEvidence(
        availability=availability,
        apply_mode=apply_mode,
        evidence_source=evidence_source,
        job_url=job_url,
    )


def explicit_closed_source_status(source_trace: dict[str, Any]) -> str | None:
    """Read only explicitly declared closed states from supported source containers."""

    values = [source_trace.get("posting_status")]
    for container_name in ("linkedin_posting", "source_posting"):
        container = source_trace.get(container_name)
        if isinstance(container, dict):
            values.extend((container.get("status"), container.get("availability")))
    for value in values:
        normalized = _string_value(value)
        if normalized in {"closed", "expired"}:
            return normalized
    return None


def source_posting_fingerprint_payload(source_trace: Any) -> dict[str, str] | None:
    """Return the stable behavior-affecting subset; volatile observation time is omitted."""

    if not isinstance(source_trace, dict):
        return None
    posting = source_trace.get("linkedin_posting")
    if not isinstance(posting, dict):
        return None

    payload: dict[str, str] = {}
    for field in ("availability", "apply_mode", "evidence_source", "job_url"):
        value = _string_value(posting.get(field))
        if value:
            payload[field] = value
    return payload or None


def canonical_linkedin_job_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlparse(value.strip())
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or host not in {"linkedin.com", "www.linkedin.com"}
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or re.fullmatch(r"/jobs/view/[^/?#]+/?", parsed.path) is None
    ):
        return None
    job_key = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return f"https://www.linkedin.com/jobs/view/{job_key}"


def _enum_value(value: Any, allowed: set[str]) -> str | None:
    normalized = _string_value(value)
    return normalized if normalized in allowed else None


def _string_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().lower().split())
    return normalized or None
