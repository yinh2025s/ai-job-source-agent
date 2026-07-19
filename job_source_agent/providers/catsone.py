from __future__ import annotations

from html.parser import HTMLParser
import ipaddress
import json
import re
from urllib.parse import parse_qsl, urlencode, urlparse

from ..fetch_failure import project_fetch_error
from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_SUPPORTED_DOMAINS = frozenset({"catsone.com", "catsone.nl"})
_WIDGET_PATH = "/resources/entry-jobwidget.js"
_PORTAL_PATH = "/portal"
_PORTAL_ID = re.compile(r"^[1-9][0-9]{0,18}$")
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$",
    re.IGNORECASE,
)
_PUBLIC_PATH = re.compile(
    r"^/careers/(?P<portal>[1-9][0-9]{0,18})"
    r"(?:-(?P<board_slug>[A-Za-z0-9][A-Za-z0-9-]{0,240}))?"
    r"(?:/jobs/(?P<job>[1-9][0-9]{0,18})"
    r"-(?P<job_slug>[A-Za-z0-9][A-Za-z0-9-]{0,600}))?/?$"
)
_CONFIG_CALL = re.compile(r"\bcjw\s*\(\s*")
_MAX_HTML_CHARS = 2_000_000
_MAX_RESPONSE_CHARS = 8_000_000
_MAX_RECORDS = 2_000
_MAX_FIELD_CHARS = 1_000
_AUTH_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "cookie",
        "password",
        "secret",
        "session",
        "token",
    }
)
_TRACKING_QUERY_KEYS = frozenset(
    {"source", "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term"}
)


class CatsoneAdapter:
    name = "catsone"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return self.identify_board(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        portal_identity = _portal_endpoint_identity(url)
        if portal_identity is not None:
            portal_id, domain = portal_identity
            return _locator_board(portal_id, domain)

        parsed = _safe_url(url)
        if parsed is None:
            return None
        host = (parsed.hostname or "").casefold()
        domain = next(
            (
                domain
                for domain in _SUPPORTED_DOMAINS
                if host.endswith(f".{domain}")
            ),
            None,
        )
        if domain is None or not _valid_provider_tenant_host(host, domain):
            return None
        route = _public_route(parsed.path)
        if route is None or not _safe_tracking_query(parsed.query):
            return None
        portal_id, board_path, _ = route
        return _public_board(portal_id, domain, host, board_path)

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        if _safe_public_page_url(page.final_url or page.url) is None:
            return None
        identity = _widget_identity(page.html)
        if identity is None:
            return None
        portal_id, domain = identity
        return _locator_board(portal_id, domain)

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identity = _board_identity(board)
        if identity is None:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="invalid CATS One board identity",
            )
        portal_id, domain, expected_public_host = identity
        inventory_url = _portal_url(portal_id, domain)
        try:
            page = fetcher.fetch(
                inventory_url,
                headers={"Accept": "application/json"},
            )
        except (FetchError, OSError, TimeoutError) as error:
            reason_code, retryable = _fetch_classification(error)
            return _result(
                board,
                reason_code=reason_code,
                retryable=retryable,
                inventory_complete=False,
                error=str(error),
                api_urls=[inventory_url],
            )

        final_url = page.final_url or page.url
        if _portal_endpoint_identity(final_url) != (portal_id, domain):
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="CATS One inventory redirected away from the tenant-bound endpoint",
                api_urls=[inventory_url],
                rejected_final_url=final_url,
                response_source=page.source,
            )

        parsed = _inventory(page.html, portal_id, domain, expected_public_host)
        if isinstance(parsed, str):
            bounded = parsed in {"response_cap_exceeded", "record_cap_exceeded"}
            return _result(
                board,
                reason_code=(
                    "FETCH_BUDGET_EXHAUSTED" if bounded else "INVALID_STRUCTURED_DATA"
                ),
                retryable=bounded,
                inventory_complete=False,
                error="invalid or incomplete CATS One public inventory",
                api_urls=[inventory_url],
                response_source=page.source,
                stop_reason=parsed,
            )

        public_host, board_path, records, site_id = parsed
        canonical_board = _public_board(portal_id, domain, public_host, board_path)
        candidates: list[JobCandidate] = []
        hidden_records = 0
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        for record in records:
            candidate = _candidate(record, portal_id, public_host, board_path, site_id)
            if candidate is None:
                return _result(
                    canonical_board,
                    reason_code="INVALID_STRUCTURED_DATA",
                    inventory_complete=False,
                    error="CATS One inventory contained a malformed or cross-tenant job",
                    board_urls=[canonical_board.url],
                    api_urls=[inventory_url],
                    response_source=page.source,
                    stop_reason="invalid_job_record",
                )
            job_id, is_hidden, opening = candidate
            if job_id in seen_ids or opening.url in seen_urls:
                return _result(
                    canonical_board,
                    reason_code="INVALID_STRUCTURED_DATA",
                    inventory_complete=False,
                    error="CATS One inventory contained a duplicate job",
                    board_urls=[canonical_board.url],
                    api_urls=[inventory_url],
                    response_source=page.source,
                    stop_reason="duplicate_job",
                )
            seen_ids.add(job_id)
            seen_urls.add(opening.url)
            if is_hidden:
                hidden_records += 1
            else:
                candidates.append(opening)

        target = _normalized_title(query.title)
        return _result(
            canonical_board,
            candidates=candidates,
            reason_code="EMPTY_PROVIDER_RESPONSE" if not candidates else None,
            inventory_complete=True,
            board_urls=[canonical_board.url],
            api_urls=[inventory_url],
            response_source=page.source,
            records_seen=len(records),
            candidate_count=len(candidates),
            hidden_records=hidden_records,
            exact_title_found=bool(
                target
                and any(_normalized_title(candidate.title) == target for candidate in candidates)
            ),
        )


class _WidgetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.element_ids: set[str] = set()
        self.has_official_script = False
        self.inline_scripts: list[str] = []
        self._inside_inline_script = False
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): value for name, value in attrs}
        element_id = values.get("id")
        if isinstance(element_id, str):
            self.element_ids.add(element_id)
        if tag.casefold() != "script":
            return
        source = values.get("src")
        if source is None:
            self._inside_inline_script = True
            self._script_parts = []
        elif _is_widget_script_url(source):
            self.has_official_script = True

    def handle_data(self, data: str) -> None:
        if self._inside_inline_script:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._inside_inline_script:
            self.inline_scripts.append("".join(self._script_parts))
            self._inside_inline_script = False
            self._script_parts = []


def _widget_identity(html: str) -> tuple[str, str] | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    parser = _WidgetParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    if not parser.has_official_script:
        return None

    identities: list[tuple[str, str]] = []
    decoder = json.JSONDecoder()
    for script in parser.inline_scripts:
        for match in _CONFIG_CALL.finditer(script):
            try:
                config, end = decoder.raw_decode(script, match.end())
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            remainder = script[end:].lstrip()
            identity = _config_identity(config, parser.element_ids)
            if not remainder.startswith(")") or identity is None:
                continue
            identities.append(identity)
    unique = list(dict.fromkeys(identities))
    return unique[0] if len(unique) == 1 else None


def _config_identity(
    config: object,
    element_ids: set[str],
) -> tuple[str, str] | None:
    if not isinstance(config, dict) or any(
        str(key).casefold() in _AUTH_KEYS for key in config
    ):
        return None
    portal_id = _positive_id(config.get("id"))
    domain = config.get("domain")
    target = config.get("target")
    if (
        portal_id is None
        or not isinstance(domain, str)
        or domain.casefold() not in _SUPPORTED_DOMAINS
        or domain != domain.casefold()
        or not isinstance(target, str)
        or not target.startswith("#")
        or target[1:] not in element_ids
    ):
        return None
    return portal_id, domain


def _is_widget_script_url(url: object) -> bool:
    parsed = _safe_url(url)
    return bool(
        parsed is not None
        and (parsed.hostname or "").casefold()
        in {f"app.{domain}" for domain in _SUPPORTED_DOMAINS}
        and parsed.path == _WIDGET_PATH
        and not parsed.query
    )


def _safe_url(url: object):
    if not isinstance(url, str) or len(url) > 8_192:
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None
    return parsed


def _safe_public_page_url(url: object):
    parsed = _safe_url(url)
    if parsed is None or _safe_public_host(parsed.hostname) is None:
        return None
    return parsed


def _safe_public_host(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 253:
        return None
    host = value.rstrip(".").casefold()
    if (
        not _HOSTNAME.fullmatch(host)
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal", ".invalid", ".test"))
    ):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    return None


def _valid_provider_tenant_host(host: str, domain: str) -> bool:
    suffix = f".{domain}"
    if not host.endswith(suffix):
        return False
    tenant = host[: -len(suffix)]
    return bool(_HOST_LABEL.fullmatch(tenant) and tenant not in {"api", "app", "www"})


def _portal_endpoint_identity(url: object) -> tuple[str, str] | None:
    parsed = _safe_url(url)
    if parsed is None or parsed.path != _PORTAL_PATH:
        return None
    host = (parsed.hostname or "").casefold()
    domain = next(
        (domain for domain in _SUPPORTED_DOMAINS if host == f"app.{domain}"),
        None,
    )
    if domain is None:
        return None
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    if len(query) != 1 or query[0][0] != "id":
        return None
    portal_id = _positive_id(query[0][1])
    return (portal_id, domain) if portal_id is not None else None


def _safe_tracking_query(query: str) -> bool:
    if not query:
        return True
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    return bool(
        0 < len(pairs) <= 10
        and all(
            key.casefold() in _TRACKING_QUERY_KEYS
            and 0 < len(value) <= 500
            and not any(ord(character) < 32 for character in value)
            for key, value in pairs
        )
    )


def _public_route(path: str) -> tuple[str, str, str | None] | None:
    if "%" in path or "//" in path:
        return None
    match = _PUBLIC_PATH.fullmatch(path)
    if match is None:
        return None
    portal_id = match.group("portal")
    board_slug = match.group("board_slug")
    job_id = match.group("job")
    if job_id is not None and board_slug is None:
        return None
    board_path = f"/careers/{portal_id}"
    if board_slug is not None:
        board_path += f"-{board_slug}"
    return portal_id, f"{board_path}/", job_id


def _portal_url(portal_id: str, domain: str) -> str:
    return f"https://app.{domain}{_PORTAL_PATH}?{urlencode({'id': portal_id})}"


def _identifier(
    portal_id: str,
    domain: str,
    public_host: str | None = None,
) -> str:
    value = {"domain": domain, "portal_id": portal_id}
    if public_host is not None:
        value["public_host"] = public_host
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _locator_board(portal_id: str, domain: str) -> JobBoard:
    return JobBoard(
        url=_portal_url(portal_id, domain),
        provider="catsone",
        identifier=_identifier(portal_id, domain),
        replay_safe=True,
    )


def _public_board(
    portal_id: str,
    domain: str,
    public_host: str,
    board_path: str,
) -> JobBoard:
    return JobBoard(
        url=f"https://{public_host}{board_path}",
        provider="catsone",
        identifier=_identifier(portal_id, domain, public_host),
        replay_safe=True,
    )


def _board_identity(board: JobBoard) -> tuple[str, str, str | None] | None:
    if board.provider != "catsone" or not isinstance(board.identifier, str):
        return None
    try:
        value = json.loads(board.identifier)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or set(value) not in (
        {"domain", "portal_id"},
        {"domain", "portal_id", "public_host"},
    ):
        return None
    portal_id = _positive_id(value.get("portal_id"))
    domain = value.get("domain")
    public_host = value.get("public_host")
    if (
        portal_id is None
        or domain not in _SUPPORTED_DOMAINS
        or (public_host is not None and _safe_public_host(public_host) != public_host)
    ):
        return None
    if public_host is None:
        if board.url != _portal_url(portal_id, domain):
            return None
    else:
        parsed = _safe_url(board.url)
        route = _public_route(parsed.path) if parsed is not None else None
        if (
            parsed is None
            or (parsed.hostname or "").casefold() != public_host
            or parsed.query
            or route is None
            or route[0] != portal_id
            or route[2] is not None
        ):
            return None
    return portal_id, domain, public_host


def _inventory(
    raw: str,
    portal_id: str,
    domain: str,
    expected_public_host: str | None,
) -> tuple[str, str, list[dict], str] | str:
    if not isinstance(raw, str):
        return "invalid_json"
    if len(raw) > _MAX_RESPONSE_CHARS:
        return "response_cap_exceeded"
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "invalid_json"
    if not isinstance(payload, dict):
        return "invalid_schema"
    response_id = _positive_id(payload.get("id"))
    site_id = _positive_id(payload.get("site_id"))
    public_host = _safe_public_host(payload.get("host"))
    internal_host = payload.get("internalHost")
    listing_ids = payload.get("job_listings_ids")
    records = payload.get("jobs")
    if (
        response_id != portal_id
        or site_id is None
        or public_host is None
        or (expected_public_host is not None and public_host != expected_public_host)
        or not isinstance(internal_host, str)
        or not _valid_provider_tenant_host(internal_host.casefold(), domain)
        or not _valid_listing_ids(listing_ids, portal_id)
        or not isinstance(records, list)
        or any(not isinstance(record, dict) for record in records)
    ):
        return "identity_or_schema_mismatch"
    if len(records) > _MAX_RECORDS:
        return "record_cap_exceeded"
    if not _complete_one_shot_payload(payload, len(records)):
        return "incomplete_pagination"

    board_paths: set[str] = set()
    for record in records:
        route = _detail_route(record.get("url"), public_host, portal_id)
        if route is None:
            return "cross_tenant_detail"
        board_paths.add(route[0])
    if len(board_paths) > 1:
        return "cross_tenant_board_path"
    if board_paths:
        board_path = next(iter(board_paths))
    else:
        board_path = f"/careers/{portal_id}/"
    return public_host, board_path, records, site_id


def _valid_listing_ids(value: object, portal_id: str) -> bool:
    if not isinstance(value, list) or not value or len(value) > 100:
        return False
    normalized = [_positive_id(item) for item in value]
    return (
        all(item is not None for item in normalized)
        and len(set(normalized)) == len(normalized)
        and portal_id in normalized
    )


def _complete_one_shot_payload(payload: dict, record_count: int) -> bool:
    for key in ("next", "next_page", "next_cursor", "cursor"):
        if payload.get(key) is not None and payload.get(key) != "":
            return False
    if payload.get("has_more") is not None and payload.get("has_more") is not False:
        return False
    for key in ("total", "total_count", "count"):
        if key in payload and _bounded_nonnegative_int(payload[key]) != record_count:
            return False
    pagination = payload.get("pagination")
    if pagination is None:
        return True
    if not isinstance(pagination, dict):
        return False
    if pagination.get("has_more") is not None and pagination.get("has_more") is not False:
        return False
    for key in ("next", "next_page", "next_cursor", "cursor"):
        if pagination.get(key) is not None and pagination.get(key) != "":
            return False
    for key in ("total", "total_count", "count"):
        if key in pagination and _bounded_nonnegative_int(pagination[key]) != record_count:
            return False
    for key in ("page", "current_page", "page_count", "pages", "total_pages"):
        if key in pagination and _bounded_nonnegative_int(pagination[key]) != 1:
            return False
    return True


def _candidate(
    record: dict,
    portal_id: str,
    public_host: str,
    board_path: str,
    site_id: str,
) -> tuple[str, bool, JobCandidate] | None:
    job_id = _positive_id(record.get("id"))
    record_site_id = _positive_id(record.get("site_id"))
    title = _field(record.get("title"), required=True)
    is_hidden = record.get("is_hidden")
    route = _detail_route(record.get("url"), public_host, portal_id)
    location = _location(record.get("location"))
    if (
        job_id is None
        or record_site_id != site_id
        or title is None
        or not isinstance(is_hidden, bool)
        or route is None
        or route[0] != board_path
        or route[1] != job_id
        or location is False
    ):
        return None
    canonical_url = route[2]
    return (
        job_id,
        is_hidden,
        JobCandidate(
            title=title,
            url=canonical_url,
            provider="catsone",
            location=location,
            raw={"job_id": job_id, "portal_id": portal_id, "site_id": site_id},
        ),
    )


def _detail_route(
    url: object,
    public_host: str,
    portal_id: str,
) -> tuple[str, str, str] | None:
    parsed = _safe_url(url)
    if (
        parsed is None
        or (parsed.hostname or "").casefold() != public_host
        or parsed.query
    ):
        return None
    route = _public_route(parsed.path)
    if route is None or route[0] != portal_id or route[2] is None:
        return None
    canonical_path = parsed.path.rstrip("/") + "/"
    board_path = canonical_path.split("/jobs/", 1)[0] + "/"
    return board_path, route[2], f"https://{public_host}{canonical_path}"


def _location(value: object) -> str | None | bool:
    if value is None:
        return None
    if not isinstance(value, dict):
        return False
    parts: list[str] = []
    for key in ("city", "state", "country"):
        field = _field(value.get(key), required=False)
        if field is None and value.get(key) is not None and value.get(key) != "":
            return False
        if field:
            parts.append(field)
    return ", ".join(parts) or None


def _field(value: object, *, required: bool) -> str | None:
    if not isinstance(value, str) or len(value) > _MAX_FIELD_CHARS:
        return None
    cleaned = " ".join(value.split())
    if required and not cleaned:
        return None
    if any(ord(character) < 32 for character in cleaned):
        return None
    return cleaned


def _positive_id(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    return text if _PORTAL_ID.fullmatch(text) else None


def _bounded_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value if value <= _MAX_RECORDS else None


def _fetch_classification(error: Exception) -> tuple[str, bool]:
    if isinstance(error, FetchError):
        projection = project_fetch_error(error)
        reason_code = projection["reason_code"]
        retryable = projection["retryable"]
    else:
        reason_code = classify_fetch_error(str(error))
        retryable = reason_spec(reason_code).retryable
    if reason_code == "FETCH_FAILED":
        return "PROVIDER_FETCH_FAILED", True
    return reason_code, retryable


def _normalized_title(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if value else ""


def _result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None,
    retryable: bool = False,
    inventory_complete: bool,
    error: str | None = None,
    board_urls: list[str] | None = None,
    api_urls: list[str] | None = None,
    response_source: str | None = None,
    rejected_final_url: str | None = None,
    stop_reason: str | None = None,
    records_seen: int | None = None,
    candidate_count: int | None = None,
    hidden_records: int | None = None,
    exact_title_found: bool | None = None,
) -> AdapterResult:
    trace = {
        "adapter": "catsone",
        "variant": "public_job_widget",
        "board_urls": board_urls or [],
        "api_urls": api_urls or [],
        "request_count": len(api_urls or []),
        "page_count": 1 if response_source is not None else 0,
        "inventory_scope": "full" if inventory_complete else "unknown",
        "inventory_complete": inventory_complete,
    }
    optional = {
        "error": error,
        "response_source": response_source,
        "rejected_final_url": rejected_final_url,
        "stop_reason": stop_reason,
        "records_seen": records_seen,
        "candidate_count": candidate_count,
        "hidden_records": hidden_records,
        "exact_title_found": exact_title_found,
    }
    trace.update({key: value for key, value in optional.items() if value is not None})
    return AdapterResult(
        provider="catsone",
        board=board,
        candidates=candidates or [],
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope="full" if inventory_complete else "unknown",
        inventory_complete=inventory_complete,
        trace=trace,
    )


ADAPTER = CatsoneAdapter()
