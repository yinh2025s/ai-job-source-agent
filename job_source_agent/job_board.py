from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import re
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, unquote, urlparse


_DETECTION_METHODS = {
    "external_apply_url",
    "linked_url_evidence",
    "page_evidence",
    "page_probe",
    "url_evidence",
}
_PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_URL_CHARS = 8_192
_MAX_IDENTIFIER_CHARS = 65_536
_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$",
    re.IGNORECASE,
)
_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_PHENOM_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{3,100}$")
_SENSITIVE_QUERY_KEYS = {
    "csrf",
    "csrf_token",
    "client_secret",
    "id_token",
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "code",
    "cookie",
    "jwt",
    "password",
    "passwd",
    "refresh_token",
    "secret",
    "session",
    "session_id",
    "sessionid",
    "sig",
    "signature",
    "token",
}
_SENSITIVE_QUERY_SUFFIXES = (
    "_auth",
    "_authorization",
    "_cookie",
    "_csrf",
    "_jwt",
    "_password",
    "_secret",
    "_session",
    "_signature",
    "_token",
)
_SECRET_VALUE = re.compile(
    r"(?:\b(?:bearer|basic)\s+[A-Za-z0-9+/=_-]{8,}|"
    r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)
_HTML_CONTENT = re.compile(r"<(?:!doctype|html|script|body|head)\b", re.IGNORECASE)
_PORTFOLIO_SCHEMA_VERSION = "1.0"
_MAX_PORTFOLIO_BOARDS = 8


@dataclass(frozen=True)
class JobBoard:
    url: str
    provider: str
    identifier: str | None = None
    replay_safe: bool = False


@dataclass(frozen=True)
class DiscoveredJobBoard:
    board: JobBoard
    detection_method: str
    evidence_url: str

    def to_checkpoint_payload(self) -> dict[str, Any] | None:
        if not self.board.replay_safe:
            return None
        _validate_discovered_board(self)
        return {
            "board": {
                "url": self.board.url,
                "provider": self.board.provider,
                "identifier": self.board.identifier,
                "replay_safe": True,
            },
            "detection_method": self.detection_method,
            "evidence_url": self.evidence_url,
        }

    @classmethod
    def from_checkpoint_payload(cls, payload: Any) -> DiscoveredJobBoard:
        if not isinstance(payload, dict) or set(payload) != {
            "board",
            "detection_method",
            "evidence_url",
        }:
            raise ValueError("Discovered job board payload has unsupported fields")
        board_payload = payload.get("board")
        if not isinstance(board_payload, dict) or set(board_payload) != {
            "url",
            "provider",
            "identifier",
            "replay_safe",
        }:
            raise ValueError("Discovered job board locator has unsupported fields")
        if board_payload.get("replay_safe") is not True:
            raise ValueError("Checkpointed job board locator must be replay-safe")
        board = JobBoard(
            url=board_payload.get("url"),
            provider=board_payload.get("provider"),
            identifier=board_payload.get("identifier"),
            replay_safe=True,
        )
        discovered = cls(
            board=board,
            detection_method=payload.get("detection_method"),
            evidence_url=payload.get("evidence_url"),
        )
        _validate_discovered_board(discovered)
        return discovered


@dataclass(frozen=True)
class JobBoardPortfolio:
    boards: tuple[DiscoveredJobBoard, ...]
    eligible_set_complete: bool

    def __post_init__(self) -> None:
        _validate_job_board_portfolio(self)

    @property
    def primary(self) -> DiscoveredJobBoard:
        return self.boards[0]

    def to_checkpoint_payload(self) -> dict[str, Any] | None:
        if not all(discovered.board.replay_safe for discovered in self.boards):
            return None
        board_payloads = []
        for discovered in self.boards:
            payload = discovered.to_checkpoint_payload()
            if payload is None:
                return None
            board_payloads.append(payload)
        return {
            "schema_version": _PORTFOLIO_SCHEMA_VERSION,
            "boards": board_payloads,
            "eligible_set_complete": self.eligible_set_complete,
        }

    @classmethod
    def from_checkpoint_payload(cls, payload: Any) -> JobBoardPortfolio:
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "boards",
            "eligible_set_complete",
        }:
            raise ValueError("Job-board portfolio payload has unsupported fields")
        if payload.get("schema_version") != _PORTFOLIO_SCHEMA_VERSION:
            raise ValueError("Job-board portfolio schema is incompatible")
        raw_boards = payload.get("boards")
        if not isinstance(raw_boards, list):
            raise ValueError("Job-board portfolio boards must be a list")
        if not isinstance(payload.get("eligible_set_complete"), bool):
            raise ValueError("Job-board portfolio completeness must be boolean")
        return cls(
            boards=tuple(
                DiscoveredJobBoard.from_checkpoint_payload(item)
                for item in raw_boards
            ),
            eligible_set_complete=payload["eligible_set_complete"],
        )


def _validate_discovered_board(discovered: DiscoveredJobBoard) -> None:
    board = discovered.board
    if not isinstance(board.url, str) or not _is_public_https_url(board.url):
        raise ValueError("Job board URL must be public HTTPS")
    if not isinstance(board.provider, str) or not _PROVIDER_NAME.fullmatch(board.provider):
        raise ValueError("Job board provider is invalid")
    if board.identifier is not None and (
        not isinstance(board.identifier, str)
        or not board.identifier
        or len(board.identifier) > _MAX_IDENTIFIER_CHARS
        or any(ord(character) < 32 for character in board.identifier)
    ):
        raise ValueError("Job board identifier is invalid")
    if not isinstance(board.replay_safe, bool):
        raise ValueError("Job board replay policy is invalid")
    if discovered.detection_method not in _DETECTION_METHODS:
        raise ValueError("Job board detection method is invalid")
    if not isinstance(discovered.evidence_url, str) or not _is_public_https_url(
        discovered.evidence_url
    ):
        raise ValueError("Job board evidence URL must be public HTTPS")
    if not _same_origin(board.url, discovered.evidence_url):
        raise ValueError("Job board evidence URL must match the board origin")
    policy = _REPLAY_SAFE_POLICIES.get(board.provider)
    if board.replay_safe and (policy is None or not policy(board)):
        raise ValueError("Job board locator is not replay-safe for this provider")


def _validate_job_board_portfolio(portfolio: JobBoardPortfolio) -> None:
    if not isinstance(portfolio.boards, tuple) or not (
        1 <= len(portfolio.boards) <= _MAX_PORTFOLIO_BOARDS
    ):
        raise ValueError("Job-board portfolio must contain between one and eight boards")
    if not isinstance(portfolio.eligible_set_complete, bool):
        raise ValueError("Job-board portfolio completeness must be boolean")
    identities: set[tuple[str, str]] = set()
    for discovered in portfolio.boards:
        if not isinstance(discovered, DiscoveredJobBoard):
            raise TypeError("Job-board portfolio members must be discovered boards")
        _validate_discovered_board(discovered)
        parsed_url = urlparse(discovered.board.url)
        normalized_identity_url = parsed_url._replace(
            scheme=parsed_url.scheme.casefold(),
            netloc=parsed_url.netloc.casefold(),
            path=parsed_url.path.rstrip("/") or "/",
        ).geturl()
        identity = (
            discovered.board.provider.casefold(),
            normalized_identity_url,
        )
        if identity in identities:
            raise ValueError("Job-board portfolio contains duplicate public board identity")
        identities.add(identity)


def _is_public_https_url(value: str) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_URL_CHARS
        or _contains_unsafe_content(value)
    ):
        return False
    try:
        parsed = urlparse(value)
        port = parsed.port
        decoded_path = unquote(parsed.path)
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme.casefold() == "https"
        and _is_public_host(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and not parsed.fragment
        and not _has_controls(decoded_path)
        and all(
            not _is_sensitive_query_key(key)
            and not _contains_unsafe_content(key)
            and not _contains_unsafe_content(item)
            for key, item in query
        )
    )


def _contains_unsafe_content(value: str) -> bool:
    decoded = unquote(value)
    return bool(
        _has_controls(value)
        or _has_controls(decoded)
        or _SECRET_VALUE.search(value)
        or _SECRET_VALUE.search(decoded)
        or _HTML_CONTENT.search(value)
        or _HTML_CONTENT.search(decoded)
    )


def _is_sensitive_query_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return normalized in _SENSITIVE_QUERY_KEYS or normalized.endswith(
        _SENSITIVE_QUERY_SUFFIXES
    )


def _has_controls(value: str) -> bool:
    return any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)


def _same_origin(first: str, second: str) -> bool:
    try:
        left = urlparse(first)
        right = urlparse(second)
        left_port = left.port or 443
        right_port = right.port or 443
    except (TypeError, ValueError):
        return False
    return (
        left.scheme.casefold() == right.scheme.casefold() == "https"
        and (left.hostname or "").casefold() == (right.hostname or "").casefold()
        and left_port == right_port == 443
    )


def _no_query(url: str) -> bool:
    return not urlparse(url).query


def _host(value: str) -> str:
    return (urlparse(value).hostname or "").casefold()


def _valid_hostname(value: str) -> bool:
    return len(value) <= 253 and bool(_HOSTNAME.fullmatch(value))


def _is_public_host(value: str | None) -> bool:
    host = (value or "").casefold().rstrip(".")
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return "." in host


def _valid_identifier_text(value: str, *, limit: int) -> bool:
    return bool(value) and len(value) <= limit and not _contains_unsafe_content(value)


def _avature_policy(board: JobBoard) -> bool:
    parts = (board.identifier or "").split("|")
    if len(parts) != 3:
        return False
    host, language, portal = parts
    expected_path = f"/{language}/{portal}/SearchJobs"
    parsed = urlparse(board.url)
    return bool(
        _valid_hostname(host)
        and host.casefold() == _host(board.url)
        and _SEGMENT.fullmatch(language)
        and _SEGMENT.fullmatch(portal)
        and parsed.path == expected_path
        and _no_query(board.url)
    )


def _eightfold_policy(board: JobBoard) -> bool:
    parsed = urlparse(board.url)
    identifier = (board.identifier or "").casefold()
    return bool(
        _valid_hostname(identifier)
        and parsed.path.rstrip("/").casefold() == "/careers"
        and _no_query(board.url)
    )


def _greenhouse_policy(board: JobBoard) -> bool:
    identifier = board.identifier or ""
    if identifier.startswith("custom:"):
        host = identifier.removeprefix("custom:").casefold()
        return _valid_hostname(host) and host == _host(board.url)
    if not identifier.startswith("nuxt:") or "|" not in identifier:
        return False
    host, payload_url = identifier.removeprefix("nuxt:").split("|", 1)
    parsed_payload = urlparse(payload_url)
    return bool(
        _valid_hostname(host)
        and host.casefold() == _host(board.url) == _host(payload_url)
        and _is_public_https_url(payload_url)
        and parsed_payload.path.endswith("/careers/payload.js")
    )


def _icims_policy(board: JobBoard) -> bool:
    identifier = (board.identifier or "").casefold()
    parts = [part.casefold() for part in urlparse(board.url).path.split("/") if part]
    return _valid_hostname(identifier) and identifier == _host(board.url) and "jobs" in parts


def _phenom_policy(board: JobBoard) -> bool:
    parts = [part.casefold() for part in urlparse(board.url).path.split("/") if part]
    return bool(
        _PHENOM_IDENTIFIER.fullmatch(board.identifier or "")
        and parts
        and parts[-1] == "search-results"
        and _no_query(board.url)
    )


def _strict_json(value: str) -> dict[str, Any] | None:
    if not _valid_identifier_text(value, limit=_MAX_IDENTIFIER_CHARS):
        return None

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    try:
        parsed = json.loads(value, object_pairs_hook=object_pairs)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _sitecore_policy(board: JobBoard) -> bool:
    identity = _strict_json(board.identifier or "")
    if identity is None or set(identity) != {
        "origin", "path", "site", "language", "country", "brand", "config"
    }:
        return False
    config = identity.get("config")
    if not isinstance(config, dict) or set(config) != {
        "baseSearchQuery", "filtersToDisplay", "brandFromDictionary"
    }:
        return False
    values = [identity[key] for key in ("origin", "path", "site", "language", "country", "brand")]
    values.extend(config.values())
    if not all(
        isinstance(value, str)
        and _valid_identifier_text(value, limit=20_000)
        for value in values
    ):
        return False
    parsed = urlparse(board.url)
    return bool(
        identity["origin"] == f"https://{parsed.netloc}"
        and identity["path"] == (parsed.path or "/")
        and _no_query(board.url)
        and json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        == board.identifier
    )


def _talemetry_policy(board: JobBoard) -> bool:
    identity = _strict_json(board.identifier or "")
    if identity is None or set(identity) not in ({"host"}, {"host", "career_site_id"}):
        return False
    host = identity.get("host")
    career_site_id = identity.get("career_site_id")
    parsed = urlparse(board.url)
    return bool(
        isinstance(host, str)
        and _valid_hostname(host)
        and host.casefold() == _host(board.url)
        and parsed.path == "/"
        and _no_query(board.url)
        and (
            career_site_id is None
            or (
                isinstance(career_site_id, str)
                and career_site_id == career_site_id.strip()
                and _valid_identifier_text(career_site_id, limit=256)
            )
        )
        and json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        == board.identifier
    )


def _smartrecruiters_policy(board: JobBoard) -> bool:
    identifier = board.identifier or ""
    parsed = urlparse(board.url)
    return bool(
        _valid_identifier_text(identifier, limit=256)
        and parsed.hostname == "jobs.smartrecruiters.com"
        and parsed.path == f"/{quote(identifier, safe='-._~')}"
        and _no_query(board.url)
    )


def _workday_policy(board: JobBoard) -> bool:
    identifier = board.identifier or ""
    if identifier.count("/") != 1:
        return False
    tenant, site = identifier.split("/", 1)
    if not _SEGMENT.fullmatch(tenant) or not _SEGMENT.fullmatch(site):
        return False
    parsed = urlparse(board.url)
    host = _host(board.url)
    parts = [part for part in parsed.path.split("/") if part]
    canonical_paths = (
        [site],
        ["recruiting", tenant, site],
        ["wday", "cxs", tenant, site],
    )
    locale_path = bool(
        len(parts) == 2
        and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0])
        and parts[1] == site
    )
    return bool(
        host.startswith(f"{tenant.casefold()}.")
        and host.endswith((".myworkdayjobs.com", ".workdayjobs.com"))
        and (parts in canonical_paths or locale_path)
        and _no_query(board.url)
    )


_REPLAY_SAFE_POLICIES: dict[str, Callable[[JobBoard], bool]] = {
    "avature": _avature_policy,
    "eightfold": _eightfold_policy,
    "greenhouse": _greenhouse_policy,
    "icims": _icims_policy,
    "phenom": _phenom_policy,
    "sitecore_next_jobs": _sitecore_policy,
    "smartrecruiters": _smartrecruiters_policy,
    "talemetry": _talemetry_policy,
    "workday": _workday_policy,
}
