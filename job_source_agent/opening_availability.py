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
    if source_status in {"closed", "expired", "unavailable"}:
        return OpeningAvailabilityDiagnostic(
            disposition="source_posting_closed",
            confidence="high",
            reason_code="OPENING_CLOSED",
            detail="The source posting explicitly reports that the opening is no longer available.",
            evidence={"source_posting_status": source_status},
        )

    provider_errors = _provider_errors(trace)
    if provider_errors:
        return OpeningAvailabilityDiagnostic(
            disposition="discovery_incomplete",
            confidence="low",
            reason_code="OPENING_NOT_FOUND",
            detail="No exact opening was verified, and provider errors prevented a conclusive availability check.",
            evidence={
                "provider_error_count": len(provider_errors),
                "provider_errors": provider_errors,
            },
        )

    provider_api = trace.get("provider_api")
    inventory = provider_api.get("inventory") if isinstance(provider_api, dict) else None
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

    return OpeningAvailabilityDiagnostic(
        disposition="discovery_incomplete",
        confidence="low",
        reason_code="OPENING_NOT_FOUND",
        detail="No exact opening was verified, and the available evidence cannot establish that the posting is closed.",
        evidence={"provider_error_count": 0, "provider_errors": []},
    )


def _provider_errors(trace: dict[str, Any]) -> list[dict[str, Any]]:
    provider_api = trace.get("provider_api")
    channels: list[tuple[str, Any, bool]] = [("generic_search", trace, False)]
    if isinstance(provider_api, dict):
        channels.extend(
            (
                ("provider_api", provider_api, False),
                ("provider_adapter", provider_api.get("adapter_trace"), True),
                ("provider_detection", provider_api.get("provider_detection"), True),
            )
        )

    aggregated: list[dict[str, Any]] = []
    positions: dict[Any, int] = {}
    for provenance, container, include_singular in channels:
        if not isinstance(container, dict):
            continue
        records = container.get("errors")
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict) and record.get("error"):
                    _add_provider_error(aggregated, positions, record, provenance)
        if include_singular and container.get("error"):
            _add_provider_error(
                aggregated,
                positions,
                {"error": container["error"]},
                provenance,
            )
    return aggregated


def _add_provider_error(
    aggregated: list[dict[str, Any]],
    positions: dict[Any, int],
    record: dict[str, Any],
    provenance: str,
) -> None:
    key = _freeze(record)
    position = positions.get(key)
    if position is None:
        positions[key] = len(aggregated)
        aggregated.append({**record, "provenance": [provenance]})
        return
    provenances = aggregated[position]["provenance"]
    if provenance not in provenances:
        provenances.append(provenance)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None
