from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Callable
from urllib.parse import urlparse

from .models import LinkCandidate
from .scoring import ATS_DOMAINS, is_ats_url


SCHEDULE_VERSION = "9"
PROBE_POLICY_VERSION = "1"

_EVIDENCE_PROBE_DEADLINE_SECONDS = 6.0
_SPECULATIVE_PROBE_DEADLINE_SECONDS = 3.0
_MINIMUM_PROBE_DEADLINE_SECONDS = 0.05
_TRANSPORT_FAILURE_CIRCUIT_LIMIT = 3

_LANGUAGE_SEGMENTS = {
    "ar", "cs", "da", "de", "en", "es", "fi", "fr", "he", "id", "it",
    "ja", "ko", "nl", "no", "pl", "pt", "sv", "th", "tr", "vi", "zh",
}
_REGION_SEGMENTS = {"au", "ca", "de", "es", "fr", "gb", "in", "jp", "uk", "us"}


@dataclass
class CareerCandidateProbeAdmission:
    max_elapsed_seconds: float
    max_retries: int | None
    evidence_tier: int
    reserve_limited: bool
    _clock: Callable[[], float]
    _started_at: float | None = None

    def next_scope_seconds(self) -> float:
        now = self._clock()
        if self._started_at is None:
            self._started_at = now
            return self.max_elapsed_seconds
        return max(
            0.001,
            self.max_elapsed_seconds - max(0.0, now - self._started_at),
        )


@dataclass(frozen=True)
class CareerCandidateProbeOutcome:
    action: str
    consecutive_transport_failures: int


class CareerCandidateProbeController:
    """Bound candidate wall time and stop repeated transport-only fanout."""

    def __init__(
        self,
        *,
        source: str,
        downstream_reserve_seconds: float = 0.0,
        transport_failure_limit: int = _TRANSPORT_FAILURE_CIRCUIT_LIMIT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(source, str) or not source:
            raise ValueError("candidate probe source must be a nonempty string")
        if (
            isinstance(downstream_reserve_seconds, bool)
            or not isinstance(downstream_reserve_seconds, (int, float))
            or not math.isfinite(downstream_reserve_seconds)
            or downstream_reserve_seconds < 0
        ):
            raise ValueError("downstream reserve must be a finite nonnegative number")
        if (
            isinstance(transport_failure_limit, bool)
            or not isinstance(transport_failure_limit, int)
            or transport_failure_limit < 1
        ):
            raise ValueError("transport failure limit must be a positive integer")
        self.source = source
        self.downstream_reserve_seconds = float(downstream_reserve_seconds)
        self.transport_failure_limit = transport_failure_limit
        self._clock = clock
        self.consecutive_transport_failures = 0
        self.circuit_open = False
        self.events: list[dict[str, object]] = []

    def admission(
        self,
        candidate: LinkCandidate,
        *,
        remaining_fetch_seconds: float | None,
    ) -> CareerCandidateProbeAdmission:
        tier = candidate_evidence_tier(candidate)
        configured_deadline = (
            _SPECULATIVE_PROBE_DEADLINE_SECONDS
            if tier >= 3
            else _EVIDENCE_PROBE_DEADLINE_SECONDS
        )
        usable_seconds = _usable_probe_seconds(
            remaining_fetch_seconds,
            reserve_seconds=self.downstream_reserve_seconds,
        )
        reserve_limited = (
            usable_seconds is not None and usable_seconds < configured_deadline
        )
        max_elapsed_seconds = (
            configured_deadline
            if usable_seconds is None
            else max(
                _MINIMUM_PROBE_DEADLINE_SECONDS,
                min(configured_deadline, usable_seconds),
            )
        )
        return CareerCandidateProbeAdmission(
            max_elapsed_seconds=max_elapsed_seconds,
            max_retries=0 if tier >= 3 else None,
            evidence_tier=tier,
            reserve_limited=reserve_limited,
            _clock=self._clock,
        )

    def record_success(
        self,
        candidate: LinkCandidate,
        admission: CareerCandidateProbeAdmission,
    ) -> CareerCandidateProbeOutcome:
        self.consecutive_transport_failures = 0
        self.events.append(
            self._event(
                candidate,
                admission,
                outcome="transport_success",
            )
        )
        return CareerCandidateProbeOutcome("continue", 0)

    def record_failure(
        self,
        candidate: LinkCandidate,
        admission: CareerCandidateProbeAdmission,
        *,
        reason_code: str,
        retryable: bool,
        owner: str,
    ) -> CareerCandidateProbeOutcome:
        if retryable and owner == "budget":
            action = "stop_retryable_terminal"
        elif retryable and owner == "network":
            self.consecutive_transport_failures += 1
            if self.consecutive_transport_failures >= self.transport_failure_limit:
                self.circuit_open = True
                action = "open_transport_circuit"
            else:
                action = "continue"
        else:
            self.consecutive_transport_failures = 0
            action = "continue"
        self.events.append(
            self._event(
                candidate,
                admission,
                outcome="fetch_failure",
                reason_code=reason_code,
                retryable=retryable,
                owner=owner,
                action=action,
            )
        )
        return CareerCandidateProbeOutcome(
            action,
            self.consecutive_transport_failures,
        )

    def record_semantic_outcome(
        self,
        candidate: LinkCandidate,
        admission: CareerCandidateProbeAdmission,
        *,
        outcome: str,
    ) -> None:
        self.consecutive_transport_failures = 0
        self.events.append(
            self._event(candidate, admission, outcome=outcome)
        )

    def trace(self) -> dict[str, object]:
        return {
            "policy": "bounded_candidate_deadline_with_transport_circuit",
            "version": PROBE_POLICY_VERSION,
            "source": self.source,
            "evidence_probe_deadline_seconds": _EVIDENCE_PROBE_DEADLINE_SECONDS,
            "speculative_probe_deadline_seconds": (
                _SPECULATIVE_PROBE_DEADLINE_SECONDS
            ),
            "minimum_probe_deadline_seconds": _MINIMUM_PROBE_DEADLINE_SECONDS,
            "downstream_reserve_seconds": self.downstream_reserve_seconds,
            "transport_failure_limit": self.transport_failure_limit,
            "circuit_open": self.circuit_open,
            "events": self.events,
        }

    def _event(
        self,
        candidate: LinkCandidate,
        admission: CareerCandidateProbeAdmission,
        *,
        outcome: str,
        reason_code: str | None = None,
        retryable: bool | None = None,
        owner: str | None = None,
        action: str = "continue",
    ) -> dict[str, object]:
        event: dict[str, object] = {
            "url": candidate.url,
            "evidence_tier": admission.evidence_tier,
            "max_elapsed_seconds": admission.max_elapsed_seconds,
            "max_retries": admission.max_retries,
            "reserve_limited": admission.reserve_limited,
            "outcome": outcome,
            "action": action,
            "consecutive_transport_failures": (
                self.consecutive_transport_failures
            ),
        }
        if reason_code:
            event["reason_code"] = reason_code
        if retryable is not None:
            event["retryable"] = retryable
        if owner:
            event["owner"] = owner
        return event


def _usable_probe_seconds(
    remaining_fetch_seconds: float | None,
    *,
    reserve_seconds: float,
) -> float | None:
    if remaining_fetch_seconds is None:
        return None
    if (
        isinstance(remaining_fetch_seconds, bool)
        or not isinstance(remaining_fetch_seconds, (int, float))
        or not math.isfinite(remaining_fetch_seconds)
    ):
        return None
    return max(0.0, float(remaining_fetch_seconds) - reserve_seconds)


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
    speculative_subdomain_probes: list[LinkCandidate] = []
    regional_gateway_candidates: list[LinkCandidate] = []
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
        regional_gateway_candidates.extend(
            candidate
            for candidate in representatives + aliases
            if _is_target_region_gateway_page_link(candidate)
        )
        if tier == 3:
            speculative_subdomain_probes.extend(
                candidate
                for candidate in representatives + aliases
                if candidate.origin == "subdomain_probe"
            )

    reserved_host_fallback = None
    if fetch_limit >= 3 and speculative_host_fallbacks:
        reserved_host_fallback = speculative_host_fallbacks[0]
        current_index = scheduled.index(reserved_host_fallback)
        host_reservation_offset = (
            2 if fetch_limit >= 4 and speculative_subdomain_probes else 1
        )
        reservation_index = max(
            stronger_evidence_count,
            fetch_limit - host_reservation_offset,
        )
        if reservation_index < fetch_limit and current_index >= fetch_limit:
            scheduled.pop(current_index)
            scheduled.insert(reservation_index, reserved_host_fallback)
            roles_by_url[reserved_host_fallback.url] = "reserved_host_fallback"

    reserved_subdomain_probe = None
    if fetch_limit >= 2 and speculative_subdomain_probes:
        speculative_subdomain_probes.sort(key=_candidate_rank)
        reserved_subdomain_probe = speculative_subdomain_probes[0]
        current_index = scheduled.index(reserved_subdomain_probe)
        reservation_index = max(stronger_evidence_count, fetch_limit - 1)
        if reservation_index < fetch_limit and current_index >= fetch_limit:
            scheduled.pop(current_index)
            scheduled.insert(reservation_index, reserved_subdomain_probe)
        roles_by_url[reserved_subdomain_probe.url] = "reserved_subdomain_probe"

    reserved_regional_gateway = None
    if fetch_limit >= 2 and regional_gateway_candidates:
        reserved_regional_gateway = regional_gateway_candidates[0]
        current_index = scheduled.index(reserved_regional_gateway)
        reservation_index = min(stronger_evidence_count, fetch_limit - 1)
        if reservation_index < fetch_limit and current_index >= fetch_limit:
            scheduled.pop(current_index)
            scheduled.insert(reservation_index, reserved_regional_gateway)
        roles_by_url[reserved_regional_gateway.url] = "reserved_regional_gateway"

    return scheduled, {
        "policy": "evidence_then_host_route_diversity",
        "version": SCHEDULE_VERSION,
        "input_count": len(candidates),
        "eligible_count": len(eligible),
        "family_count": family_count,
        "deferred_alias_count": deferred_alias_count,
        "reserved_host_fallback": reserved_host_fallback.url if reserved_host_fallback else None,
        "reserved_subdomain_probe": (
            reserved_subdomain_probe.url if reserved_subdomain_probe else None
        ),
        "reserved_regional_gateway": (
            reserved_regional_gateway.url if reserved_regional_gateway else None
        ),
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
    if _is_high_evidence_job_list_candidate(candidate):
        return 1
    if (
        candidate.origin in {
            "page_link",
            "verified_homepage_navigation",
            "first_party_bundle_navigation",
        }
        and urlparse(candidate.url).scheme.casefold() == "https"
        and has_explicit_career_semantics
    ):
        return 1
    if (
        candidate.origin == "unknown"
        and urlparse(candidate.url).scheme.casefold() == "https"
        and "homepage navigation link" in candidate.reasons
        and has_explicit_career_semantics
    ):
        return 1
    if _is_first_party_embedded_job_list(candidate):
        return 1
    if _is_observed_http_ats_anchor(candidate):
        return 2
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


def _candidate_family_rank(candidate: LinkCandidate) -> tuple[int, int, int, int, int]:
    parsed = urlparse(candidate.url)
    source_host = candidate_concrete_host(candidate.source_url)
    candidate_host = candidate_concrete_host(candidate.url)
    locale_depth = candidate_locale_depth(parsed.path)
    return (
        0 if _has_unconflicted_target_region_match(candidate) else 1,
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


def _is_target_region_gateway_page_link(candidate: LinkCandidate) -> bool:
    if candidate.origin != "page_link":
        return False
    target = urlparse(candidate.url)
    source = urlparse(candidate.source_url)
    try:
        target_port = target.port
        source_port = source.port
    except ValueError:
        return False
    if (
        target.scheme.casefold() != "https"
        or source.scheme.casefold() != "https"
        or not target.hostname
        or not source.hostname
        or target.username
        or target.password
        or source.username
        or source.password
        or target_port not in {None, 443}
        or source_port not in {None, 443}
        or _registrable_site(target.hostname) != _registrable_site(source.hostname)
        or not _is_regional_or_locale_root(target.path)
    ):
        return False
    return _has_unconflicted_target_region_match(candidate)


def _has_unconflicted_target_region_match(candidate: LinkCandidate) -> bool:
    return any(
        reason.startswith("matches target location region '")
        for reason in candidate.reasons
    ) and not any(
        reason.startswith("conflicts with target location region '")
        for reason in candidate.reasons
    )


def _is_regional_or_locale_root(path: str) -> bool:
    parts = [part.casefold() for part in path.split("/") if part]
    return not parts or candidate_locale_depth(path) == len(parts)


def _registrable_site(host: str) -> str:
    labels = candidate_concrete_host(f"https://{host}").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    two_level_suffixes = {"co.uk", "com.au", "com.br", "com.sg", "co.jp", "co.nz"}
    suffix = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if suffix in two_level_suffixes else suffix


def _evidence_priority_boost(candidate: LinkCandidate) -> int:
    if any(
        reason.startswith("identity-supplied") or reason == "derived provider configuration"
        for reason in candidate.reasons
    ):
        return 1000
    if _is_high_evidence_job_list_candidate(candidate):
        return 750
    if _is_first_party_embedded_job_list(candidate) or (
        (
            candidate.origin in {"page_link", "verified_homepage_navigation"}
            or "homepage navigation link" in candidate.reasons
        )
        and urlparse(candidate.url).scheme.casefold() == "https"
        and any(
            reason.startswith("career keyword")
            or reason in {
                "explicit job-list route",
                "homepage team link requiring employment evidence",
            }
            for reason in candidate.reasons
        )
    ):
        return 500
    return 0


def _is_high_evidence_job_list_candidate(candidate: LinkCandidate) -> bool:
    if candidate.origin not in {
        "form_action",
        "page_link",
        "verified_homepage_navigation",
    }:
        return False
    target = urlparse(candidate.url)
    source = urlparse(candidate.source_url)
    if (
        target.scheme.casefold() != "https"
        or source.scheme.casefold() != "https"
        or not target.hostname
        or not source.hostname
        or target.username
        or target.password
    ):
        return False
    target_host = candidate_host_family(candidate)
    source_host = candidate_concrete_host(candidate.source_url).removeprefix("www.")
    same_site = (
        target_host == source_host
        or target_host.endswith("." + source_host)
        or source_host.endswith("." + target_host)
    )
    if same_site and "explicit job-list command" in candidate.reasons:
        return True
    return (
        not same_site
        and is_ats_url(candidate.url)
        and "known ATS domain" in candidate.reasons
    )


def _is_first_party_embedded_job_list(candidate: LinkCandidate) -> bool:
    source = urlparse(candidate.source_url)
    candidate_host = candidate_concrete_host(candidate.url)
    return (
        candidate.origin == "embedded_url"
        and urlparse(candidate.url).scheme.casefold() == "https"
        and source.scheme.casefold() == "https"
        and bool(candidate_host)
        and candidate_host == candidate_concrete_host(source.geturl())
        and "explicit job-list route" in candidate.reasons
    )


def _is_observed_http_ats_anchor(candidate: LinkCandidate) -> bool:
    if candidate.origin not in {"page_link", "verified_homepage_navigation"}:
        return False
    target = urlparse(candidate.url)
    source = urlparse(candidate.source_url)
    try:
        target_port = target.port
    except ValueError:
        return False
    return (
        target.scheme.casefold() == "http"
        and source.scheme.casefold() == "https"
        and bool(target.hostname)
        and bool(source.hostname)
        and candidate_concrete_host(candidate.url) in ATS_DOMAINS
        and target_port in {None, 80}
        and not target.username
        and not target.password
        and not source.username
        and not source.password
    )
