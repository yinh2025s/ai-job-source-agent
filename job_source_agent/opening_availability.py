from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .source_posting import explicit_closed_source_status


@dataclass(frozen=True)
class OpeningAvailabilityDiagnostic:
    disposition: str
    confidence: str
    reason_code: str
    detail: str
    evidence: dict[str, Any]


def diagnose_opening_availability(
    trace: dict[str, Any],
    source_trace: dict[str, Any] | None = None,
) -> OpeningAvailabilityDiagnostic:
    """Classify an opening miss without treating missing search results as proof of expiry."""

    source_status = explicit_closed_source_status(source_trace or {})
    inventory = trace.get("provider_api", {}).get("inventory")
    if source_status in {"closed", "expired", "unavailable"}:
        return OpeningAvailabilityDiagnostic(
            disposition="source_posting_closed",
            confidence="high",
            reason_code="OPENING_CLOSED",
            detail="The source posting explicitly reports that the opening is no longer available.",
            evidence={"source_posting_status": source_status},
        )

    if isinstance(inventory, dict) and inventory.get("status") in {
        "verified",
        "verified_filtered_empty",
    }:
        candidate_count = _nonnegative_int(inventory.get("candidate_count"))
        strongest_score = _nonnegative_int(inventory.get("strongest_title_score"))
        return OpeningAvailabilityDiagnostic(
            disposition="verified_inventory_no_match",
            confidence="medium",
            reason_code="OPENING_NOT_FOUND",
            detail="The official provider inventory was read successfully, but no title met the match threshold.",
            evidence={
                "inventory_source": inventory.get("source"),
                "inventory_scope": inventory.get("scope", "full"),
                "candidate_count": candidate_count,
                "strongest_title_score": strongest_score,
            },
        )

    if isinstance(inventory, dict) and inventory.get("status") == "verified_empty":
        return OpeningAvailabilityDiagnostic(
            disposition="verified_inventory_empty",
            confidence="medium",
            reason_code="NO_PUBLIC_OPENINGS",
            detail="The official provider returned a valid empty public inventory.",
            evidence={
                "inventory_source": inventory.get("source"),
                "candidate_count": 0,
            },
        )

    errors = trace.get("provider_api", {}).get("errors")
    return OpeningAvailabilityDiagnostic(
        disposition="discovery_incomplete",
        confidence="low",
        reason_code="OPENING_NOT_FOUND",
        detail="No exact opening was verified, and the available evidence cannot establish that the posting is closed.",
        evidence={"provider_error_count": len(errors) if isinstance(errors, list) else 0},
    )


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None
