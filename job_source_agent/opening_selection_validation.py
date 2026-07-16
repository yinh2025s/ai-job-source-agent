from __future__ import annotations

import re

from .identity_continuity import (
    OpeningIdentity,
    OpeningSelectionEvidence,
    ProviderIdentity,
)
from .opening_matcher import title_identity_matches
from .result_identity import canonicalize_identity_url
from .website_resolver import location_region


_STRICT_SELECTION_METHODS = {
    "linkedin_external_apply",
    "provider_tenant_match",
    "verified_declared_inventory",
}


def validate_opening_selection(
    *,
    selection: OpeningSelectionEvidence | None,
    provider: ProviderIdentity | None,
    opening: OpeningIdentity | None,
    open_position_url: str | None,
    target_title: str | None,
    target_location: str | None,
) -> tuple[list[str], str]:
    if not open_position_url:
        return [], "not_applicable"
    strict = bool(
        provider is not None
        and provider.verification_method in _STRICT_SELECTION_METHODS
    )
    if selection is None:
        return (["OPENING_SELECTION_MISSING"] if strict else []), "missing"

    failures: list[str] = []
    if provider is None or opening is None:
        failures.append("OPENING_SELECTION_IDENTITY_MISSING")
    else:
        if selection.provider != provider.provider:
            failures.append("OPENING_SELECTION_PROVIDER_MISMATCH")
        if selection.tenant != provider.tenant:
            failures.append("OPENING_SELECTION_TENANT_MISMATCH")
        if selection.canonical_board_url != provider.canonical_board_url:
            failures.append("OPENING_SELECTION_BOARD_MISMATCH")
        if selection.canonical_opening_url != opening.canonical_opening_url:
            failures.append("OPENING_SELECTION_URL_MISMATCH")
    try:
        canonical_output = canonicalize_identity_url(open_position_url)
    except ValueError:
        failures.append("OPENING_SELECTION_URL_INVALID")
    else:
        if canonical_output != selection.canonical_opening_url:
            failures.append("OPENING_SELECTION_URL_MISMATCH")
    if target_title and not title_identity_matches(selection.title, target_title):
        failures.append("OPENING_TITLE_MISMATCH")
    location_classification = classify_location(
        selection.location,
        target_location,
    )
    if strict and location_classification == "mismatch":
        failures.append("OPENING_LOCATION_MISMATCH")
    if strict and selection.candidate_count < 1:
        failures.append("OPENING_SELECTION_EMPTY")
    return _deduplicate(failures), location_classification


def classify_location(
    candidate_location: str | None,
    target_location: str | None,
) -> str:
    if not candidate_location or not target_location:
        return "missing"
    candidate = _normalized_location(candidate_location)
    target = _normalized_location(target_location)
    if not candidate or not target:
        return "missing"
    if candidate == target:
        return "exact"
    if "remote" in candidate.split() or "remote" in target.split():
        return "region" if "remote" in candidate.split() else "mismatch"
    candidate_region = location_region(candidate_location)
    target_region = location_region(target_location)
    if candidate_region and candidate_region == target_region:
        return "region"
    overlap = set(candidate.split()) & set(target.split())
    if len(overlap) >= 2:
        return "overlap"
    return "mismatch"


def _normalized_location(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
