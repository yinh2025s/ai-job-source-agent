from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urlparse, urlunparse

from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_API_BASE = "https://api.digitalrecruiters.com/public/v1"
_LIST_PATH = "/careers-site/job-ads"
_LIMIT = 20
_MAX_PAGES = 10
_MAX_ROWS = _LIMIT * _MAX_PAGES
_MAX_RESPONSE_CHARS = 2_000_000
_TENANT = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_LOCALE = re.compile(r"^[a-z]{2}(?:_[A-Z]{2})?$")
_ROW_ID = re.compile(r"^(?P<job_id>[1-9]\d*)-(?P<address_id>[1-9]\d*)$")
_SLUG = re.compile(r"^[1-9]\d*-[a-z0-9]+(?:-[a-z0-9]+)*$")

_DATABASE_LOCALES = {
    "de": "de_DE",
    "en": "en_GB",
    "es": "es_ES",
    "fr": "fr_FR",
    "it": "it_IT",
    "nl": "nl_BE",
    "pt": "pt_PT",
}


class DigitalRecruitersAdapter:
    name = "digitalrecruiters"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return False

    def identify_board(self, url: str) -> JobBoard | None:
        return None

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        parsed = _safe_page_url(page_url)
        if parsed is None or not isinstance(page.html, str) or len(page.html) > _MAX_RESPONSE_CHARS:
            return None

        tenants = _stylesheet_tenants(page.html)
        hostname = (parsed.hostname or "").casefold()
        if tenants != {hostname}:
            return None

        locale = _locale_from_path(parsed.path)
        if locale is None:
            locale = "en"
        board_url = urlunparse(parsed._replace(path=f"/{locale}/annonces", query="", fragment=""))
        identifier = json.dumps(
            {
                "api_base": _API_BASE,
                "board_url": board_url,
                "locale": locale,
                "tenant": hostname,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return JobBoard(
            url=board_url,
            provider=self.name,
            identifier=identifier,
            replay_safe=True,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identity = _board_identity(board)
        title = " ".join((query.title or "").split())
        if identity is None or not title or len(title) > 200 or _has_controls(title):
            return _failure(board, "PROVIDER_VARIANT_UNSUPPORTED", "invalid_board_or_title")
        tenant, locale, board_url = identity

        candidates: list[JobCandidate] = []
        seen_ids: set[str] = set()
        expected_total: int | None = None
        records_seen = 0
        pages_fetched = 0
        stop_reason = "not_started"
        failure_reason: str | None = None
        response_source: str | None = None

        body = json.dumps(
            {"filters": {}, "q": quote(title, safe="")},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        for page_number in range(1, _MAX_PAGES + 1):
            request_url = _request_url(tenant, locale, page_number)
            try:
                response = fetcher.fetch(request_url, data=body, headers=headers)
            except (FetchError, OSError, TimeoutError) as error:
                failure_reason = _fetch_reason(error)
                stop_reason = "fetch_failed"
                break
            pages_fetched += 1
            if not _same_endpoint(response.final_url or response.url, request_url):
                failure_reason = "PROVIDER_VARIANT_UNSUPPORTED"
                stop_reason = "unsafe_response_url"
                break
            response_source = response_source or response.source
            parsed_page = _inventory_page(response.html, tenant)
            if isinstance(parsed_page, str):
                failure_reason = "INVALID_STRUCTURED_DATA"
                stop_reason = parsed_page
                break
            rows, total = parsed_page
            if expected_total is None:
                expected_total = total
                if total > _MAX_ROWS:
                    failure_reason = "FETCH_BUDGET_EXHAUSTED"
                    stop_reason = "row_cap_exceeded"
                    break
            elif total != expected_total:
                failure_reason = "INVALID_STRUCTURED_DATA"
                stop_reason = "contradictory_total"
                break

            remaining = total - records_seen
            expected_rows = min(_LIMIT, remaining)
            if remaining < 0 or len(rows) != expected_rows:
                failure_reason = "INVALID_STRUCTURED_DATA"
                stop_reason = "pagination_count_mismatch"
                break

            page_candidates: list[JobCandidate] = []
            for row in rows:
                candidate = _candidate(row, board_url, tenant, locale)
                if candidate is None:
                    failure_reason = "INVALID_STRUCTURED_DATA"
                    stop_reason = "invalid_or_cross_tenant_record"
                    break
                row_id = str(candidate.raw["row_id"])
                if row_id in seen_ids:
                    failure_reason = "INVALID_STRUCTURED_DATA"
                    stop_reason = "duplicate_job_id"
                    break
                seen_ids.add(row_id)
                page_candidates.append(candidate)
            if failure_reason is not None:
                break
            candidates.extend(page_candidates)
            records_seen += len(rows)
            if records_seen == total:
                stop_reason = "complete"
                break
        else:
            failure_reason = "FETCH_BUDGET_EXHAUSTED"
            stop_reason = "page_cap_reached"

        complete = failure_reason is None and expected_total == records_seen
        if not complete and failure_reason is None:
            failure_reason = "FETCH_BUDGET_EXHAUSTED"
            stop_reason = "page_cap_reached"
        reason_code = failure_reason
        if complete and not candidates:
            reason_code = "EMPTY_PROVIDER_RESPONSE"

        trace = {
            "adapter": self.name,
            "variant": "public_v1_title_filtered",
            "tenant": tenant,
            "page_count": pages_fetched,
            "records_seen": records_seen,
            "total": expected_total,
            "candidate_count": len(candidates),
            "response_source": response_source,
            "stop_reason": stop_reason,
            "inventory_scope": "title_filtered",
            "inventory_complete": complete,
            "exposed_candidate_count": len(candidates) if complete else 0,
        }
        if failure_reason:
            trace["error_classification"] = failure_reason
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates if complete else [],
            reason_code=reason_code,
            retryable=reason_spec(reason_code).retryable,
            inventory_scope="title_filtered",
            inventory_complete=complete,
            trace=trace,
        )


def _safe_page_url(url: str):
    try:
        parsed = urlparse(url)
        _ = parsed.port
    except (TypeError, ValueError):
        return None
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not _TENANT.fullmatch(hostname)
        or parsed.username
        or parsed.password
        or parsed.port is not None
    ):
        return None
    return parsed


class _StylesheetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tenants: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "link":
            return
        values = {name.casefold(): value for name, value in attrs if value is not None}
        rel = values.get("rel", "").casefold().split()
        href = values.get("href")
        if "stylesheet" not in rel or not href:
            return
        tenant = _provider_stylesheet_tenant(href)
        if tenant is not None:
            self.tenants.add(tenant)


def _stylesheet_tenants(raw: str) -> set[str]:
    parser = _StylesheetParser()
    try:
        parser.feed(raw)
        parser.close()
    except (TypeError, ValueError):
        return set()
    return parser.tenants


def _provider_stylesheet_tenant(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        _ = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != "api.digitalrecruiters.com"
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.fragment
    ):
        return None
    prefix = "/careers/v1/careers-sites/"
    if not parsed.path.startswith(prefix) or not parsed.path.endswith("/css"):
        return None
    tenant = parsed.path[len(prefix) : -len("/css")].casefold()
    return tenant if _TENANT.fullmatch(tenant) else None


def _locale_from_path(path: str) -> str | None:
    first = next((part for part in path.split("/") if part), None)
    return first if first and _LOCALE.fullmatch(first) else None


def _board_identity(board: JobBoard) -> tuple[str, str, str] | None:
    if board.provider != "digitalrecruiters" or not isinstance(board.identifier, str):
        return None
    try:
        value = json.loads(board.identifier)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != {"api_base", "board_url", "locale", "tenant"}:
        return None
    tenant = value.get("tenant")
    locale = value.get("locale")
    board_url = value.get("board_url")
    parsed = _safe_page_url(board_url) if isinstance(board_url, str) else None
    if (
        value.get("api_base") != _API_BASE
        or not isinstance(tenant, str)
        or parsed is None
        or parsed.hostname.casefold() != tenant.casefold()
        or not isinstance(locale, str)
        or not _LOCALE.fullmatch(locale)
        or parsed.path.rstrip("/") != f"/{locale}/annonces"
        or not isinstance(board.url, str)
        or board.url.rstrip("/") != board_url.rstrip("/")
    ):
        return None
    return tenant.casefold(), locale, board_url


def _request_url(tenant: str, locale: str, page: int) -> str:
    database_locale = _DATABASE_LOCALES.get(locale, locale)
    query = urlencode(
        {
            "domainName": tenant,
            "limit": _LIMIT,
            "page": page,
            "locale": database_locale,
        }
    )
    return f"{_API_BASE}{_LIST_PATH}?{query}"


def _same_endpoint(actual: str, expected: str) -> bool:
    try:
        left = urlparse(actual)
        right = urlparse(expected)
        _ = left.port
        _ = right.port
        return (
            (
                left.scheme,
                left.hostname,
                left.port,
                left.path,
                left.query,
                left.fragment,
            )
            == (
                right.scheme,
                right.hostname,
                right.port,
                right.path,
                right.query,
                right.fragment,
            )
            and not left.username
            and not left.password
        )
    except (TypeError, ValueError):
        return False


def _inventory_page(raw: str, tenant: str):
    if not isinstance(raw, str) or len(raw) > _MAX_RESPONSE_CHARS:
        return "invalid_response_size"
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return "invalid_response_schema"
    if not isinstance(payload, dict) or set(payload) < {"count", "items"}:
        return "invalid_response_schema"
    total = payload.get("count")
    rows = payload.get("items")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0 or not isinstance(rows, list):
        return "invalid_response_schema"
    if any(not isinstance(row, dict) for row in rows):
        return "invalid_response_schema"
    if any(
        not isinstance(row.get("career_domain"), str)
        or row["career_domain"].casefold() != tenant
        for row in rows
    ):
        return "cross_tenant_response"
    return rows, total


def _candidate(row: dict, board_url: str, tenant: str, locale: str) -> JobCandidate | None:
    row_id = row.get("id")
    job_id = row.get("job_ad_id")
    title = row.get("title")
    location = row.get("location")
    slug = row.get("url")
    career_domain = row.get("career_domain")
    match = _ROW_ID.fullmatch(row_id) if isinstance(row_id, str) else None
    if (
        match is None
        or isinstance(job_id, bool)
        or not isinstance(job_id, int)
        or str(job_id) != match.group("job_id")
        or not isinstance(title, str)
        or not title.strip()
        or len(title) > 300
        or _has_controls(title)
        or (location is not None and not isinstance(location, str))
        or (isinstance(location, str) and (len(location) > 300 or _has_controls(location)))
        or not isinstance(slug, str)
        or not _SLUG.fullmatch(slug)
        or not slug.startswith(f"{job_id}-")
        or not isinstance(career_domain, str)
        or career_domain.casefold() != tenant
        or row.get("is_external") is not False
    ):
        return None
    origin = urlunparse(urlparse(board_url)._replace(path="", query="", fragment="")).rstrip("/")
    return JobCandidate(
        title=" ".join(title.split()),
        location=" ".join(location.split()) if location else None,
        url=f"{origin}/{locale}/annonce/{slug}",
        provider="digitalrecruiters",
        raw={"row_id": row_id, "job_id": job_id, "address_id": match.group("address_id")},
    )


def _has_controls(value: str) -> bool:
    return any(ord(char) < 32 for char in value)


def _fetch_reason(error: Exception) -> str:
    typed = getattr(error, "reason_code", None)
    if isinstance(typed, str) and typed:
        return typed
    classified = classify_fetch_error(str(error))
    return "PROVIDER_FETCH_FAILED" if classified == "FETCH_FAILED" else classified


def _failure(board: JobBoard, reason_code: str, stop_reason: str) -> AdapterResult:
    return AdapterResult(
        provider="digitalrecruiters",
        board=board,
        reason_code=reason_code,
        retryable=reason_spec(reason_code).retryable,
        inventory_scope="title_filtered",
        inventory_complete=False,
        trace={
            "adapter": "digitalrecruiters",
            "stop_reason": stop_reason,
            "inventory_scope": "title_filtered",
            "inventory_complete": False,
            "candidate_count": 0,
            "exposed_candidate_count": 0,
        },
    )


ADAPTER = DigitalRecruitersAdapter()
