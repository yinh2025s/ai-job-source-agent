from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from typing import Any, Protocol, runtime_checkable
from urllib.parse import parse_qsl, unquote, urlparse, urlunparse

from .job_board import DiscoveredJobBoard
from .result_identity import canonicalize_identity_url


PROVIDER_CANDIDATE_SCHEMA_VERSION = "1.0"
MAX_PROVIDER_CANDIDATES = 12

SOURCE_PRIORITIES = {
    "external_apply": 500,
    "first_party_ats_link": 400,
    "targeted_opening_search": 300,
    "targeted_board_search": 200,
    "guessed_path": 100,
}

_PROVIDER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "code",
    "cookie",
    "csrf",
    "id_token",
    "jwt",
    "password",
    "refresh_token",
    "secret",
    "session",
    "sig",
    "signature",
    "token",
}


@dataclass(frozen=True)
class ProviderCandidate:
    """Untrusted URL lead. Ranking never grants provider or hiring identity."""

    url: str
    source_kind: str
    source_url: str
    company_name: str
    target_title: str | None = None
    target_location: str | None = None
    provider_hint: str | None = None
    query: str | None = None
    result_rank: int | None = None
    schema_version: str = PROVIDER_CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        canonical_url = _canonical_public_url(self.url)
        canonical_source = _canonical_public_url(self.source_url)
        object.__setattr__(self, "url", canonical_url)
        object.__setattr__(self, "source_url", canonical_source)
        if self.source_kind not in SOURCE_PRIORITIES:
            raise ValueError("Unsupported provider candidate source kind")
        _validate_text(self.company_name, "company name", required=True, maximum=300)
        _validate_text(self.target_title, "target title", maximum=500)
        _validate_text(self.target_location, "target location", maximum=500)
        _validate_text(self.query, "search query", maximum=1_000)
        if self.provider_hint is not None and not _PROVIDER.fullmatch(
            self.provider_hint
        ):
            raise ValueError("Invalid provider candidate hint")
        if self.result_rank is not None and (
            isinstance(self.result_rank, bool)
            or not isinstance(self.result_rank, int)
            or not 1 <= self.result_rank <= 1_000
        ):
            raise ValueError("Provider candidate result rank is invalid")
        if self.schema_version != PROVIDER_CANDIDATE_SCHEMA_VERSION:
            raise ValueError("Provider candidate schema is incompatible")
        if self.source_kind.startswith("targeted_"):
            if not self.query or self.result_rank is None:
                raise ValueError("Search candidates require query and result rank")
        elif self.query is not None or self.result_rank is not None:
            raise ValueError("Non-search candidates cannot carry search metadata")

    @property
    def priority(self) -> int:
        return SOURCE_PRIORITIES[self.source_kind]

    def to_trace_payload(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "source_kind": self.source_kind,
            "source_url": self.source_url,
            "company_name": self.company_name,
            "target_title": self.target_title,
            "target_location": self.target_location,
            "provider_hint": self.provider_hint,
            "query": self.query,
            "result_rank": self.result_rank,
            "priority": self.priority,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class ProviderCandidatePool:
    candidates: tuple[ProviderCandidate, ...]
    truncated: bool = False
    schema_version: str = PROVIDER_CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROVIDER_CANDIDATE_SCHEMA_VERSION:
            raise ValueError("Provider candidate pool schema is incompatible")
        if not isinstance(self.candidates, tuple):
            raise TypeError("Provider candidate pool must use an immutable tuple")
        if not isinstance(self.truncated, bool):
            raise TypeError("Provider candidate pool truncation must be boolean")
        if len(self.candidates) > MAX_PROVIDER_CANDIDATES:
            raise ValueError("Provider candidate pool exceeds the global bound")
        if any(not isinstance(item, ProviderCandidate) for item in self.candidates):
            raise TypeError("Provider candidate pool contains an invalid member")
        if tuple(sorted(self.candidates, key=_candidate_sort_key)) != self.candidates:
            raise ValueError("Provider candidate pool is not deterministically ranked")
        identities = [_candidate_identity(item) for item in self.candidates]
        if len(set(identities)) != len(identities):
            raise ValueError("Provider candidate pool contains duplicate URLs")

    @classmethod
    def build(
        cls,
        candidates: list[ProviderCandidate] | tuple[ProviderCandidate, ...],
        *,
        limit: int = MAX_PROVIDER_CANDIDATES,
    ) -> ProviderCandidatePool:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PROVIDER_CANDIDATES:
            raise ValueError("Provider candidate pool limit is invalid")
        strongest: dict[str, ProviderCandidate] = {}
        for candidate in candidates:
            if not isinstance(candidate, ProviderCandidate):
                raise TypeError("Provider candidate pool contains an invalid member")
            identity = _candidate_identity(candidate)
            current = strongest.get(identity)
            if current is None or _candidate_sort_key(candidate) < _candidate_sort_key(current):
                strongest[identity] = candidate
        ranked = sorted(strongest.values(), key=_candidate_sort_key)
        return cls(tuple(ranked[:limit]), truncated=len(ranked) > limit)

    def to_trace_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_count": len(self.candidates),
            "truncated": self.truncated,
            "candidates": [item.to_trace_payload() for item in self.candidates],
        }


@dataclass(frozen=True)
class VerifiedProviderCandidate:
    """Adapter-identified board derived from one untrusted candidate."""

    candidate: ProviderCandidate
    discovered_board: DiscoveredJobBoard

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, ProviderCandidate):
            raise TypeError("Verified candidate requires ProviderCandidate provenance")
        if not isinstance(self.discovered_board, DiscoveredJobBoard):
            raise TypeError("Verified candidate requires a discovered provider board")
        if self.candidate.provider_hint is not None and (
            self.candidate.provider_hint != self.discovered_board.board.provider
        ):
            raise ValueError("Verified provider conflicts with the candidate hint")


@dataclass(frozen=True)
class CandidateDiscoveryRequest:
    company_name: str
    target_title: str | None = None
    target_location: str | None = None
    company_website_url: str | None = None
    career_page_url: str | None = None
    external_apply_url: str | None = None


@dataclass(frozen=True)
class CandidateDiscoveryResult:
    candidates: tuple[ProviderCandidate, ...]
    trace: dict[str, Any]


@runtime_checkable
class CandidateDiscovery(Protocol):
    def discover(self, request: CandidateDiscoveryRequest) -> CandidateDiscoveryResult:
        ...


def _candidate_sort_key(candidate: ProviderCandidate) -> tuple[Any, ...]:
    return (
        -candidate.priority,
        candidate.result_rank if candidate.result_rank is not None else 0,
        candidate.url.casefold(),
        candidate.source_kind,
    )


def _candidate_identity(candidate: ProviderCandidate) -> str:
    parsed = urlparse(candidate.url)
    return urlunparse(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path.rstrip("/") or "/",
            "",
            parsed.query,
            "",
        )
    )


def _canonical_public_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 8_192:
        raise ValueError("Provider candidate URL is invalid")
    try:
        parsed = urlparse(value.strip())
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except (TypeError, ValueError) as error:
        raise ValueError("Provider candidate URL is invalid") from error
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
        or any(ord(character) < 32 for character in unquote(parsed.path))
        or any(_sensitive_query_key(key) for key, _item in query)
        or _private_host(hostname)
    ):
        raise ValueError("Provider candidate URL must be public HTTPS")
    netloc = hostname if port is None else f"{hostname}:{port}"
    candidate_url = urlunparse(
        (
            "https",
            netloc,
            parsed.path or "/",
            parsed.params,
            parsed.query,
            "",
        )
    )
    try:
        return canonicalize_identity_url(candidate_url)
    except (TypeError, ValueError) as error:
        raise ValueError("Provider candidate URL must be canonical identity evidence") from error


def _private_host(hostname: str) -> bool:
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return not address.is_global


def _sensitive_query_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return normalized in _SENSITIVE_QUERY_KEYS or normalized.endswith(
        ("_token", "_secret", "_session", "_signature")
    )


def _validate_text(
    value: str | None,
    label: str,
    *,
    required: bool = False,
    maximum: int,
) -> None:
    if value is None:
        if required:
            raise ValueError(f"Provider candidate {label} is required")
        return
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"Provider candidate {label} is invalid")
