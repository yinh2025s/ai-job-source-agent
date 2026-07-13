from __future__ import annotations

from urllib.parse import urlparse

from .models import LinkCandidate


SCHEDULE_VERSION = "1"

_LANGUAGE_SEGMENTS = {
    "ar", "cs", "da", "de", "en", "es", "fi", "fr", "he", "id", "it",
    "ja", "ko", "nl", "no", "pl", "pt", "sv", "th", "tr", "vi", "zh",
}
_REGION_SEGMENTS = {"au", "ca", "de", "es", "fr", "gb", "in", "jp", "uk", "us"}


def schedule_career_candidates(
    candidates: list[LinkCandidate],
    *,
    fetch_limit: int,
) -> tuple[list[LinkCandidate], dict]:
    eligible = [candidate for candidate in candidates if candidate.score >= 50]
    tiers: dict[int, list[LinkCandidate]] = {}
    for candidate in eligible:
        tiers.setdefault(candidate_evidence_tier(candidate), []).append(candidate)

    scheduled: list[LinkCandidate] = []
    deferred_alias_count = 0
    family_count = 0
    roles_by_url: dict[str, str] = {}
    speculative_host_fallbacks: list[LinkCandidate] = []
    stronger_evidence_count = 0
    for tier in sorted(tiers):
        groups: dict[tuple[str, str], list[LinkCandidate]] = {}
        for candidate in tiers[tier]:
            family = (candidate_host_family(candidate), candidate_route_family(candidate))
            groups.setdefault(family, []).append(candidate)

        family_entries: list[tuple[LinkCandidate, list[LinkCandidate]]] = []
        aliases: list[LinkCandidate] = []
        for family_candidates in groups.values():
            ordered_family = sorted(family_candidates, key=_candidate_family_rank)
            representative = ordered_family[0]
            family_aliases = ordered_family[1:]
            family_entries.append((representative, family_aliases))
            aliases.extend(family_aliases)
        family_entries.sort(key=lambda entry: _candidate_rank(entry[0]))
        representatives = [entry[0] for entry in family_entries]
        family_count += len(family_entries)
        deferred_alias_count += len(aliases)
        aliases.sort(key=_candidate_rank)
        for representative, family_aliases in family_entries:
            roles_by_url[representative.url] = "representative"
            for alias in family_aliases:
                role = _candidate_alias_role(representative, alias)
                roles_by_url[alias.url] = role
                if tier == 3 and role == "host_fallback":
                    speculative_host_fallbacks.append(alias)
        scheduled.extend(representatives)
        scheduled.extend(aliases)
        if tier < 3:
            stronger_evidence_count += len(representatives) + len(aliases)

    reserved_host_fallback = None
    if fetch_limit >= 3 and speculative_host_fallbacks:
        reserved_host_fallback = speculative_host_fallbacks[0]
        current_index = scheduled.index(reserved_host_fallback)
        reservation_index = max(stronger_evidence_count, fetch_limit - 1)
        if reservation_index < fetch_limit and current_index >= fetch_limit:
            scheduled.pop(current_index)
            scheduled.insert(reservation_index, reserved_host_fallback)
            roles_by_url[reserved_host_fallback.url] = "reserved_host_fallback"

    return scheduled, {
        "policy": "evidence_then_host_route_diversity",
        "version": SCHEDULE_VERSION,
        "input_count": len(candidates),
        "eligible_count": len(eligible),
        "family_count": family_count,
        "deferred_alias_count": deferred_alias_count,
        "reserved_host_fallback": reserved_host_fallback.url if reserved_host_fallback else None,
        "roles_by_url": roles_by_url,
    }


def candidate_evidence_tier(candidate: LinkCandidate) -> int:
    has_explicit_career_semantics = any(
        reason.startswith("career keyword")
        or reason in {
            "explicit job-list route",
            "homepage team link requiring employment evidence",
        }
        for reason in candidate.reasons
    )
    if candidate.origin in {"identity_career_root", "derived_provider_config"} or any(
        reason.startswith("identity-supplied") or reason == "derived provider configuration"
        for reason in candidate.reasons
    ):
        return 0
    if (
        candidate.origin in {"page_link", "first_party_bundle_navigation"}
        and has_explicit_career_semantics
    ):
        return 1
    if (
        candidate.origin == "unknown"
        and "homepage navigation link" in candidate.reasons
        and has_explicit_career_semantics
    ):
        return 1
    if candidate.origin in {"path_probe", "subdomain_probe", "blind_ats_probe"}:
        return 3
    if candidate.origin == "unknown" and "generated path probe" in candidate.reasons:
        return 3
    if candidate.origin in {
        "derived_provider_config",
        "embedded_url",
        "job_detail_check",
        "search_result",
        "sitemap",
    }:
        return 2
    if candidate.origin == "unknown":
        return 2
    return 4


def candidate_host_family(candidate: LinkCandidate) -> str:
    return candidate_concrete_host(candidate.url).removeprefix("www.")


def candidate_concrete_host(url: str) -> str:
    host = (urlparse(url).hostname or "").rstrip(".").casefold()
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


def candidate_route_family(candidate: LinkCandidate) -> str:
    parts = [part.casefold() for part in urlparse(candidate.url).path.split("/") if part]
    parts = parts[candidate_locale_depth(urlparse(candidate.url).path):]
    if parts and parts[0].startswith("join-") and parts[0] not in {"join-us", "join-our-team"}:
        parts[0] = "join-brand"
    return "/".join(parts) or "/"


def candidate_locale_key(url: str) -> str | None:
    parts = [part.casefold() for part in urlparse(url).path.split("/") if part]
    depth = candidate_locale_depth(urlparse(url).path)
    return "/".join(parts[:depth]) if depth else None


def candidate_locale_depth(path: str) -> int:
    parts = [part.casefold() for part in path.split("/") if part]
    if not parts:
        return 0
    first = parts[0]
    if "-" in first:
        language, _, region = first.partition("-")
        return 1 if language in _LANGUAGE_SEGMENTS and region in _REGION_SEGMENTS else 0
    if first in _REGION_SEGMENTS and len(parts) > 1 and parts[1] in _LANGUAGE_SEGMENTS:
        return 2
    return 1 if first in _LANGUAGE_SEGMENTS else 0


def _candidate_rank(candidate: LinkCandidate) -> tuple[int]:
    return (-(candidate.score + _evidence_priority_boost(candidate)),)


def _candidate_family_rank(candidate: LinkCandidate) -> tuple[int, int, int, int]:
    parsed = urlparse(candidate.url)
    source_host = candidate_concrete_host(candidate.source_url)
    candidate_host = candidate_concrete_host(candidate.url)
    locale_depth = candidate_locale_depth(parsed.path)
    return (
        locale_depth if "generated path probe" in candidate.reasons else 0,
        0 if candidate_host == source_host else 1,
        len([part for part in parsed.path.split("/") if part]),
        -(candidate.score + _evidence_priority_boost(candidate)),
    )


def _candidate_alias_role(representative: LinkCandidate, alias: LinkCandidate) -> str:
    representative_host = candidate_concrete_host(representative.url)
    alias_host = candidate_concrete_host(alias.url)
    if (
        representative_host.removeprefix("www.") == alias_host.removeprefix("www.")
        and representative_host != alias_host
    ):
        if urlparse(representative.url).path.rstrip("/") == urlparse(alias.url).path.rstrip("/"):
            return "host_fallback"
    return "locale_alias"


def _evidence_priority_boost(candidate: LinkCandidate) -> int:
    if any(
        reason.startswith("identity-supplied") or reason == "derived provider configuration"
        for reason in candidate.reasons
    ):
        return 1000
    if "homepage navigation link" in candidate.reasons and any(
        reason.startswith("career keyword")
        or reason in {
            "explicit job-list route",
            "homepage team link requiring employment evidence",
        }
        for reason in candidate.reasons
    ):
        return 500
    return 0
