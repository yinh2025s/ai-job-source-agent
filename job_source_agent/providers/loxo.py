from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST_SUFFIX = ".app.loxo.co"
_TENANT = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_BOARD_PATH = re.compile(r"^/[a-z0-9](?:[a-z0-9-]{0,199}[a-z0-9])?/?$", re.I)
_DETAIL_PATH = re.compile(r"^/job/[A-Za-z0-9_-]{8,512}={0,2}/?$")
_MAX_HTML = 3_000_000
_MAX_JOBS = 2_000


class LoxoAdapter:
    name = "loxo"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _url_identity(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        identity = _url_identity(url)
        if identity is None:
            return None
        tenant, path = identity
        return _job_board(tenant, path)

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identity = _board_identity(board)
        if identity is None:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="invalid Loxo board identity",
            )
        tenant, path = identity
        title = (query.title or "").strip()
        fields = {"disable_addthis": "true"}
        if title:
            fields["query"] = title
        listing_url = f"{_canonical_board_url(tenant, path)}?{urlencode(fields)}"
        try:
            page = fetcher.fetch(listing_url)
        except (FetchError, OSError, TimeoutError) as error:
            reason = classify_fetch_error(str(error))
            return _result(
                board,
                reason_code=reason,
                retryable=reason_spec(reason).retryable,
                inventory_complete=False,
                error=str(error),
                listing_url=listing_url,
            )

        if _response_identity(page.final_url or page.url) != (tenant, path):
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="Loxo listing redirected outside the bound tenant board",
                listing_url=listing_url,
                rejected_url=page.final_url or page.url,
            )
        parsed = _parse_listing(page.html, tenant, path, expected_query=title)
        if parsed is None:
            return _result(
                board,
                reason_code="INVALID_STRUCTURED_DATA",
                inventory_complete=False,
                error="invalid or contradictory Loxo listing response",
                listing_url=listing_url,
            )
        candidates, empty = parsed
        return _result(
            board,
            candidates=candidates,
            reason_code="EMPTY_PROVIDER_RESPONSE" if empty else None,
            inventory_scope="title_filtered" if title else "full",
            inventory_complete=True,
            listing_url=listing_url,
            candidate_count=len(candidates),
            query_echoed=title,
        )


class _ListingParser(HTMLParser):
    def __init__(self, tenant: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tenant = tenant
        self.powered_by_loxo = False
        self.has_openings_heading = False
        self.empty = False
        self.query_values: list[str] = []
        self.records: list[tuple[str, str, str | None]] = []
        self.card_count = 0
        self._capture: str | None = None
        self._parts: list[str] = []
        self._card_title: str | None = None
        self._card_url: str | None = None
        self._card_location: str | None = None
        self._card_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        classes = set(values.get("class", "").casefold().split())
        lower_tag = tag.casefold()
        if lower_tag == "input" and values.get("name") == "query":
            self.query_values.append(values.get("value", ""))
        if lower_tag == "div" and "jobs-listing-card" in classes:
            if self._card_depth:
                self._card_depth += 1
            else:
                self.card_count += 1
                self._card_depth = 1
                self._card_title = None
                self._card_url = None
                self._card_location = None
            return
        if self._card_depth and lower_tag == "div":
            self._card_depth += 1
        if self._card_depth and lower_tag == "a" and "job-title" in classes:
            self._card_url = values.get("href") or None
            self._capture = "title"
            self._parts = []
        elif self._card_depth and lower_tag == "div" and "job-location" in classes:
            self._capture = "location"
            self._parts = []
        elif lower_tag == "div" and "jobs-listing-empty-state" in classes:
            self.empty = True
        elif lower_tag == "div" and "powered-by-loxo" in classes:
            self.powered_by_loxo = True

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._parts.append(data)
        text = " ".join(data.split()).casefold()
        if text == "job openings":
            self.has_openings_heading = True

    def handle_endtag(self, tag: str) -> None:
        lower_tag = tag.casefold()
        if self._capture == "title" and lower_tag == "a":
            self._card_title = " ".join("".join(self._parts).split()) or None
            self._capture = None
            self._parts = []
        elif self._capture == "location" and lower_tag == "div":
            location = " ".join("".join(self._parts).split())
            self._card_location = location or None
            self._capture = None
            self._parts = []
        if self._card_depth and lower_tag == "div":
            self._card_depth -= 1
            if self._card_depth == 0:
                if self._card_title and self._card_url:
                    self.records.append(
                        (self._card_title, self._card_url, self._card_location)
                    )
                self._card_title = None
                self._card_url = None
                self._card_location = None


def _safe_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None
    return parsed, host


def _host_tenant(host: str) -> str | None:
    if not host.endswith(_HOST_SUFFIX):
        return None
    tenant = host[: -len(_HOST_SUFFIX)]
    return tenant if _TENANT.fullmatch(tenant) else None


def _normalized_path(path: str) -> str:
    return "/" + "/".join(part for part in unquote(path).split("/") if part)


def _url_identity(url: str) -> tuple[str, str] | None:
    parsed_host = _safe_url(url)
    if parsed_host is None:
        return None
    parsed, host = parsed_host
    tenant = _host_tenant(host)
    path = _normalized_path(parsed.path)
    if tenant is None or _BOARD_PATH.fullmatch(path) is None:
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(key not in {"disable_addthis", "query", "type_sort", "location_sort"} for key in query):
        return None
    return tenant, path


def _response_identity(url: str) -> tuple[str, str] | None:
    return _url_identity(url)


def _canonical_board_url(tenant: str, path: str) -> str:
    return f"https://{tenant}{_HOST_SUFFIX}{path}"


def _job_board(tenant: str, path: str) -> JobBoard:
    identifier = json.dumps(
        {"path": path, "tenant": tenant, "v": 1},
        separators=(",", ":"),
        sort_keys=True,
    )
    return JobBoard(_canonical_board_url(tenant, path), "loxo", identifier)


def _board_identity(board: JobBoard) -> tuple[str, str] | None:
    if board.provider != "loxo" or not isinstance(board.identifier, str):
        return None
    try:
        value = json.loads(board.identifier)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != {"path", "tenant", "v"} or value["v"] != 1:
        return None
    tenant = value["tenant"]
    path = value["path"]
    if (
        not isinstance(tenant, str)
        or _TENANT.fullmatch(tenant) is None
        or not isinstance(path, str)
        or _BOARD_PATH.fullmatch(path) is None
        or board.url != _canonical_board_url(tenant, path)
    ):
        return None
    return tenant, path


def _detail_url(href: str, tenant: str) -> str | None:
    parsed_host = _safe_url(href)
    if parsed_host is None:
        if not href.startswith("/"):
            return None
        parsed_host = _safe_url(f"https://{tenant}{_HOST_SUFFIX}{href}")
        if parsed_host is None:
            return None
    parsed, host = parsed_host
    if _host_tenant(host) != tenant or _DETAIL_PATH.fullmatch(parsed.path) is None:
        return None
    if any(key != "disable_addthis" for key in parse_qs(parsed.query, keep_blank_values=True)):
        return None
    return urlunparse(("https", host, parsed.path.rstrip("/"), "", "", ""))


def _parse_listing(
    html: str,
    tenant: str,
    path: str,
    *,
    expected_query: str,
) -> tuple[list[JobCandidate], bool] | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML:
        return None
    parser = _ListingParser(tenant)
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    if (
        not parser.powered_by_loxo
        or not parser.has_openings_heading
        or parser.query_values != [expected_query]
        or (parser.empty and parser.records)
        or (not parser.empty and not parser.records)
        or parser.card_count != len(parser.records)
        or len(parser.records) > _MAX_JOBS
    ):
        return None
    candidates: list[JobCandidate] = []
    seen: set[str] = set()
    for title, href, location in parser.records:
        url = _detail_url(href, tenant)
        if url is None or url in seen:
            return None
        seen.add(url)
        candidates.append(
            JobCandidate(
                title=title,
                url=url,
                provider="loxo",
                location=location,
                raw={"tenant": tenant, "board_path": path},
            )
        )
    if len(seen) != len(parser.records):
        return None
    return candidates, parser.empty


def _result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None = None,
    retryable: bool = False,
    inventory_scope: str = "title_filtered",
    inventory_complete: bool,
    **trace,
) -> AdapterResult:
    return AdapterResult(
        provider="loxo",
        board=board,
        candidates=candidates or [],
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope=inventory_scope,
        inventory_complete=inventory_complete,
        trace={
            "adapter": "loxo",
            "variant": "public_server_rendered_search",
            "inventory_scope": inventory_scope,
            "inventory_complete": inventory_complete,
            **trace,
        },
    )


ADAPTER = LoxoAdapter()
