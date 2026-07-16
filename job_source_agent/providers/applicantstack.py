from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import unquote, urljoin, urlparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST_SUFFIX = ".applicantstack.com"
_TENANT_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_DETAIL_PATH = re.compile(r"^/x/detail/([a-z0-9]{8,64})/?$", flags=re.I)
_BOARD_PATH = "/x/openings"


class ApplicantStackAdapter:
    name = "applicantstack"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        parsed_tenant = _parsed_tenant_url(url)
        if parsed_tenant is None:
            return False
        parsed, _tenant = parsed_tenant
        path = unquote(parsed.path).rstrip("/")
        return path == _BOARD_PATH or _DETAIL_PATH.fullmatch(path) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        parsed_tenant = _parsed_tenant_url(url)
        if parsed_tenant is None or not self.recognizes(url):
            return None
        _parsed, tenant = parsed_tenant
        return _job_board(tenant)

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        tenant = _board_tenant(board)
        if tenant is None:
            return _unsupported(board, "invalid ApplicantStack board tenant or route")

        try:
            page = fetcher.fetch(board.url)
        except (FetchError, OSError, TimeoutError) as error:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                trace={
                    "adapter": self.name,
                    "board_urls": [board.url],
                    "error": str(error),
                },
            )

        final_url = page.final_url or page.url
        if not _is_exact_board_url(final_url, tenant):
            return _unsupported(
                board,
                "ApplicantStack board redirected outside the declared tenant route",
                final_url,
            )

        parser = _ApplicantStackJobsParser(tenant, board.url)
        try:
            parser.feed(page.html or "")
        except (TypeError, ValueError):
            return _invalid(board, "malformed ApplicantStack HTML")

        if not parser.has_fingerprint:
            return _invalid(board, "missing ApplicantStack public-board fingerprint")

        candidates: list[JobCandidate] = []
        seen: set[str] = set()
        rejected_rows = 0
        for title, href, location in parser.rows:
            detail = _detail_identity(href, tenant, board.url)
            if not title or detail is None:
                rejected_rows += 1
                continue
            detail_url, job_id = detail
            if detail_url in seen:
                rejected_rows += 1
                continue
            seen.add(detail_url)
            candidates.append(
                JobCandidate(
                    title=title,
                    url=detail_url,
                    provider=self.name,
                    location=location,
                    raw={"job_id": job_id},
                )
            )

        target = _normalized_title(query.title)
        inventory_complete = rejected_rows == 0
        inventory_scope = "full" if inventory_complete else "partial"
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            inventory_scope=inventory_scope,
            inventory_complete=inventory_complete,
            trace={
                "adapter": self.name,
                "variant": "public_board_html",
                "board_urls": [board.url],
                "response_source": page.source,
                "candidate_count": len(candidates),
                "public_row_count": len(parser.rows),
                "rejected_row_count": rejected_rows,
                "exact_title_found": bool(
                    target
                    and any(
                        _normalized_title(candidate.title) == target
                        for candidate in candidates
                    )
                ),
                "inventory_scope": inventory_scope,
                "inventory_complete": inventory_complete,
            },
        )


class _ApplicantStackJobsParser(HTMLParser):
    def __init__(self, tenant: str, board_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tenant = tenant
        self.board_url = board_url
        self.has_openings_form = False
        self.has_data_table = False
        self.has_branding = False
        self.rows: list[tuple[str, str, str | None]] = []
        self._table_depth = 0
        self._row_depth = 0
        self._cell_depth = 0
        self._cells: list[list[str]] = []
        self._href: str | None = None
        self._link_text: list[str] = []
        self._title = ""
        self._row_link_count = 0

    @property
    def has_fingerprint(self) -> bool:
        return self.has_openings_form and self.has_data_table and self.has_branding

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}

        if tag == "form" and attributes.get("id") == "mainform":
            self.has_openings_form = _is_exact_board_url(
                attributes.get("action", ""), self.tenant
            )
        if attributes.get("id") == "asbranding":
            self.has_branding = True
        if tag == "table" and attributes.get("id") == "data-table":
            self.has_data_table = True
            self._table_depth = 1
            return
        if self._table_depth:
            self._table_depth += 1
        if not self._table_depth:
            return

        if tag == "tr" and not self._row_depth:
            self._row_depth = 1
            self._cells = []
            self._href = None
            self._link_text = []
            self._title = ""
            self._row_link_count = 0
        elif self._row_depth:
            self._row_depth += 1

        if tag == "td" and self._row_depth and not self._cell_depth:
            self._cell_depth = 1
            self._cells.append([])
        elif self._cell_depth:
            self._cell_depth += 1

        if tag == "a" and self._row_depth and len(self._cells) == 1:
            self._row_link_count += 1
            self._href = attributes.get("href", "")
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._cell_depth and self._cells:
            self._cells[-1].append(data)
        if self._href is not None:
            self._link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "a" and self._href is not None:
            self._title = _clean_text(self._link_text)
            self._link_text = []

        if self._cell_depth:
            self._cell_depth -= 1
        if self._row_depth:
            self._row_depth -= 1
            if not self._row_depth:
                if self._href is not None:
                    location = (
                        _clean_text(self._cells[1]) if len(self._cells) > 1 else None
                    )
                    href = self._href if self._row_link_count == 1 else ""
                    self.rows.append((self._title, href, location or None))
                self._cells = []
                self._href = None
                self._link_text = []
                self._title = ""
                self._row_link_count = 0
        if self._table_depth:
            self._table_depth -= 1


def _parsed_tenant_url(url: str):
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
        or not host.endswith(_HOST_SUFFIX)
    ):
        return None
    tenant = host[: -len(_HOST_SUFFIX)]
    if not _TENANT_PATTERN.fullmatch(tenant):
        return None
    return parsed, tenant


def _job_board(tenant: str) -> JobBoard:
    return JobBoard(
        url=f"https://{tenant}{_HOST_SUFFIX}{_BOARD_PATH}",
        provider="applicantstack",
        identifier=tenant,
    )


def _board_tenant(board: JobBoard) -> str | None:
    if board.provider != "applicantstack" or not board.identifier:
        return None
    tenant = board.identifier.casefold()
    if not _TENANT_PATTERN.fullmatch(tenant) or board.url != _job_board(tenant).url:
        return None
    return tenant


def _is_exact_board_url(url: str, tenant: str) -> bool:
    parsed_tenant = _parsed_tenant_url(url)
    if parsed_tenant is None:
        return False
    parsed, actual_tenant = parsed_tenant
    return (
        actual_tenant == tenant
        and unquote(parsed.path).rstrip("/") == _BOARD_PATH
        and not parsed.query
        and not parsed.fragment
    )


def _detail_identity(href: str, tenant: str, board_url: str) -> tuple[str, str] | None:
    try:
        url = urljoin(board_url, href)
    except (TypeError, ValueError):
        return None
    parsed_tenant = _parsed_tenant_url(url)
    if parsed_tenant is None:
        return None
    parsed, actual_tenant = parsed_tenant
    path = unquote(parsed.path).rstrip("/")
    match = _DETAIL_PATH.fullmatch(path)
    if actual_tenant != tenant or match is None:
        return None
    return f"https://{tenant}{_HOST_SUFFIX}{path}", match.group(1).casefold()


def _clean_text(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


def _normalized_title(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if value else ""


def _unsupported(
    board: JobBoard, error: str, rejected_url: str | None = None
) -> AdapterResult:
    trace = {"adapter": "applicantstack", "error": error}
    if rejected_url:
        trace["rejected_final_url"] = rejected_url
    return AdapterResult(
        provider="applicantstack",
        board=board,
        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
        trace=trace,
    )


def _invalid(board: JobBoard, error: str) -> AdapterResult:
    return AdapterResult(
        provider="applicantstack",
        board=board,
        reason_code="INVALID_STRUCTURED_DATA",
        trace={"adapter": "applicantstack", "board_urls": [board.url], "error": error},
    )


ADAPTER = ApplicantStackAdapter()
