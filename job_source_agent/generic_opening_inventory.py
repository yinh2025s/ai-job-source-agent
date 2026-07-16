from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import ipaddress
import re
from urllib.parse import parse_qsl, unquote, urljoin, urlparse, urlunparse

from .contracts import FetchClient
from .listing_extraction import ListingCandidate, extract_listing_candidates
from .web import FetchError, Page


MAX_PAGES = 25
MAX_CANDIDATES = 1_000
MAX_PAGINATION_NODES = 20_000
MAX_PAGINATION_TEXT_CHARS = 500_000
MAX_URL_CHARS = 8_192

_NEXT_LABEL = re.compile(r"^(?:next(?:\s+page)?|load\s+more)$", re.IGNORECASE)
_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$",
    re.IGNORECASE,
)
_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "code",
    "cookie",
    "csrf",
    "csrf_token",
    "id_token",
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


@dataclass(frozen=True)
class InventoryTraceEntry:
    url: str
    candidate_count: int
    reason: str


@dataclass(frozen=True)
class GenericOpeningInventoryResult:
    candidates: tuple[ListingCandidate, ...]
    pages_fetched: int
    inventory_complete: bool
    stop_reason: str
    trace: tuple[InventoryTraceEntry, ...]


@dataclass
class _PaginationLink:
    href: str
    rel_next: bool
    text_parts: list[str]


class _LimitReached(Exception):
    pass


class _PaginationParser(HTMLParser):
    _VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = source_url
        self.nodes = 0
        self.text_chars = 0
        self.links: list[_PaginationLink] = []
        self._stack: list[tuple[str, _PaginationLink | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        self.nodes += 1
        if self.nodes > MAX_PAGINATION_NODES:
            raise _LimitReached
        values = {name.casefold(): (value or "") for name, value in attrs}
        if tag == "base" and values.get("href"):
            self.base_url = urljoin(self.base_url, values["href"])

        link = None
        if tag in {"a", "link"} and values.get("href"):
            rel = {item.casefold() for item in values.get("rel", "").split()}
            link = _PaginationLink(values["href"], "next" in rel, [])
            self.links.append(link)
        if tag not in self._VOID_TAGS:
            self._stack.append((tag, link))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not data:
            return
        self.text_chars += len(data)
        if self.text_chars > MAX_PAGINATION_TEXT_CHARS:
            raise _LimitReached
        for _tag, link in self._stack:
            if link is not None:
                link.text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                return

    def explicit_next_urls(self) -> list[str]:
        output: list[str] = []
        for link in self.links:
            text = " ".join(" ".join(link.text_parts).split())
            if link.rel_next or _NEXT_LABEL.fullmatch(text):
                output.append(urljoin(self.base_url, link.href))
        return output


def collect_generic_opening_inventory(
    fetcher: FetchClient,
    page: Page,
    *,
    max_pages: int,
    max_candidates: int,
) -> GenericOpeningInventoryResult:
    """Collect strict listing candidates from bounded public HTML pagination."""

    _validate_limit("max_pages", max_pages, MAX_PAGES)
    _validate_limit("max_candidates", max_candidates, MAX_CANDIDATES)

    initial_url = _canonical_public_https_url(page.final_url or page.url)
    if initial_url is None:
        return GenericOpeningInventoryResult(
            candidates=(),
            pages_fetched=1,
            inventory_complete=False,
            stop_reason="unsafe_initial_page_url",
            trace=(InventoryTraceEntry("[unsafe_url]", 0, "unsafe_initial_page_url"),),
        )

    candidates: list[ListingCandidate] = []
    seen_candidates: set[tuple[str, str]] = set()
    seen_pages = {initial_url}
    traces: list[InventoryTraceEntry] = []
    current_page = page
    current_url = initial_url
    pages_fetched = 1
    saw_pagination = False

    while True:
        extracted = extract_listing_candidates(current_page.html, current_url)
        page_candidate_count = 0
        overflow = False
        for candidate in extracted:
            key = (candidate.url.rstrip("/"), candidate.title.casefold().strip())
            if key in seen_candidates:
                continue
            if len(candidates) >= max_candidates:
                overflow = True
                break
            seen_candidates.add(key)
            candidates.append(candidate)
            page_candidate_count += 1

        if overflow or len(candidates) >= max_candidates:
            stop_reason = "candidate_cap_reached"
            traces.append(InventoryTraceEntry(current_url, page_candidate_count, stop_reason))
            break

        next_urls, pagination_limited = _explicit_next_urls(current_page.html, current_url)
        if pagination_limited:
            stop_reason = "pagination_parse_limit"
            traces.append(InventoryTraceEntry(current_url, page_candidate_count, stop_reason))
            break
        if not next_urls:
            stop_reason = "complete" if saw_pagination else "single_page_unbounded"
            traces.append(InventoryTraceEntry(current_url, page_candidate_count, stop_reason))
            return GenericOpeningInventoryResult(
                candidates=tuple(candidates),
                pages_fetched=pages_fetched,
                inventory_complete=saw_pagination,
                stop_reason=stop_reason,
                trace=tuple(traces),
            )
        safe_next_urls = [
            normalized
            for value in next_urls
            if (normalized := _canonical_public_https_url(value, origin_url=initial_url))
            is not None
        ]
        if not safe_next_urls:
            stop_reason = "unsafe_next_url"
            traces.append(InventoryTraceEntry(current_url, page_candidate_count, stop_reason))
            break

        next_url = safe_next_urls[0]
        saw_pagination = True
        if next_url in seen_pages:
            stop_reason = "pagination_cycle"
            traces.append(InventoryTraceEntry(current_url, page_candidate_count, stop_reason))
            break
        if pages_fetched >= max_pages:
            stop_reason = "page_cap_reached"
            traces.append(InventoryTraceEntry(current_url, page_candidate_count, stop_reason))
            break

        traces.append(InventoryTraceEntry(current_url, page_candidate_count, "next_page"))
        seen_pages.add(next_url)
        try:
            fetched_page = fetcher.fetch(next_url)
        except FetchError:
            stop_reason = "fetch_error"
            traces.append(InventoryTraceEntry(next_url, 0, stop_reason))
            break
        pages_fetched += 1
        response_url = _canonical_public_https_url(
            fetched_page.final_url or fetched_page.url,
            origin_url=initial_url,
        )
        if response_url is None:
            stop_reason = "unsafe_response_url"
            traces.append(InventoryTraceEntry(next_url, 0, stop_reason))
            break
        if response_url in seen_pages and response_url != next_url:
            stop_reason = "pagination_cycle"
            traces.append(InventoryTraceEntry(response_url, 0, stop_reason))
            break
        seen_pages.add(response_url)
        current_page = fetched_page
        current_url = response_url

    return GenericOpeningInventoryResult(
        candidates=tuple(candidates),
        pages_fetched=pages_fetched,
        inventory_complete=False,
        stop_reason=stop_reason,
        trace=tuple(traces),
    )


def _explicit_next_urls(html: str, source_url: str) -> tuple[list[str], bool]:
    parser = _PaginationParser(source_url)
    try:
        parser.feed(html)
        parser.close()
    except (_LimitReached, TypeError, ValueError):
        return [], True
    return parser.explicit_next_urls(), False


def _validate_limit(name: str, value: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer between 1 and {maximum}")


def _canonical_public_https_url(
    value: str,
    *,
    origin_url: str | None = None,
) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_URL_CHARS:
        return None
    try:
        parsed = urlparse(value)
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except (TypeError, ValueError):
        return None
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "https"
        or not _is_public_host(hostname)
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
        or _has_controls(value)
        or _has_controls(unquote(parsed.path))
        or any(
            _is_sensitive_query_key(key)
            or _contains_secret(key)
            or _contains_secret(item)
            for key, item in query
        )
    ):
        return None
    normalized = urlunparse(("https", hostname, parsed.path or "/", "", parsed.query, ""))
    if origin_url is not None:
        origin = urlparse(origin_url)
        if hostname != (origin.hostname or "").casefold().rstrip("."):
            return None
    return normalized


def _is_public_host(hostname: str) -> bool:
    if (
        not hostname
        or len(hostname) > 253
        or not _HOSTNAME.fullmatch(hostname)
        or hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".internal"))
    ):
        return False
    try:
        return ipaddress.ip_address(hostname).is_global
    except ValueError:
        return "." in hostname


def _is_sensitive_query_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return normalized in _SENSITIVE_QUERY_KEYS or normalized.endswith(
        _SENSITIVE_QUERY_SUFFIXES
    )


def _contains_secret(value: str) -> bool:
    decoded = unquote(value)
    return _has_controls(decoded) or bool(_SECRET_VALUE.search(value) or _SECRET_VALUE.search(decoded))


def _has_controls(value: str) -> bool:
    return any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
