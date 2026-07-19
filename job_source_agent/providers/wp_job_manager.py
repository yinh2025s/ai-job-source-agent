from __future__ import annotations

from html.parser import HTMLParser
import ipaddress
import json
import re
from urllib.parse import urlencode, urljoin, urlparse

from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_CONFIG = re.compile(
    r"\b(?:var|let|const)\s+job_manager_ajax_filters\s*=\s*(\{.{1,20000}?\})\s*;",
    re.S,
)
_PLUGIN_PATH = "/wp-content/plugins/wp-job-manager/assets/dist/js/ajax-filters.js"
_ENDPOINT_TEMPLATE = "/jm-ajax/%%endpoint%%/"
_MAX_HTML = 2_000_000
_MAX_RESPONSE = 5_000_000
_MAX_PAGES = 50
_MAX_PER_PAGE = 100
_DETAIL_PATH = re.compile(
    r"/(?:job|jobs|job-listing|job_listing)/[a-z0-9][a-z0-9_-]{0,300}/?",
    re.I,
)
_SENSITIVE = re.compile(
    r"(?:token|secret|password|authorization|cookie|csrf|nonce)", re.I
)


class WPJobManagerAdapter:
    name = "wp_job_manager"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return False

    def identify_board(self, url: str) -> JobBoard | None:
        return None

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = _public_https_url(page.final_url or page.url)
        if page_url is None or not isinstance(page.html, str) or len(page.html) > _MAX_HTML:
            return None
        parser = _ListingPageParser(page_url)
        try:
            parser.feed(page.html)
            parser.close()
        except (TypeError, ValueError):
            return None
        if not parser.valid():
            return None
        configs = []
        for match in _CONFIG.finditer(page.html):
            try:
                value = json.loads(match.group(1))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if (
                isinstance(value, dict)
                and not any(_SENSITIVE.search(str(key)) for key in value)
                and value.get("ajax_url") == _ENDPOINT_TEMPLATE
            ):
                configs.append(value)
        if len(configs) != 1:
            return None
        endpoint = _public_https_url(urljoin(page_url, "/jm-ajax/get_listings/"))
        if endpoint is None or not _same_origin(endpoint, page_url):
            return None
        identifier = json.dumps(
            {
                "endpoint": endpoint,
                "order": parser.order,
                "orderby": parser.orderby,
                "per_page": parser.per_page,
                "v": 1,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return JobBoard(page_url, self.name, identifier, replay_safe=False)

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identity = _board_identity(board)
        if identity is None:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="invalid WP Job Manager board identity",
            )
        endpoint, per_page, orderby, order = identity
        candidates: list[JobCandidate] = []
        seen: set[tuple[str, str]] = set()
        expected_pages: int | None = None
        pages_fetched = 0
        for page_number in range(1, _MAX_PAGES + 1):
            fields = {
                "lang": "",
                "search_keywords": (query.title or "").strip(),
                "search_location": (query.location or "").strip(),
                "search_categories": "",
                "filter_job_type": "",
                "filter_post_status": "",
                "per_page": str(per_page),
                "orderby": orderby,
                "order": order,
                "page": str(page_number),
                "featured": "false",
                "filled": "false",
                "remote_position": "false",
                "show_pagination": "false",
            }
            body = urlencode(fields).encode("utf-8")
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
            }
            try:
                page = fetcher.fetch(endpoint, data=body, headers=headers)
            except (FetchError, OSError, TimeoutError) as error:
                reason = classify_fetch_error(str(error))
                return _result(
                    board,
                    candidates=candidates,
                    reason_code=reason,
                    retryable=reason_spec(reason).retryable,
                    inventory_complete=False,
                    error=str(error),
                    endpoint_url=endpoint,
                    pages_fetched=pages_fetched,
                )
            pages_fetched += 1
            if _public_https_url(page.final_url or page.url) != endpoint:
                return _result(
                    board,
                    candidates=candidates,
                    reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                    inventory_complete=False,
                    error="WP Job Manager inventory redirected away from the bound endpoint",
                    endpoint_url=endpoint,
                    pages_fetched=pages_fetched,
                )
            parsed = _parse_response(page.html, board.url)
            if parsed is None:
                return _result(
                    board,
                    candidates=candidates,
                    reason_code="INVALID_STRUCTURED_DATA",
                    inventory_complete=False,
                    error="invalid WP Job Manager inventory response",
                    endpoint_url=endpoint,
                    pages_fetched=pages_fetched,
                )
            found_jobs, max_pages, records = parsed
            if expected_pages is None:
                expected_pages = max_pages
            elif expected_pages != max_pages:
                return _result(
                    board,
                    candidates=candidates,
                    reason_code="INVALID_STRUCTURED_DATA",
                    inventory_complete=False,
                    error="WP Job Manager pagination metadata changed",
                    endpoint_url=endpoint,
                    pages_fetched=pages_fetched,
                )
            for record in records:
                key = (record.url.rstrip("/"), record.title.casefold())
                if key in seen:
                    return _result(
                        board,
                        candidates=candidates,
                        reason_code="INVALID_STRUCTURED_DATA",
                        inventory_complete=False,
                        error="WP Job Manager returned duplicate records",
                        endpoint_url=endpoint,
                        pages_fetched=pages_fetched,
                    )
                seen.add(key)
                candidates.append(record)
            if not found_jobs:
                if max_pages != 0 or records:
                    return _result(
                        board,
                        reason_code="INVALID_STRUCTURED_DATA",
                        inventory_complete=False,
                        error="WP Job Manager empty metadata is inconsistent",
                        endpoint_url=endpoint,
                        pages_fetched=pages_fetched,
                    )
                break
            if max_pages < 1 or page_number >= max_pages:
                break
        complete = expected_pages is not None and pages_fetched >= max(1, expected_pages)
        return _result(
            board,
            candidates=candidates,
            reason_code=(
                "FETCH_BUDGET_EXHAUSTED"
                if not complete
                else "EMPTY_PROVIDER_RESPONSE"
                if not candidates
                else None
            ),
            retryable=not complete,
            inventory_complete=complete,
            inventory_scope="title_filtered",
            endpoint_url=endpoint,
            pages_fetched=pages_fetched,
            declared_pages=expected_pages,
            candidate_count=len(candidates),
        )


class _ListingPageParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.per_page: int | None = None
        self.orderby: str | None = None
        self.order: str | None = None
        self.has_filters = False
        self.has_keywords = False
        self.has_location = False
        self.has_results = False
        self.has_plugin = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        classes = set(values.get("class", "").casefold().split())
        if tag.casefold() == "div" and "job_listings" in classes:
            raw_per_page = values.get("data-per_page", "")
            if raw_per_page.isdigit() and 1 <= int(raw_per_page) <= _MAX_PER_PAGE:
                self.per_page = int(raw_per_page)
                self.orderby = values.get("data-orderby", "").casefold()
                self.order = values.get("data-order", "").upper()
        elif tag.casefold() == "form" and "job_filters" in classes:
            self.has_filters = True
        elif tag.casefold() == "input":
            self.has_keywords |= values.get("name") == "search_keywords"
            self.has_location |= values.get("name") == "search_location"
        elif tag.casefold() == "ul" and "job_listings" in classes:
            self.has_results = True
        elif tag.casefold() == "script" and values.get("src"):
            script = _public_https_url(urljoin(self.page_url, values["src"]))
            self.has_plugin |= bool(
                script
                and _same_origin(script, self.page_url)
                and urlparse(script).path == _PLUGIN_PATH
            )

    def valid(self) -> bool:
        return bool(
            self.per_page
            and self.orderby in {"date", "featured", "modified", "title"}
            and self.order in {"ASC", "DESC"}
            and self.has_filters
            and self.has_keywords
            and self.has_location
            and self.has_results
            and self.has_plugin
        )


class _RecordParser(HTMLParser):
    def __init__(self, board_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.board_url = board_url
        self.depth = 0
        self.li_depth: int | None = None
        self.anchor_depth: int | None = None
        self.title_depth: int | None = None
        self.location_depth: int | None = None
        self.url: str | None = None
        self.title_parts: list[str] = []
        self.location_parts: list[str] = []
        self.records: list[JobCandidate] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.depth += 1
        values = {key.casefold(): value or "" for key, value in attrs}
        classes = set(values.get("class", "").casefold().split())
        tag = tag.casefold()
        if tag == "li" and "job_listing" in classes and self.li_depth is None:
            self.li_depth = self.depth
        elif tag == "a" and self.li_depth is not None and self.anchor_depth is None:
            candidate = _public_https_url(urljoin(self.board_url, values.get("href", "")))
            if (
                candidate
                and _same_origin(candidate, self.board_url)
                and _DETAIL_PATH.fullmatch(urlparse(candidate).path)
            ):
                self.anchor_depth = self.depth
                self.url = candidate
        elif tag in {"h2", "h3"} and self.li_depth is not None:
            self.title_depth = self.depth
        elif tag == "div" and self.li_depth is not None and "location" in classes:
            self.location_depth = self.depth

    def handle_data(self, data: str) -> None:
        if self.title_depth is not None and self.depth >= self.title_depth:
            self.title_parts.append(data)
        if self.location_depth is not None and self.depth >= self.location_depth:
            self.location_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "a" and self.anchor_depth == self.depth:
            self.anchor_depth = None
        if tag in {"h2", "h3"} and self.title_depth == self.depth:
            self.title_depth = None
        if tag == "div" and self.location_depth == self.depth:
            self.location_depth = None
        if tag == "li" and self.li_depth == self.depth:
            title = " ".join("".join(self.title_parts).split())
            location = " ".join("".join(self.location_parts).split()) or None
            if self.url and title and len(title) <= 300:
                self.records.append(
                    JobCandidate(title, self.url, "wp_job_manager", location)
                )
            self.li_depth = None
            self.anchor_depth = None
            self.title_depth = None
            self.location_depth = None
            self.url = None
            self.title_parts = []
            self.location_parts = []
        self.depth = max(0, self.depth - 1)


def _parse_response(source: str, board_url: str):
    if not isinstance(source, str) or len(source) > _MAX_RESPONSE:
        return None
    try:
        payload = json.loads(source)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    found_jobs = payload.get("found_jobs")
    max_pages = payload.get("max_num_pages")
    html = payload.get("html")
    if (
        not isinstance(found_jobs, bool)
        or isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or not 0 <= max_pages <= _MAX_PAGES
        or not isinstance(html, str)
    ):
        return None
    parser = _RecordParser(board_url)
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    return found_jobs, max_pages, parser.records


def _board_identity(board: JobBoard):
    if board.provider != "wp_job_manager" or not isinstance(board.identifier, str):
        return None
    try:
        value = json.loads(board.identifier)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != {
        "endpoint", "order", "orderby", "per_page", "v"
    } or value.get("v") != 1:
        return None
    endpoint = _public_https_url(value.get("endpoint"))
    per_page = value.get("per_page")
    orderby = value.get("orderby")
    order = value.get("order")
    if (
        endpoint is None
        or not _same_origin(endpoint, board.url)
        or urlparse(endpoint).path != "/jm-ajax/get_listings/"
        or isinstance(per_page, bool)
        or not isinstance(per_page, int)
        or not 1 <= per_page <= _MAX_PER_PAGE
        or orderby not in {"date", "featured", "modified", "title"}
        or order not in {"ASC", "DESC"}
    ):
        return None
    return endpoint, per_page, orderby, order


def _public_https_url(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 8192:
        return None
    try:
        parsed = urlparse(value)
        port = parsed.port
        host = (parsed.hostname or "").casefold().rstrip(".")
    except (TypeError, ValueError, AttributeError):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if (
        parsed.scheme.casefold() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or (address is not None and not address.is_global)
        or _SENSITIVE.search(parsed.query)
    ):
        return None
    return parsed._replace(scheme="https", netloc=host).geturl()


def _same_origin(left: str, right: str) -> bool:
    try:
        a, b = urlparse(left), urlparse(right)
        return (
            a.scheme.casefold(), (a.hostname or "").casefold(), a.port or 443
        ) == (
            b.scheme.casefold(), (b.hostname or "").casefold(), b.port or 443
        )
    except (TypeError, ValueError):
        return False


def _result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None = None,
    retryable: bool = False,
    inventory_complete: bool,
    inventory_scope: str = "unknown",
    error: str | None = None,
    **trace,
) -> AdapterResult:
    trace.update(
        {
            "adapter": "wp_job_manager",
            "inventory_scope": inventory_scope if inventory_complete else "unknown",
            "inventory_complete": inventory_complete,
        }
    )
    if error is not None:
        trace["error"] = error
    return AdapterResult(
        provider="wp_job_manager",
        board=board,
        candidates=candidates or [],
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope=inventory_scope if inventory_complete else "unknown",
        inventory_complete=inventory_complete,
        trace=trace,
    )


ADAPTER = WPJobManagerAdapter()
