from __future__ import annotations

import re
from urllib.parse import urlparse

from .identity_continuity import (
    OpeningIdentity,
    OpeningSelectionEvidence,
    ProviderIdentity,
)
from .opening_matcher import publication_title_identity_matches
from .result_identity import canonicalize_identity_url
from .website_resolver import location_region


_US_STATES = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
    "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
    "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
    "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
    "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn",
    "mississippi": "ms", "missouri": "mo", "montana": "mt", "nebraska": "ne",
    "nevada": "nv", "new hampshire": "nh", "new jersey": "nj",
    "new mexico": "nm", "new york": "ny", "north carolina": "nc",
    "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or",
    "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc",
    "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut",
    "vermont": "vt", "virginia": "va", "washington": "wa",
    "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy",
    "district of columbia": "dc",
}
_UNAMBIGUOUS_STATE_CODES = set(_US_STATES.values()) - {
    "as", "in", "me", "or",
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
    strict = bool(provider is not None and provider.relationship_verified)
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
    if target_title and not publication_title_identity_matches(
        selection.title,
        target_title,
        target_location=target_location,
    ):
        failures.append("OPENING_TITLE_MISMATCH")
    location_classification = classify_location(
        selection.location,
        target_location,
    )
    if location_classification == "mismatch":
        title_location = _explicit_title_location(selection.title)
        if title_location and classify_location(title_location, target_location) in {
            "exact",
            "overlap",
            "region",
        }:
            location_classification = "title_qualifier"
    if (
        location_classification == "missing"
        and _opening_url_confirms_target_location(open_position_url, target_location)
    ):
        location_classification = "url_qualifier"
    if (
        strict
        and location_classification == "missing"
        and _opening_url_has_conflicting_state(open_position_url, target_location)
    ):
        location_classification = "mismatch"
    if strict and location_classification == "mismatch":
        failures.append("OPENING_LOCATION_MISMATCH")
    if (
        strict
        and target_location
        and location_classification == "missing"
        and selection.inventory_scope == "unknown"
        and not selection.inventory_complete
        and selection.candidate_count > 1
    ):
        failures.append("OPENING_LOCATION_UNVERIFIED")
    if strict and selection.candidate_count < 1:
        failures.append("OPENING_SELECTION_EMPTY")
    return _deduplicate(failures), location_classification


def classify_location(
    candidate_location: str | None,
    target_location: str | None,
) -> str:
    if not candidate_location or not target_location:
        return "missing"
    raw_candidate = _basic_location(candidate_location)
    raw_target = _basic_location(target_location)
    candidate = _normalized_location(candidate_location)
    target = _normalized_location(target_location)
    if not candidate or not target:
        return "missing"
    if candidate == target:
        return "exact"
    if len(set(raw_candidate.split()) & set(raw_target.split())) >= 2:
        return "overlap"
    if "remote" in candidate.split() or "remote" in target.split():
        return "region" if "remote" in candidate.split() else "mismatch"
    candidate_state = _target_state_code(candidate_location)
    target_state = _target_state_code(target_location)
    if candidate_state and target_state and candidate_state != target_state:
        return "mismatch"
    if (
        candidate_state
        and candidate_state == target_state
        and _looks_like_opaque_facility_label(candidate_location)
    ):
        return "region"
    if (
        candidate_state
        and candidate_state == target_state
        and _has_explicit_city_conflict(
            candidate_location,
            target_location,
            candidate_state,
            target_state,
        )
    ):
        return "mismatch"
    candidate_region = location_region(candidate_location)
    target_region = location_region(target_location)
    if candidate_region and candidate_region == target_region:
        return "region"
    overlap = set(candidate.split()) & set(target.split())
    if len(overlap) >= 2:
        return "overlap"
    meaningful_overlap = {
        token
        for token in overlap
        if len(token) >= 5 and token not in {"clinic", "office", "remote", "united", "states"}
    }
    if meaningful_overlap:
        return "overlap"
    return "mismatch"


def _explicit_title_location(title: str) -> str | None:
    qualifiers = re.findall(
        r"(?:,|\s+-\s+|\()\s*([^,()]{1,80})\)?(?:\s*$|,)",
        title,
    )
    aliases = {
        "nyc": "New York, NY",
        "new york city": "New York, NY",
        "dc": "Washington, DC",
        "d c": "Washington, DC",
        "washington dc": "Washington, DC",
    }
    for qualifier in qualifiers:
        normalized = " ".join(re.findall(r"[a-z0-9]+", qualifier.casefold()))
        if normalized in aliases:
            return aliases[normalized]
    return None


def _normalized_location(value: str) -> str:
    normalized = _basic_location(value)
    for name, code in sorted(_US_STATES.items(), key=lambda item: -len(item[0])):
        normalized = re.sub(
            rf"(?:^| ){re.escape(name)}(?= |$)",
            lambda match: (" " if match.group(0).startswith(" ") else "") + code,
            normalized,
        )
    return " ".join(normalized.split())


def _basic_location(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _looks_like_opaque_facility_label(location: str) -> bool:
    return "," not in location and bool(re.match(r"^\s*[a-z]\s+", location, re.I))


def _has_explicit_city_conflict(
    candidate_location: str,
    target_location: str,
    candidate_state: str,
    target_state: str,
) -> bool:
    if "," not in candidate_location or "," not in target_location:
        return False
    candidate_city = _location_city_tokens(candidate_location, candidate_state)
    target_city = _location_city_tokens(target_location, target_state)
    return bool(candidate_city and target_city and not candidate_city & target_city)


def _location_city_tokens(location: str, state_code: str) -> set[str]:
    parts = [part.strip() for part in location.split(",") if part.strip()]
    if parts and parts[-1].casefold() in {
        "us",
        "usa",
        "united states",
        "united states of america",
    }:
        parts.pop()
    city_part = parts[-2] if len(parts) >= 2 else (parts[0] if parts else location)
    tokens = set(re.findall(r"[a-z0-9]+", city_part.casefold()))
    tokens.discard(state_code.casefold())
    return tokens


def _opening_url_has_conflicting_state(
    opening_url: str,
    target_location: str | None,
) -> bool:
    target_state = _target_state_code(target_location)
    if target_state is None:
        return False
    path_tokens: list[str] = []
    for segment in urlparse(opening_url).path.casefold().split("/"):
        if not segment or _looks_like_opaque_requisition_id(segment):
            continue
        path_tokens.extend(re.findall(r"[a-z]+", segment))
    path_text = " ".join(path_tokens)
    states = {
        code
        for name, code in _US_STATES.items()
        if re.search(rf"(?:^| ){re.escape(name)}(?: |$)", path_text)
    }
    states.update(token for token in path_tokens if token in _UNAMBIGUOUS_STATE_CODES)
    return bool(states and target_state not in states)


def _opening_url_confirms_target_location(
    opening_url: str,
    target_location: str | None,
) -> bool:
    target_state = _target_state_code(target_location)
    if target_state is None or not target_location:
        return False
    target_city = _location_city_tokens(target_location, target_state)
    if not target_city:
        return False

    path_tokens: list[str] = []
    for segment in urlparse(opening_url).path.casefold().split("/"):
        if not segment or _looks_like_opaque_requisition_id(segment):
            continue
        path_tokens.extend(re.findall(r"[a-z]+", segment))
    path_token_set = set(path_tokens)
    path_text = " ".join(path_tokens)
    state_present = target_state in path_token_set or any(
        code == target_state
        and re.search(rf"(?:^| ){re.escape(name)}(?: |$)", path_text)
        for name, code in _US_STATES.items()
    )
    return state_present and target_city.issubset(path_token_set)


def _looks_like_opaque_requisition_id(segment: str) -> bool:
    if re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        segment,
    ):
        return True
    return bool(
        len(segment) >= 6
        and "-" not in segment
        and "_" not in segment
        and re.fullmatch(r"[a-z0-9]+", segment)
        and re.search(r"[a-z]", segment)
        and re.search(r"[0-9]", segment)
    )


def _target_state_code(location: str | None) -> str | None:
    if not location:
        return None
    normalized = _normalized_location(location)
    if not normalized:
        return None
    parts = [part.strip() for part in location.casefold().split(",") if part.strip()]
    for part in reversed(parts):
        tokens = re.findall(r"[a-z]+", part)
        for token in reversed(tokens):
            if token in _US_STATES.values():
                return token
        name = " ".join(tokens)
        if name in _US_STATES:
            return _US_STATES[name]
    for name, code in _US_STATES.items():
        if re.search(rf"(?:^| ){re.escape(name)}(?: |$)", normalized):
            return code
    return None


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
