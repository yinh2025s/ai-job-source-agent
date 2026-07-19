from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import ipaddress
import json
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_PUBLIC_COMPONENT = "HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL"
_SEARCH_PAGE = "HRS_APP_SCHJOB_FL"
_DETAIL_PAGE = "HRS_APP_JBPST_FL"
_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_NUMBER = re.compile(r"^[1-9][0-9]{0,19}$")
_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$",
    re.IGNORECASE,
)
_PATH = re.compile(
    rf"^/psc/(?P<portal>[A-Za-z0-9_-]{{1,80}})/EMPLOYEE/"
    rf"(?P<node>[A-Za-z0-9_-]{{1,80}})/c/{re.escape(_PUBLIC_COMPONENT)}/?$",
    re.IGNORECASE,
)
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_MAX_HTML_CHARS = 5_000_000
_MAX_ROWS = 2_000


@dataclass(frozen=True)
class _Route:
    host: str
    portal: str
    node: str
    site_id: str
    kind: str
    opening_id: str | None = None
    posting_seq: str | None = None


class PeopleSoftAdapter:
    name = "peoplesoft"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _route(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        route = _route(url)
        return _job_board(route) if route is not None else None

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        explicit = self.identify_board(page_url)
        if explicit is not None:
            return explicit
        if _safe_url(page_url) is None or not _bounded_html(page.html):
            return None

        parser = _PeopleSoftParser()
        if not _feed(parser, page.html):
            return None
        boards: dict[str, JobBoard] = {}
        for raw_url in parser.navigation_urls:
            normalized = urljoin(page_url, raw_url)
            route = _route(normalized)
            if route is None or route.kind != "search":
                continue
            board = _job_board(route)
            boards[board.identifier or ""] = board
        return next(iter(boards.values())) if len(boards) == 1 else None

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        route = _board_route(board)
        scope = "title_filtered" if query.title else "full"
        if route is None:
            return _failure(
                board,
                scope,
                "PROVIDER_VARIANT_UNSUPPORTED",
                "invalid_board_identity",
            )

        request_url = _canonical_url(route)
        try:
            page = fetcher.fetch(request_url)
        except (FetchError, OSError, TimeoutError) as error:
            code = classify_fetch_error(str(error))
            return _failure(
                board,
                scope,
                code,
                "fetch_failed",
                retryable=reason_spec(code).retryable,
                request_url=request_url,
                error=str(error),
            )

        final_url = page.final_url or page.url
        final_route = _route(final_url)
        if final_route != route:
            return _failure(
                board,
                scope,
                "PROVIDER_VARIANT_UNSUPPORTED",
                "cross_site_tenant_or_opening_redirect",
                request_url=request_url,
                rejected_final_url=final_url,
                response_source=page.source,
            )
        if not _bounded_html(page.html):
            return _failure(
                board,
                scope,
                "INVALID_STRUCTURED_DATA",
                "unbounded_or_invalid_html",
                request_url=request_url,
                response_source=page.source,
            )

        parser = _PeopleSoftParser()
        if not _feed(parser, page.html):
            return _failure(
                board,
                scope,
                "INVALID_STRUCTURED_DATA",
                "malformed_html",
                request_url=request_url,
                response_source=page.source,
            )
        if parser.is_login_flow:
            return _failure(
                board,
                scope,
                "LOGIN_REQUIRED",
                "employee_login_flow",
                request_url=request_url,
                response_source=page.source,
            )

        if route.kind == "detail":
            return _detail_result(board, route, page, parser, scope)
        return _search_result(board, route, page, parser, query, scope)


class _PeopleSoftParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.navigation_urls: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.hidden_values: dict[str, set[str]] = {}
        self.title_candidates: list[str] = []
        self.location_candidates: list[str] = []
        self.text_parts: list[str] = []
        self._anchor_href: str | None = None
        self._anchor_parts: list[str] = []
        self._capture_kind: str | None = None
        self._capture_tag: str | None = None
        self._capture_parts: list[str] = []
        self._has_password = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        values = {name.casefold(): value for name, value in attrs if value is not None}
        for key in ("href", "action"):
            value = values.get(key)
            if value:
                self.navigation_urls.append(value)
        if tag == "a" and values.get("href"):
            self._anchor_href = values["href"]
            self._anchor_parts = []
        if tag == "input":
            name = values.get("name") or values.get("id")
            value = values.get("value")
            if name and value:
                self.hidden_values.setdefault(name.casefold(), set()).add(value.strip())
            if values.get("type", "").casefold() == "password":
                self._has_password = True

        marker = " ".join(
            value for value in (values.get("id"), values.get("class")) if value
        ).casefold()
        if tag == "h1" or "posting_title" in marker or "job_title" in marker:
            self._begin_capture("title", tag)
        elif "location" in marker and tag in {"div", "span", "td", "dd"}:
            self._begin_capture("location", tag)
        elif tag == "meta" and values.get("content"):
            name = (values.get("property") or values.get("name") or "").casefold()
            if name in {"og:title", "twitter:title"}:
                self.title_candidates.append(values["content"])

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._anchor_href is not None:
            self._anchor_parts.append(data)
        if self._capture_kind is not None:
            self._capture_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "a" and self._anchor_href is not None:
            self.links.append((self._anchor_href, _clean(" ".join(self._anchor_parts))))
            self._anchor_href = None
            self._anchor_parts = []
        if tag == self._capture_tag and self._capture_kind is not None:
            value = _clean(" ".join(self._capture_parts))
            if value:
                target = (
                    self.title_candidates
                    if self._capture_kind == "title"
                    else self.location_candidates
                )
                target.append(value)
            self._capture_kind = None
            self._capture_tag = None
            self._capture_parts = []

    def _begin_capture(self, kind: str, tag: str) -> None:
        if self._capture_kind is None:
            self._capture_kind = kind
            self._capture_tag = tag
            self._capture_parts = []

    @property
    def is_login_flow(self) -> bool:
        text = _clean(" ".join(self.text_parts)).casefold()
        return self._has_password or any(
            marker in text
            for marker in (
                "employee login",
                "employee sign in",
                "sign in to peoplesoft",
                "enter your user id and password",
            )
        )


def _detail_result(
    board: JobBoard,
    route: _Route,
    page: Page,
    parser: _PeopleSoftParser,
    scope: str,
) -> AdapterResult:
    if not _page_has_exact_identity(parser, page.html, route):
        return _failure(
            board,
            scope,
            "INVALID_STRUCTURED_DATA",
            "missing_or_conflicting_detail_identity_evidence",
            request_url=board.url,
            response_source=page.source,
        )
    title = next((_clean(value) for value in parser.title_candidates if _usable_title(value)), None)
    if title is None:
        return _failure(
            board,
            scope,
            "INVALID_STRUCTURED_DATA",
            "missing_public_job_title",
            request_url=board.url,
            response_source=page.source,
        )
    location = next((_clean(value) for value in parser.location_candidates if _clean(value)), None)
    candidate = JobCandidate(
        title=title,
        url=_canonical_url(route),
        provider="peoplesoft",
        location=location,
        raw={"site_id": route.site_id, "job_opening_id": route.opening_id},
    )
    return AdapterResult(
        provider="peoplesoft",
        board=board,
        candidates=[candidate],
        inventory_scope="title_filtered",
        inventory_complete=True,
        trace={
            "adapter": "peoplesoft",
            "variant": "public_candidate_gateway_exact_detail",
            "host": route.host,
            "site_id": route.site_id,
            "job_opening_id": route.opening_id,
            "detail_urls": [candidate.url],
            "response_source": page.source,
            "candidate_count": 1,
            "inventory_scope": "title_filtered",
            "inventory_complete": True,
        },
    )


def _search_result(
    board: JobBoard,
    route: _Route,
    page: Page,
    parser: _PeopleSoftParser,
    query: JobQuery,
    scope: str,
) -> AdapterResult:
    candidates: list[JobCandidate] = []
    seen: set[str] = set()
    invalid_public_links = 0
    for href, title in parser.links:
        if _PUBLIC_COMPONENT.casefold() not in href.casefold() and "jobopeningid" not in href.casefold():
            continue
        detail = _route(urljoin(board.url, href))
        if (
            detail is None
            or detail.kind != "detail"
            or not _same_tenant(route, detail)
            or not title
        ):
            invalid_public_links += 1
            continue
        detail_url = _canonical_url(detail)
        if detail_url in seen:
            invalid_public_links += 1
            continue
        seen.add(detail_url)
        candidates.append(
            JobCandidate(
                title=title,
                url=detail_url,
                provider="peoplesoft",
                raw={"site_id": detail.site_id, "job_opening_id": detail.opening_id},
            )
        )
        if len(candidates) > _MAX_ROWS:
            return _failure(
                board,
                scope,
                "FETCH_BUDGET_EXHAUSTED",
                "inventory_cap_exceeded",
                request_url=board.url,
                response_source=page.source,
            )
    if invalid_public_links:
        return _failure(
            board,
            scope,
            "INVALID_STRUCTURED_DATA",
            "malformed_cross_site_or_duplicate_detail_links",
            request_url=board.url,
            response_source=page.source,
        )

    target = _normalize(query.title)
    visible = [item for item in candidates if not target or target in _normalize(item.title)]
    complete = _search_inventory_complete(page.html, len(candidates))
    reason_code = "EMPTY_PROVIDER_RESPONSE" if complete and not visible else None
    if not candidates and not complete:
        reason_code = "PROVIDER_VARIANT_UNSUPPORTED"
    return AdapterResult(
        provider="peoplesoft",
        board=board,
        candidates=visible,
        reason_code=reason_code,
        inventory_scope=scope,
        inventory_complete=complete,
        trace={
            "adapter": "peoplesoft",
            "variant": "public_candidate_gateway_search_html",
            "host": route.host,
            "site_id": route.site_id,
            "board_urls": [board.url],
            "response_source": page.source,
            "records_seen": len(candidates),
            "candidate_count": len(visible),
            "inventory_scope": scope,
            "inventory_complete": complete,
        },
    )


def _route(url: str) -> _Route | None:
    parsed = _safe_url(url)
    if parsed is None or parsed.fragment or _BAD_PERCENT.search(parsed.query):
        return None
    match = _PATH.fullmatch(parsed.path)
    if match is None:
        return None
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    query: dict[str, str] = {}
    for raw_key, raw_value in pairs:
        key = raw_key.casefold()
        if key in query or not raw_value:
            return None
        query[key] = raw_value
    if set(query) not in (
        {"page", "action", "siteid", "focus"},
        {"page", "action", "siteid", "focus", "jobopeningid"},
        {"page", "action", "siteid", "focus", "jobopeningid", "postingseq"},
    ):
        return None
    if query.get("action", "").casefold() != "u" or query.get("focus", "").casefold() != "applicant":
        return None
    site_id = query.get("siteid", "")
    if not _NUMBER.fullmatch(site_id):
        return None
    page_name = query.get("page", "").upper()
    opening_id = query.get("jobopeningid")
    posting_seq = query.get("postingseq")
    if page_name == _SEARCH_PAGE and opening_id is None and posting_seq is None:
        kind = "search"
    elif (
        page_name == _DETAIL_PAGE
        and opening_id is not None
        and _NUMBER.fullmatch(opening_id)
        and (posting_seq is None or _NUMBER.fullmatch(posting_seq))
    ):
        kind = "detail"
    else:
        return None
    return _Route(
        host=(parsed.hostname or "").casefold(),
        portal=match.group("portal"),
        node=match.group("node"),
        site_id=site_id,
        kind=kind,
        opening_id=opening_id,
        posting_seq=posting_seq,
    )


def _safe_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or not host
        or not _HOSTNAME.fullmatch(host)
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    if host == "localhost" or host.endswith(".localhost") or "." not in host:
        return None
    return parsed


def _canonical_url(route: _Route) -> str:
    path = f"/psc/{route.portal}/EMPLOYEE/{route.node}/c/{_PUBLIC_COMPONENT}"
    params = [
        ("Page", _DETAIL_PAGE if route.kind == "detail" else _SEARCH_PAGE),
        ("Action", "U"),
        ("SiteId", route.site_id),
        ("FOCUS", "Applicant"),
    ]
    if route.opening_id is not None:
        params.append(("JobOpeningId", route.opening_id))
    if route.posting_seq is not None:
        params.append(("PostingSeq", route.posting_seq))
    return f"https://{route.host}{path}?{urlencode(params)}"


def _identifier(route: _Route) -> str:
    return json.dumps(
        {
            "host": route.host,
            "job_opening_id": route.opening_id,
            "kind": route.kind,
            "node": route.node,
            "portal": route.portal,
            "posting_seq": route.posting_seq,
            "site_id": route.site_id,
            "v": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _job_board(route: _Route) -> JobBoard:
    return JobBoard(
        url=_canonical_url(route),
        provider="peoplesoft",
        identifier=_identifier(route),
        replay_safe=route.kind == "search",
    )


def _board_route(board: JobBoard) -> _Route | None:
    if board.provider != "peoplesoft" or not isinstance(board.identifier, str):
        return None
    route = _route(board.url)
    if route is None or board.identifier != _identifier(route):
        return None
    return route


def _same_tenant(left: _Route, right: _Route) -> bool:
    return (
        left.host.casefold(),
        left.portal.casefold(),
        left.node.casefold(),
        left.site_id,
    ) == (
        right.host.casefold(),
        right.portal.casefold(),
        right.node.casefold(),
        right.site_id,
    )


def _page_has_exact_identity(parser: _PeopleSoftParser, html: str, route: _Route) -> bool:
    observed: set[tuple[str, str]] = set()
    for raw_url in parser.navigation_urls:
        candidate = _route(urljoin(_canonical_url(route), raw_url))
        if candidate is not None and candidate.kind == "detail":
            if not _same_tenant(route, candidate):
                return False
            observed.add((candidate.site_id, candidate.opening_id or ""))
    site_values = set()
    opening_values = set()
    for key, values in parser.hidden_values.items():
        folded = re.sub(r"[^a-z0-9]", "", key)
        if folded.endswith("siteid"):
            site_values.update(values)
        elif folded.endswith("jobopeningid"):
            opening_values.update(values)
    if site_values or opening_values:
        if site_values != {route.site_id} or opening_values != {route.opening_id}:
            return False
        observed.add((route.site_id, route.opening_id or ""))
    if not observed:
        text = html[:_MAX_HTML_CHARS]
        site = re.search(r"\bSiteId\s*[=:]\s*['\"]?([0-9]{1,20})", text, re.I)
        opening = re.search(r"\bJobOpeningId\s*[=:]\s*['\"]?([0-9]{1,20})", text, re.I)
        if site and opening:
            observed.add((site.group(1), opening.group(1)))
    return observed == {(route.site_id, route.opening_id or "")}


def _search_inventory_complete(html: str, row_count: int) -> bool:
    counts = {
        int(value)
        for value in re.findall(
            r"(?:TotalJobs|TotalResults|SearchResultsCount)\s*[=:]\s*['\"]?([0-9]{1,6})",
            html,
            re.I,
        )
    }
    return counts == {row_count}


def _bounded_html(html: str) -> bool:
    return isinstance(html, str) and len(html) <= _MAX_HTML_CHARS


def _feed(parser: _PeopleSoftParser, html: str) -> bool:
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return False
    return True


def _clean(value: str) -> str:
    return " ".join((value or "").split())


def _normalize(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _usable_title(value: str) -> bool:
    normalized = _normalize(value)
    return bool(normalized and normalized not in {"careers", "job search", "search jobs"})


def _failure(
    board: JobBoard,
    scope: str,
    code: str,
    error_classification: str,
    *,
    retryable: bool = False,
    **trace,
) -> AdapterResult:
    return AdapterResult(
        provider="peoplesoft",
        board=board,
        reason_code=code,
        retryable=retryable,
        inventory_scope=scope,
        inventory_complete=False,
        trace={
            "adapter": "peoplesoft",
            "error_classification": error_classification,
            "inventory_scope": scope,
            "inventory_complete": False,
            **trace,
        },
    )


ADAPTER = PeopleSoftAdapter()
