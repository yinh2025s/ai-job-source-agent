from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import re
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, unquote, urlparse


_DETECTION_METHODS = {
    "acquired_brand_handoff",
    "external_apply_url",
    "linked_url_evidence",
    "page_evidence",
    "page_probe",
    "targeted_search",
    "url_evidence",
    "verified_declared_inventory",
    "verified_first_party_action",
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
_CWS_ORG_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}"
    r"(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,79}){0,3}$"
)
_CWS_DETAIL_PATH = re.compile(r"/[A-Za-z0-9][A-Za-z0-9/_-]{0,199}")
_CWS_SORT_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}")
_ORACLE_HOST = re.compile(
    r"^(?P<tenant>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
    r"\.fa(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)?"
    r"\.oraclecloud\.com$"
)
_ORACLE_LOCALE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2})?")
_ORACLE_SITE = re.compile(r"[A-Za-z0-9_-]{1,100}")
_ORACLE_OPENING_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_PEOPLESOFT_NUMBER = re.compile(r"[1-9][0-9]{0,19}")
_PEOPLESOFT_COMPONENT = "HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL"
_ADP_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_ADP_CC_ID = re.compile(r"[0-9]{8}_[0-9]{6}")
_ADP_LOCALE = re.compile(r"[A-Za-z]{2}_[A-Za-z]{2}")
_ADP_POSITIVE_ID = re.compile(r"[1-9][0-9]{0,19}")
_ADP_SITE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}")
_ADP_PRC = re.compile(r"RMPOD[1-9][0-9]?", re.IGNORECASE)
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
    relationship_evidence_url: str | None = None

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
        board_payloads = []
        for discovered in self.boards:
            if not discovered.board.replay_safe:
                break
            payload = discovered.to_checkpoint_payload()
            if payload is None:
                break
            board_payloads.append(payload)
        if not board_payloads:
            return None
        return {
            "schema_version": _PORTFOLIO_SCHEMA_VERSION,
            "boards": board_payloads,
            "eligible_set_complete": (
                self.eligible_set_complete
                and len(board_payloads) == len(self.boards)
            ),
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
    if discovered.relationship_evidence_url is not None and (
        not isinstance(discovered.relationship_evidence_url, str)
        or not _is_public_https_url(discovered.relationship_evidence_url)
    ):
        raise ValueError("Job board relationship evidence URL must be public HTTPS")
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


def _adp_policy(board: JobBoard) -> bool:
    identifier = board.identifier or ""
    parts = identifier.split("|")
    parsed = urlparse(board.url)
    host = _host(board.url)
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False

    if len(parts) == 4 and parts[0] == "wfn":
        _, cid, cc_id, locale = parts
        expected_query = [
            ("cid", cid),
            ("ccId", cc_id),
            ("type", "MP"),
            ("lang", locale),
            ("selectedMenuKey", "CurrentOpenings"),
        ]
        return bool(
            cid == cid.casefold()
            and _ADP_UUID.fullmatch(cid)
            and _ADP_CC_ID.fullmatch(cc_id)
            and _ADP_LOCALE.fullmatch(locale)
            and host == "workforcenow.adp.com"
            and parsed.path == "/mascsr/default/mdf/recruitment/recruitment.html"
            and query == expected_query
        )

    if len(parts) == 5 and parts[0] == "srccar":
        _, client, site_identity, prc_identity, requisition = parts
        if not (
            _ADP_POSITIVE_ID.fullmatch(client)
            and _ADP_SITE.fullmatch(site_identity)
            and site_identity == site_identity.casefold()
            and (not prc_identity or _ADP_PRC.fullmatch(prc_identity))
            and prc_identity == prc_identity.upper()
            and (not requisition or _ADP_POSITIVE_ID.fullmatch(requisition))
        ):
            return False
        expected_query = [("c", client)]
        if len(query) < 2 or query[0] != expected_query[0]:
            return False
        site = query[1][1]
        expected_query.append(("d", site))
        if prc_identity:
            expected_query.append(("prc", prc_identity))
        if requisition:
            expected_query.append(("r", requisition))
        return bool(
            _ADP_SITE.fullmatch(site)
            and site.casefold() == site_identity
            and host == "recruiting.adp.com"
            and parsed.path == "/srccar/public/nghome.guid"
            and query == expected_query
        )

    return False


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


def _healthcaresource_policy(board: JobBoard) -> bool:
    tenant = (board.identifier or "").casefold()
    parsed = urlparse(board.url)
    return bool(
        _SEGMENT.fullmatch(tenant)
        and tenant == board.identifier
        and (parsed.hostname or "").casefold() == f"{tenant}.hcshiring.com"
        and parsed.path == "/jobs"
        and _no_query(board.url)
    )


def _pinpoint_policy(board: JobBoard) -> bool:
    tenant = (board.identifier or "").casefold()
    parsed = urlparse(board.url)
    return bool(
        _SEGMENT.fullmatch(tenant)
        and tenant == board.identifier
        and (parsed.hostname or "").casefold() == f"{tenant}.pinpointhq.com"
        and parsed.path == "/"
        and _no_query(board.url)
    )


def _cws_policy(board: JobBoard) -> bool:
    identity = _strict_json(board.identifier or "")
    base_keys = {
        "api_url",
        "board_url",
        "boost",
        "detail_path",
        "filters",
        "limit",
        "org_id",
        "sort",
    }
    if (
        identity is None
        or set(identity)
        not in {frozenset(base_keys), frozenset(base_keys | {"smartpost_org"})}
    ):
        return False
    api_url = identity.get("api_url")
    detail_path = identity.get("detail_path")
    limit = identity.get("limit")
    org_id = identity.get("org_id")
    smartpost_org = identity.get("smartpost_org")
    filters = identity.get("filters")
    boost = identity.get("boost")
    sort = identity.get("sort")
    if not all(isinstance(value, str) for value in (api_url, detail_path, org_id)):
        return False
    parsed_api = urlparse(api_url)
    parsed_board = urlparse(board.url)
    return bool(
        identity.get("board_url") == board.url
        and not parsed_board.query
        and not parsed_board.fragment
        and _is_public_https_url(api_url)
        and (parsed_api.hostname or "").casefold().endswith(".m-cloud.io")
        and parsed_api.path == "/api/"
        and not parsed_api.query
        and not parsed_api.fragment
        and _CWS_ORG_ID.fullmatch(org_id)
        and (
            smartpost_org is None
            or (
                isinstance(smartpost_org, str)
                and re.fullmatch(r"[0-9]{1,20}", smartpost_org)
            )
        )
        and _CWS_DETAIL_PATH.fullmatch(detail_path)
        and "//" not in detail_path
        and all(segment not in {".", ".."} for segment in detail_path.split("/"))
        and not isinstance(limit, bool)
        and isinstance(limit, int)
        and 1 <= limit <= 100
        and isinstance(filters, list)
        and len(filters) <= 20
        and len(set(filters)) == len(filters)
        and all(_valid_cws_criterion(item) for item in filters)
        and (boost is None or _valid_cws_criterion(boost))
        and (
            sort is None
            or (
                isinstance(sort, list)
                and len(sort) == 2
                and all(isinstance(item, str) for item in sort)
                and _CWS_SORT_FIELD.fullmatch(sort[0])
                and sort[1] in {"ascending", "descending"}
            )
        )
        and json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        == board.identifier
    )


def _valid_cws_criterion(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and value
        and len(value) <= 500
        and not any(ord(character) < 32 for character in value)
        and not _SECRET_VALUE.search(value)
    )


def _oracle_hcm_policy(board: JobBoard) -> bool:
    identity = _strict_json(board.identifier or "")
    required = {"host", "locale", "site", "tenant", "v"}
    detail = {"detail_url", "opening_id"}
    if identity is None or set(identity) not in (required, required | detail):
        return False
    host = identity.get("host")
    locale = identity.get("locale")
    site = identity.get("site")
    tenant = identity.get("tenant")
    version = identity.get("v")
    if not all(isinstance(value, str) for value in (host, locale, site, tenant)):
        return False
    host_match = _ORACLE_HOST.fullmatch(host)
    board_path = f"/hcmUI/CandidateExperience/{locale}/sites/{site}"
    parsed_board = urlparse(board.url)
    if not (
        version == 1
        and host == host.casefold() == _host(board.url)
        and host_match is not None
        and tenant == host_match.group("tenant")
        and _ORACLE_LOCALE.fullmatch(locale)
        and _ORACLE_SITE.fullmatch(site)
        and parsed_board.path == board_path
        and _no_query(board.url)
    ):
        return False
    if set(identity) == required:
        return json.dumps(identity, separators=(",", ":"), sort_keys=True) == board.identifier
    opening_id = identity.get("opening_id")
    detail_url = identity.get("detail_url")
    if not isinstance(opening_id, str) or not _ORACLE_OPENING_ID.fullmatch(opening_id):
        return False
    parsed_detail = urlparse(detail_url) if isinstance(detail_url, str) else None
    return bool(
        parsed_detail is not None
        and _is_public_https_url(detail_url)
        and _host(detail_url) == host
        and parsed_detail.path == f"{board_path}/job/{opening_id}"
        and _no_query(detail_url)
        and json.dumps(identity, separators=(",", ":"), sort_keys=True)
        == board.identifier
    )


def _phenom_policy(board: JobBoard) -> bool:
    parts = [part.casefold() for part in urlparse(board.url).path.split("/") if part]
    return bool(
        _PHENOM_IDENTIFIER.fullmatch(board.identifier or "")
        and parts
        and parts[-1] == "search-results"
        and _no_query(board.url)
    )


def _catsone_policy(board: JobBoard) -> bool:
    identity = _strict_json(board.identifier or "")
    if identity is None or set(identity) not in (
        {"domain", "portal_id"},
        {"domain", "portal_id", "public_host"},
    ):
        return False
    domain = identity.get("domain")
    portal_id = identity.get("portal_id")
    if domain not in {"catsone.com", "catsone.nl"} or not (
        isinstance(portal_id, str)
        and re.fullmatch(r"[1-9][0-9]{0,18}", portal_id)
    ):
        return False
    parsed = urlparse(board.url)
    host = _host(board.url)
    public_host = identity.get("public_host")
    if public_host is None:
        return bool(
            host == f"app.{domain}"
            and parsed.path == "/portal"
            and parse_qsl(parsed.query, keep_blank_values=True) == [("id", portal_id)]
        )
    if not isinstance(public_host, str) or public_host != public_host.casefold():
        return False
    route = re.fullmatch(
        rf"/careers/{re.escape(portal_id)}(?:-[A-Za-z0-9][A-Za-z0-9-]{{0,240}})?/",
        parsed.path,
    )
    return bool(host == public_host and route and _no_query(board.url))


def _peoplesoft_policy(board: JobBoard) -> bool:
    identity = _strict_json(board.identifier or "")
    required = {
        "host",
        "job_opening_id",
        "kind",
        "node",
        "portal",
        "posting_seq",
        "site_id",
        "v",
    }
    if identity is None or set(identity) != required or identity.get("v") != 1:
        return False
    host = identity.get("host")
    portal = identity.get("portal")
    node = identity.get("node")
    site_id = identity.get("site_id")
    kind = identity.get("kind")
    opening_id = identity.get("job_opening_id")
    posting_seq = identity.get("posting_seq")
    if not (
        isinstance(host, str)
        and host == host.casefold() == _host(board.url)
        and _HOSTNAME.fullmatch(host)
        and isinstance(portal, str)
        and _SEGMENT.fullmatch(portal)
        and isinstance(node, str)
        and _SEGMENT.fullmatch(node)
        and isinstance(site_id, str)
        and _PEOPLESOFT_NUMBER.fullmatch(site_id)
        and kind in {"search", "detail"}
    ):
        return False
    parsed = urlparse(board.url)
    expected_path = (
        f"/psc/{portal}/EMPLOYEE/{node}/c/{_PEOPLESOFT_COMPONENT}"
    )
    if parsed.path != expected_path or parsed.fragment:
        return False
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    expected = [
        ("Page", "HRS_APP_JBPST_FL" if kind == "detail" else "HRS_APP_SCHJOB_FL"),
        ("Action", "U"),
        ("SiteId", site_id),
        ("FOCUS", "Applicant"),
    ]
    if kind == "search":
        if opening_id is not None or posting_seq is not None:
            return False
    else:
        if not isinstance(opening_id, str) or not _PEOPLESOFT_NUMBER.fullmatch(opening_id):
            return False
        expected.append(("JobOpeningId", opening_id))
        if posting_seq is not None:
            if not isinstance(posting_seq, str) or not _PEOPLESOFT_NUMBER.fullmatch(posting_seq):
                return False
            expected.append(("PostingSeq", posting_seq))
    return bool(
        query == expected
        and json.dumps(identity, separators=(",", ":"), sort_keys=True)
        == board.identifier
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


def _talentbrew_policy(board: JobBoard) -> bool:
    identity = _strict_json(board.identifier or "")
    if identity is None or set(identity) != {"host", "locale", "site_id", "tenant_id"}:
        return False
    host = identity.get("host")
    locale = identity.get("locale")
    site_id = identity.get("site_id")
    tenant_id = identity.get("tenant_id")
    parsed = urlparse(board.url)
    return bool(
        isinstance(host, str)
        and _valid_hostname(host)
        and host.casefold() == host == _host(board.url)
        and isinstance(locale, str)
        and re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", locale)
        and isinstance(site_id, str)
        and re.fullmatch(r"[1-9][0-9]{0,11}", site_id)
        and isinstance(tenant_id, str)
        and re.fullmatch(r"[1-9][0-9]{0,11}", tenant_id)
        and parsed.path == f"/{locale}/search-jobs"
        and _no_query(board.url)
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


def _digitalrecruiters_policy(board: JobBoard) -> bool:
    identity = _strict_json(board.identifier or "")
    if identity is None or set(identity) != {"api_base", "board_url", "locale", "tenant"}:
        return False
    tenant = identity.get("tenant")
    locale = identity.get("locale")
    parsed = urlparse(board.url)
    return bool(
        identity.get("api_base") == "https://api.digitalrecruiters.com/public/v1"
        and parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and not parsed.fragment
        and isinstance(tenant, str)
        and _valid_hostname(tenant)
        and tenant.casefold() == tenant == _host(board.url)
        and isinstance(locale, str)
        and re.fullmatch(r"[a-z]{2}(?:_[A-Z]{2})?", locale)
        and parsed.path == f"/{locale}/annonces"
        and _no_query(board.url)
        and identity.get("board_url") == board.url
        and json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        == board.identifier
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
        and re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", parts[0])
        and parts[1] == site
    )
    return bool(
        host.startswith(f"{tenant.casefold()}.")
        and host.endswith((".myworkdayjobs.com", ".workdayjobs.com"))
        and (parts in canonical_paths or locale_path)
        and _no_query(board.url)
    )


_REPLAY_SAFE_POLICIES: dict[str, Callable[[JobBoard], bool]] = {
    "adp": _adp_policy,
    "avature": _avature_policy,
    "catsone": _catsone_policy,
    "cws": _cws_policy,
    "digitalrecruiters": _digitalrecruiters_policy,
    "eightfold": _eightfold_policy,
    "greenhouse": _greenhouse_policy,
    "healthcaresource": _healthcaresource_policy,
    "icims": _icims_policy,
    "oracle_hcm": _oracle_hcm_policy,
    "peoplesoft": _peoplesoft_policy,
    "pinpoint": _pinpoint_policy,
    "phenom": _phenom_policy,
    "sitecore_next_jobs": _sitecore_policy,
    "smartrecruiters": _smartrecruiters_policy,
    "talentbrew": _talentbrew_policy,
    "talemetry": _talemetry_policy,
    "workday": _workday_policy,
}
