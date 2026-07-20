from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import unquote, urljoin, urlparse

from ..fetch_failure import project_fetch_error
from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST_SUFFIX = ".hua.hrsmart.com"
_TENANT = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_LIST_PATHS = {
    "/hr/ats/JobSearch/index",
    "/hr/ats/JobSearch/index/searchType:quick",
    "/hr/ats/JobSearch/index/searchType:advanced",
    "/hr/ats/JobSearch/viewAll",
}
_DETAIL_PATH = re.compile(r"^/hr/ats/Posting/view/(?P<job_id>[0-9]{1,20})/?$")
_BOARD_PATH = "/hr/ats/JobSearch/viewAll"
_SUMMARY = re.compile(r"^Displaying\s+(\d+)\s+-\s+(\d+)\s+of\s+(\d+)$", re.I)
_MAX_HTML_CHARS = 2_000_000
_MAX_JOBS = 2_000
_MAX_FIELD_CHARS = 1_000


class HRSmartAdapter:
    name = "hrsmart"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        identity = _url_identity(url)
        if identity is None:
            return False
        parsed, _tenant = identity
        path = _normalized_path(parsed.path)
        return path in _LIST_PATHS or _DETAIL_PATH.fullmatch(path) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        identity = _url_identity(url)
        if identity is None or not self.recognizes(url):
            return None
        return _job_board(identity[1])

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        tenant = _board_tenant(board)
        if tenant is None:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="invalid HRSmart board identity",
            )

        board_url = _board_url(tenant)
        try:
            page = fetcher.fetch(board_url)
        except FetchError as error:
            failure = project_fetch_error(error)
            return _result(
                board,
                reason_code=failure.pop("reason_code"),
                retryable=failure.pop("retryable"),
                inventory_complete=False,
                board_urls=[board_url],
                **failure,
            )
        except (OSError, TimeoutError) as error:
            return _result(
                board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                inventory_complete=False,
                board_urls=[board_url],
                error=str(error),
            )

        final_url = page.final_url or page.url
        if not _is_canonical_board_response(final_url, tenant):
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                board_urls=[board_url],
                response_source=page.source,
                rejected_final_url=final_url,
                error="HRSmart inventory redirected away from the declared tenant board",
            )

        parsed = _parse_inventory(page.html, tenant, board_url)
        if parsed is None:
            return _result(
                board,
                reason_code="INVALID_STRUCTURED_DATA",
                inventory_complete=False,
                board_urls=[board_url],
                response_source=page.source,
                error="invalid, incomplete, or contradictory HRSmart public inventory",
            )
        candidates, total = parsed
        target = _normalized(query.title)
        return _result(
            board,
            candidates=candidates,
            reason_code="EMPTY_PROVIDER_RESPONSE" if not candidates else None,
            board_urls=[board_url],
            response_source=page.source,
            variant="public_view_all_html",
            tenant=tenant,
            records_seen=total,
            exact_title_found=bool(
                target
                and any(_normalized(candidate.title) == target for candidate in candidates)
            ),
        )


class _InventoryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_provider_title = False
        self.has_request_uri = False
        self.has_table = False
        self.headers: list[str] = []
        self.rows: list[list[tuple[str, str | None]]] = []
        self.summaries: list[tuple[int, int, int]] = []
        self._in_title = False
        self._title_text: list[str] = []
        self._in_table = False
        self._section: str | None = None
        self._row: list[tuple[str, str | None]] | None = None
        self._cell_text: list[str] | None = None
        self._cell_href: str | None = None
        self._summary_depth = 0
        self._summary_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag == "title":
            self._in_title = True
            self._title_text = []
        if tag == "table" and attributes.get("id") == "jobSearchResultsGrid_table":
            self.has_table = True
            self._in_table = True
        if self._in_table and tag in {"thead", "tbody"}:
            self._section = tag
        if self._in_table and tag == "tr":
            self._row = []
        if self._row is not None and tag in {"th", "td"}:
            self._cell_text = []
            self._cell_href = None
        if self._cell_text is not None and tag == "a":
            self._cell_href = attributes.get("href") or self._cell_href

        classes = set(attributes.get("class", "").split())
        if tag == "div" and {"pagination_displaying", "displaycount"}.issubset(classes):
            self._summary_depth = 1
            self._summary_text = []
        elif self._summary_depth:
            self._summary_depth += 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_text.append(data)
        if 'xajax.config.requestURI = "/hr/ats/JobSearch/viewAll"' in data:
            self.has_request_uri = True
        if self._cell_text is not None:
            self._cell_text.append(data)
        if self._summary_depth:
            self._summary_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "title" and self._in_title:
            title = _clean_text("".join(self._title_text))
            self.has_provider_title = "Deltek Talent Management" in title
            self._in_title = False
        if tag in {"th", "td"} and self._cell_text is not None:
            cell = (_clean_text("".join(self._cell_text)), self._cell_href)
            if self._section == "thead":
                self.headers.append(cell[0])
            elif self._row is not None:
                self._row.append(cell)
            self._cell_text = None
            self._cell_href = None
        if tag == "tr" and self._row is not None:
            if self._section == "tbody":
                self.rows.append(self._row)
            self._row = None
        if tag in {"thead", "tbody"} and self._in_table:
            self._section = None
        if tag == "table" and self._in_table:
            self._in_table = False
        if self._summary_depth:
            self._summary_depth -= 1
            if self._summary_depth == 0:
                text = _clean_text("".join(self._summary_text))
                match = _SUMMARY.fullmatch(text)
                if match:
                    self.summaries.append(tuple(int(value) for value in match.groups()))


def _parse_inventory(
    html: str,
    tenant: str,
    board_url: str,
) -> tuple[list[JobCandidate], int] | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    parser = _InventoryParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    if not (
        parser.has_provider_title
        and parser.has_request_uri
        and parser.has_table
        and parser.headers[:3] == ["Req. #", "Job Title", "Location"]
        and len(set(parser.summaries)) == 1
    ):
        return None
    first, last, total = parser.summaries[0]
    if total > _MAX_JOBS or first != 1 or last != total or len(parser.rows) != total:
        return None

    candidates: list[JobCandidate] = []
    seen_ids: set[str] = set()
    for row in parser.rows:
        if len(row) < 3:
            return None
        requisition, _ = row[0]
        title, href = row[1]
        location, _ = row[2]
        detail = _detail_identity(href, tenant, board_url)
        if (
            detail is None
            or requisition != detail[1]
            or detail[1] in seen_ids
            or not title
            or len(title) > _MAX_FIELD_CHARS
            or len(location) > _MAX_FIELD_CHARS
        ):
            return None
        seen_ids.add(detail[1])
        candidates.append(
            JobCandidate(
                title=title,
                url=detail[0],
                provider="hrsmart",
                location=location or None,
                raw={"job_id": detail[1], "requisition": requisition},
            )
        )
    return candidates, total


def _url_identity(url: str):
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
        or not host.endswith(_HOST_SUFFIX)
    ):
        return None
    tenant = host[: -len(_HOST_SUFFIX)]
    if not _TENANT.fullmatch(tenant):
        return None
    return parsed, tenant


def _normalized_path(path: str) -> str:
    decoded = unquote(path)
    return decoded.rstrip("/") or "/"


def _board_url(tenant: str) -> str:
    return f"https://{tenant}{_HOST_SUFFIX}{_BOARD_PATH}"


def _job_board(tenant: str) -> JobBoard:
    return JobBoard(url=_board_url(tenant), provider="hrsmart", identifier=tenant)


def _board_tenant(board: JobBoard) -> str | None:
    if board.provider != "hrsmart" or not isinstance(board.identifier, str):
        return None
    tenant = board.identifier.casefold()
    if not _TENANT.fullmatch(tenant) or board.url != _board_url(tenant):
        return None
    return tenant


def _is_canonical_board_response(url: str, tenant: str) -> bool:
    identity = _url_identity(url)
    if identity is None:
        return False
    parsed, actual_tenant = identity
    return (
        actual_tenant == tenant
        and _normalized_path(parsed.path) == _BOARD_PATH
        and not parsed.query
    )


def _detail_identity(
    href: str | None,
    tenant: str,
    board_url: str,
) -> tuple[str, str] | None:
    if not href:
        return None
    try:
        url = urljoin(board_url, href)
    except (TypeError, ValueError):
        return None
    identity = _url_identity(url)
    if identity is None:
        return None
    parsed, actual_tenant = identity
    path = _normalized_path(parsed.path)
    match = _DETAIL_PATH.fullmatch(path)
    if actual_tenant != tenant or match is None or parsed.query:
        return None
    job_id = match.group("job_id")
    return f"https://{tenant}{_HOST_SUFFIX}/hr/ats/Posting/view/{job_id}", job_id


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _normalized(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if value else ""


def _result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None = None,
    retryable: bool = False,
    inventory_complete: bool = True,
    **trace,
) -> AdapterResult:
    items = candidates or []
    trace.setdefault("adapter", "hrsmart")
    trace.setdefault("candidate_count", len(items))
    trace.setdefault("inventory_scope", "full")
    trace.setdefault("inventory_complete", inventory_complete)
    return AdapterResult(
        provider="hrsmart",
        board=board,
        candidates=items,
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope="full",
        inventory_complete=inventory_complete,
        trace=trace,
    )


ADAPTER = HRSmartAdapter()
