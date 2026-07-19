from __future__ import annotations

from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import re
from urllib.parse import parse_qsl, unquote, urljoin, urlparse, urlunparse

from .contracts import FetchClient
from .listing_extraction import (
    ListingCandidate,
    extract_listing_candidates,
    validate_output_url,
)
from .web import FetchError, Page


MAX_PAGES = 25
MAX_CANDIDATES = 1_000
MAX_PAGINATION_NODES = 20_000
MAX_PAGINATION_TEXT_CHARS = 500_000
MAX_PAGINATION_LINKS = 1_000
MAX_URL_CHARS = 8_192
MAX_DYNAMIC_INVENTORY_BYTES = 5_000_000
MAX_FINGERPRINT_SOURCE_CHARS = 5_000_000

_NEXT_LABEL = re.compile(r"^(?:next(?:\s+page)?|load\s+more)$", re.IGNORECASE)
_PAGE_ROUTE = re.compile(r"^(?P<prefix>.*)/page-(?P<number>[1-9]\d*)/?$")
_PAGINATION_EXCLUDED_TAGS = {"script", "style", "template", "noscript"}
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
_FIRST_PARTY_CARD_CLASSES = {
    "career-single",
    "job-list__job",
    "position-container",
    "wmc-position-container",
}
_SEMANTIC_JOB_CARD_CLASS = "job-card"
_SEMANTIC_NON_TITLE_CLASSES = {
    "jc-company",
    "jc-description",
    "jc-location",
    "job-company",
    "job-location",
}
_TITLE_TAGS = {"h2", "h3", "h4", "h5", "h6"}
_HIDDEN_STYLE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b",
    re.IGNORECASE,
)
_CONRAD_DETAIL_PATH = re.compile(
    r"^(?P<directory>(?:/[^/]+)*/)?[^/]+?(?P<locale>-[a-z]{2,3})?"
    r"-j[1-9]\d*\.html$",
    re.IGNORECASE,
)
_NONZERO_COUNT = re.compile(r"[1-9]\d{0,8}")
_DYNAMIC_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
_APPLICANT_MANAGER_POSITION = re.compile(r"[A-Za-z][1-9]\d{0,18}")
_SEMANTIC_JOB_DETAIL_PATH = re.compile(
    r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?job/"
    r"(?:\d+|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})"
    r"-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*/?$",
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


@dataclass(frozen=True)
class DynamicInventoryPayloadResult:
    candidates: tuple[ListingCandidate, ...]
    inventory_complete: bool
    total: int | None


def generic_opening_inventory_fingerprint(page: Page) -> str | None:
    """Fingerprint bounded, validated listing identity rather than page decoration."""

    source_url = _canonical_public_https_url(page.final_url or page.url)
    if (
        source_url is None
        or not isinstance(page.html, str)
        or len(page.html) > MAX_FINGERPRINT_SOURCE_CHARS
    ):
        return None
    candidates = extract_listing_candidates(page.html, source_url)
    semantic_parent_urls = _semantic_job_card_parent_urls(page.html, source_url)
    candidates = [
        candidate
        for candidate in candidates
        if not (
            candidate.origin == "parent_card"
            and candidate.url in semantic_parent_urls
        )
    ]
    candidates.extend(_extract_first_party_job_cards(page.html, source_url))
    candidates.extend(_extract_conrad_inventory(page.html, source_url))
    candidates.extend(_extract_applicant_manager_inventory(page.html, source_url))
    candidates.extend(
        _extract_embedded_dynamic_inventory(page.html, source_url).candidates
    )
    identity = sorted(
        {
            (
                " ".join(candidate.title.casefold().split()),
                candidate.url.rstrip("/"),
                " ".join((candidate.location or "").casefold().split()),
            )
            for candidate in candidates[:MAX_CANDIDATES]
        }
    )
    if not identity:
        return None
    encoded = json.dumps(
        identity,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class _PaginationLink:
    href: str
    rel_next: bool
    text_parts: list[str]
    closed: bool


@dataclass
class _FirstPartyAnchor:
    href: str
    direct_text_parts: list[str]


@dataclass
class _FirstPartyCard:
    kind: str
    title_parts: list[str]
    title_count: int
    anchors: list[_FirstPartyAnchor]


@dataclass
class _FirstPartyElement:
    tag: str
    blocked: bool
    card: _FirstPartyCard | None = None
    title_card: _FirstPartyCard | None = None
    anchor: _FirstPartyAnchor | None = None


@dataclass
class _ConradCard:
    anchors: list[_FirstPartyAnchor]
    has_form: bool = False


@dataclass
class _ConradElement:
    tag: str
    blocked: bool
    filter_form: bool = False
    listing: bool = False
    card: _ConradCard | None = None
    anchor: _FirstPartyAnchor | None = None


@dataclass
class _ApplicantManagerCell:
    classes: set[str]
    text_parts: list[str]
    anchors: list[_ApplicantManagerAnchor]


@dataclass
class _ApplicantManagerAnchor:
    href: str
    classes: set[str]
    text_parts: list[str]


@dataclass
class _ApplicantManagerRow:
    row_id: str
    cells: list[_ApplicantManagerCell]


@dataclass
class _ApplicantManagerElement:
    tag: str
    blocked: bool
    table: bool = False
    header: bool = False
    body: bool = False
    row: _ApplicantManagerRow | None = None
    cell: _ApplicantManagerCell | None = None
    anchor: _ApplicantManagerAnchor | None = None
    header_parts: list[str] | None = None


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
        self.malformed = False
        self._stack: list[tuple[str, _PaginationLink | None, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        self.nodes += 1
        if self.nodes > MAX_PAGINATION_NODES:
            raise _LimitReached
        values = {name.casefold(): (value or "") for name, value in attrs}
        excluded = bool(self._stack and self._stack[-1][2]) or (
            tag in _PAGINATION_EXCLUDED_TAGS
        )
        if excluded:
            if tag not in self._VOID_TAGS:
                self._stack.append((tag, None, True))
            return

        if tag in {"a", "link", "base"} and len(values) != len(attrs):
            self.malformed = True
        if tag == "base" and values.get("href"):
            self.base_url = urljoin(self.base_url, values["href"])

        link = None
        if tag in {"a", "link"} and values.get("href"):
            if len(values["href"]) > MAX_URL_CHARS:
                raise _LimitReached
            if tag == "a" and any(item[1] is not None for item in self._stack):
                self.malformed = True
            if len(self.links) >= MAX_PAGINATION_LINKS:
                raise _LimitReached
            rel = {item.casefold() for item in values.get("rel", "").split()}
            link = _PaginationLink(
                values["href"], "next" in rel, [], tag == "link"
            )
            self.links.append(link)
        if tag not in self._VOID_TAGS:
            self._stack.append((tag, link, False))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not data or (self._stack and self._stack[-1][2]):
            return
        self.text_chars += len(data)
        if self.text_chars > MAX_PAGINATION_TEXT_CHARS:
            raise _LimitReached
        for _tag, link, _excluded in self._stack:
            if link is not None:
                link.text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                link = self._stack[index][1]
                if link is not None:
                    link.closed = True
                del self._stack[index:]
                return

    def explicit_next_urls(self, source_url: str) -> list[str]:
        output: list[str] = []
        for link in self.links:
            text = " ".join(" ".join(link.text_parts).split())
            target = urljoin(self.base_url, link.href)
            strict_route = _is_consecutive_page_route(source_url, target)
            marked = bool(link.rel_next or _NEXT_LABEL.fullmatch(text) or strict_route)
            if marked and not link.closed:
                self.malformed = True
                continue
            if marked:
                if not _is_continuous_pagination_url(source_url, target):
                    self.malformed = True
                    continue
                output.append(target)
        return output


class _FirstPartyJobCardParser(HTMLParser):
    """Recognize a small set of first-party career card conventions."""

    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.nodes = 0
        self.text_chars = 0
        self.stack: list[_FirstPartyElement] = []
        self.results: list[ListingCandidate] = []
        self.semantic_card_parent_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        self.nodes += 1
        if self.nodes > MAX_PAGINATION_NODES:
            raise _LimitReached
        values = {name.casefold(): (value or "") for name, value in attrs}
        blocked = bool(self.stack and self.stack[-1].blocked) or _is_blocked_element(
            tag, values
        )
        card = None
        classes = set(values.get("class", "").casefold().split())
        marker_classes = classes & _FIRST_PARTY_CARD_CLASSES
        marker = next(iter(marker_classes)) if len(marker_classes) == 1 else None
        if not blocked and self._current_card() is None:
            if (
                tag == "article"
                and _SEMANTIC_JOB_CARD_CLASS in classes
                and not marker_classes
            ):
                card = _FirstPartyCard(_SEMANTIC_JOB_CARD_CLASS, [], 0, [])
            elif marker:
                card = _FirstPartyCard(marker, [], 0, [])

        owner = card or self._current_card()
        title_card = None
        anchor = None
        if not blocked and owner is not None:
            if owner.kind in {
                "career-single",
                "job-list__job",
                _SEMANTIC_JOB_CARD_CLASS,
            } and tag in _TITLE_TAGS and not (
                owner.kind == _SEMANTIC_JOB_CARD_CLASS
                and classes.intersection(_SEMANTIC_NON_TITLE_CLASSES)
            ):
                owner.title_count += 1
                title_card = owner
            if tag == "a" and values.get("href"):
                anchor = _FirstPartyAnchor(values["href"], [])
                owner.anchors.append(anchor)

        if tag not in _PaginationParser._VOID_TAGS:
            self.stack.append(
                _FirstPartyElement(tag, blocked, card, title_card, anchor)
            )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _PaginationParser._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not data or not self.stack or self.stack[-1].blocked:
            return
        self.text_chars += len(data)
        if self.text_chars > MAX_PAGINATION_TEXT_CHARS:
            raise _LimitReached
        for element in self.stack:
            if element.title_card is not None:
                element.title_card.title_parts.append(data)
        # Nested spans commonly contain location, not part of the job title.
        if self.stack[-1].anchor is not None:
            self.stack[-1].anchor.direct_text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                while len(self.stack) > index:
                    element = self.stack.pop()
                    if element.card is not None:
                        self._emit_card(element.card)
                return

    def close_open_elements(self) -> None:
        while self.stack:
            element = self.stack.pop()
            if element.card is not None:
                self._emit_card(element.card)

    def _current_card(self) -> _FirstPartyCard | None:
        for element in reversed(self.stack):
            if element.card is not None:
                return element.card
        return None

    def _emit_card(self, card: _FirstPartyCard) -> None:
        if len(self.results) >= MAX_CANDIDATES:
            raise _LimitReached
        if card.kind == _SEMANTIC_JOB_CARD_CLASS:
            self._record_semantic_card_parent_urls(card)
        if card.kind in {
            "career-single",
            "job-list__job",
            _SEMANTIC_JOB_CARD_CLASS,
        }:
            if card.title_count != 1:
                return
            title = _clean_card_text(card.title_parts)
            if card.kind == _SEMANTIC_JOB_CARD_CLASS:
                detail_urls = {
                    url
                    for anchor in card.anchors
                    if (
                        url := _semantic_job_card_detail_url(
                            anchor.href,
                            self.source_url,
                        )
                    ) is not None
                }
                if len(detail_urls) == 1:
                    self._emit_semantic_job_card_candidate(
                        title,
                        next(iter(detail_urls)),
                    )
                return
            if len(card.anchors) != 1:
                return
            self._emit_candidate(title, card.anchors[0].href)
            return
        for anchor in card.anchors:
            title = _clean_card_text(anchor.direct_text_parts)
            self._emit_candidate(title, anchor.href)

    def _emit_candidate(self, title: str, href: str) -> None:
        if len(self.results) >= MAX_CANDIDATES:
            raise _LimitReached
        if not title or len(title) > 200:
            return
        url = validate_output_url(href, self.source_url, title=title)
        if url is not None:
            self.results.append(
                ListingCandidate(title, url, self.source_url, "first_party_job_card")
            )

    def _emit_semantic_job_card_candidate(self, title: str, href: str) -> None:
        if len(self.results) >= MAX_CANDIDATES or not title or len(title) > 200:
            return
        url = _semantic_job_card_detail_url(href, self.source_url)
        if url is not None:
            self.results.append(
                ListingCandidate(title, url, self.source_url, "semantic_job_card")
            )

    def _record_semantic_card_parent_urls(self, card: _FirstPartyCard) -> None:
        for anchor in card.anchors:
            url = _canonical_public_https_url(
                urljoin(self.source_url, anchor.href), origin_url=self.source_url
            )
            if url is None:
                continue
            path = urlparse(url).path.casefold()
            if (
                path.startswith("/job/")
                or path.startswith("/apply/")
                or path.startswith("/signup/")
                or path.startswith("/sign-up/")
            ):
                self.semantic_card_parent_urls.add(url)


class _ConradInventoryParser(HTMLParser):
    """Recognize bounded Rexx/Conrad listing roots from structural evidence."""

    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.nodes = 0
        self.text_chars = 0
        self.stack: list[_ConradElement] = []
        self.filter_names: set[str] = set()
        self.vacancy_count: int | None = None
        self.cards: list[_ConradCard] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        self.nodes += 1
        if self.nodes > MAX_PAGINATION_NODES:
            raise _LimitReached
        values = {name.casefold(): (value or "") for name, value in attrs}
        blocked = bool(self.stack and self.stack[-1].blocked) or _is_blocked_element(
            tag, values
        )
        classes = set(values.get("class", "").casefold().split())
        filter_form = (
            not blocked
            and tag == "form"
            and (
                values.get("id", "").casefold() == "list_filter"
                or values.get("name", "").casefold() == "list_filter"
            )
        )
        listing = (
            not blocked
            and (
                values.get("id", "").casefold() == "joboffer_table_container"
                or "real_table_container" in classes
            )
        )
        if listing:
            raw_count = values.get("data-count")
            if raw_count and _NONZERO_COUNT.fullmatch(raw_count):
                self.vacancy_count = int(raw_count)

        in_filter_form = filter_form or self._in_filter_form()
        if not blocked and in_filter_form and tag in {"input", "select", "textarea"}:
            name = values.get("name", "")
            if name.casefold().startswith("filter["):
                self.filter_names.add(name.casefold())

        owner = self._current_card()
        card = None
        if (
            not blocked
            and owner is None
            and self._in_listing(listing)
            and "joboffer_container" in classes
        ):
            card = _ConradCard([])
            owner = card
        anchor = None
        if not blocked and owner is not None:
            if tag == "form":
                owner.has_form = True
            if tag == "a" and values.get("href"):
                anchor = _FirstPartyAnchor(values["href"], [])
                owner.anchors.append(anchor)

        if tag not in _PaginationParser._VOID_TAGS:
            self.stack.append(
                _ConradElement(tag, blocked, filter_form, listing, card, anchor)
            )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _PaginationParser._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not data or not self.stack or self.stack[-1].blocked:
            return
        self.text_chars += len(data)
        if self.text_chars > MAX_PAGINATION_TEXT_CHARS:
            raise _LimitReached
        if self.stack[-1].anchor is not None:
            self.stack[-1].anchor.direct_text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                while len(self.stack) > index:
                    element = self.stack.pop()
                    if element.card is not None:
                        self.cards.append(element.card)
                return

    def close_open_elements(self) -> None:
        while self.stack:
            element = self.stack.pop()
            if element.card is not None:
                self.cards.append(element.card)

    def candidates(self) -> list[ListingCandidate]:
        if len(self.filter_names) < 2 or self.vacancy_count is None:
            return []
        recognized: list[tuple[str, str, tuple[str, str]]] = []
        for card in self.cards:
            if card.has_form or len(card.anchors) != 1:
                continue
            anchor = card.anchors[0]
            title = _clean_card_text(anchor.direct_text_parts)
            url = _canonical_public_https_url(
                urljoin(self.source_url, anchor.href),
                origin_url=self.source_url,
            )
            schema = _conrad_detail_schema(url) if url is not None else None
            if title and len(title) <= 200 and schema is not None:
                recognized.append((title, url, schema))
        if len(recognized) < 2 or len({item[2] for item in recognized}) != 1:
            return []
        return [
            ListingCandidate(title, url, self.source_url, "first_party_job_card")
            for title, url, _schema in recognized[:MAX_CANDIDATES]
        ]

    def _current_card(self) -> _ConradCard | None:
        for element in reversed(self.stack):
            if element.card is not None:
                return element.card
        return None

    def _in_filter_form(self) -> bool:
        return any(element.filter_form for element in self.stack)

    def _in_listing(self, current_listing: bool = False) -> bool:
        return current_listing or any(element.listing for element in self.stack)


class _ApplicantManagerInventoryParser(HTMLParser):
    """Recognize Applicant Manager's server-rendered careers table contract."""

    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.nodes = 0
        self.text_chars = 0
        self.stack: list[_ApplicantManagerElement] = []
        self.table_count = 0
        self.headers: list[str] = []
        self.rows: list[_ApplicantManagerRow] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        self.nodes += 1
        if self.nodes > MAX_PAGINATION_NODES:
            raise _LimitReached
        values = {name.casefold(): (value or "") for name, value in attrs}
        blocked = bool(self.stack and self.stack[-1].blocked) or _is_blocked_element(
            tag, values
        )
        table = (
            not blocked
            and tag == "table"
            and values.get("id", "").casefold() == "careers_table"
        )
        if table:
            self.table_count += 1
        in_table = table or self._inside("table")
        header = not blocked and tag == "thead" and in_table
        body = not blocked and tag == "tbody" and in_table
        row = None
        if not blocked and tag == "tr" and self._inside("body"):
            row = _ApplicantManagerRow(values.get("id", ""), [])
        owner_row = row or self._current_row()
        cell = None
        if not blocked and tag == "td" and owner_row is not None:
            cell = _ApplicantManagerCell(
                set(values.get("class", "").casefold().split()), [], []
            )
        owner_cell = cell or self._current_cell()
        anchor = None
        if not blocked and tag == "a" and owner_cell is not None and values.get("href"):
            anchor = _ApplicantManagerAnchor(
                values["href"],
                set(values.get("class", "").casefold().split()),
                [],
            )
            owner_cell.anchors.append(anchor)
        header_parts = (
            [] if not blocked and tag == "th" and self._inside("header") else None
        )
        if tag not in _PaginationParser._VOID_TAGS:
            self.stack.append(
                _ApplicantManagerElement(
                    tag, blocked, table, header, body, row, cell, anchor, header_parts
                )
            )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _PaginationParser._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not data or not self.stack or self.stack[-1].blocked:
            return
        self.text_chars += len(data)
        if self.text_chars > MAX_PAGINATION_TEXT_CHARS:
            raise _LimitReached
        cell = self._current_cell()
        if cell is not None:
            cell.text_parts.append(data)
        anchor = self._current_anchor()
        if anchor is not None:
            anchor.text_parts.append(data)
        for element in reversed(self.stack):
            if element.header_parts is not None:
                element.header_parts.append(data)
                break

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                while len(self.stack) > index:
                    self._finish(self.stack.pop())
                return

    def close_open_elements(self) -> None:
        while self.stack:
            self._finish(self.stack.pop())

    def candidates(self) -> list[ListingCandidate]:
        if self.table_count != 1:
            return []
        normalized_headers = [header.casefold() for header in self.headers]
        if normalized_headers.count("job title") != 1 or normalized_headers.count(
            "location"
        ) != 1:
            return []
        title_index = normalized_headers.index("job title")
        location_index = normalized_headers.index("location")
        output: list[ListingCandidate] = []
        for row in self.rows:
            if max(title_index, location_index) >= len(row.cells):
                return []
            title_cell = row.cells[title_index]
            location = _clean_card_text(row.cells[location_index].text_parts)
            if (
                "pos_title" not in title_cell.classes
                or len(title_cell.anchors) != 1
                or not location
                or len(location) > 300
            ):
                return []
            anchor = title_cell.anchors[0]
            title = _clean_card_text(anchor.text_parts)
            url = _applicant_manager_detail_url(
                anchor.href, self.source_url, row.row_id
            )
            if (
                "pos_title" not in anchor.classes
                or not title
                or _clean_card_text(title_cell.text_parts) != title
                or len(title) > 300
                or url is None
            ):
                return []
            output.append(
                ListingCandidate(
                    title,
                    url,
                    self.source_url,
                    "applicant_manager_table",
                    location,
                )
            )
        return output[:MAX_CANDIDATES]

    def _inside(self, field: str) -> bool:
        return any(getattr(element, field) for element in self.stack)

    def _current_row(self) -> _ApplicantManagerRow | None:
        return next(
            (element.row for element in reversed(self.stack) if element.row is not None),
            None,
        )

    def _current_cell(self) -> _ApplicantManagerCell | None:
        return next(
            (element.cell for element in reversed(self.stack) if element.cell is not None),
            None,
        )

    def _current_anchor(self) -> _ApplicantManagerAnchor | None:
        return next(
            (
                element.anchor
                for element in reversed(self.stack)
                if element.anchor is not None
            ),
            None,
        )

    def _finish(self, element: _ApplicantManagerElement) -> None:
        if element.header_parts is not None:
            self.headers.append(_clean_card_text(element.header_parts))
        if element.cell is not None:
            row = self._current_row()
            if row is not None:
                row.cells.append(element.cell)
        if element.row is not None:
            if len(self.rows) >= MAX_CANDIDATES:
                raise _LimitReached
            self.rows.append(element.row)


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
        dynamic_inventory = _extract_embedded_dynamic_inventory(
            current_page.html,
            current_url,
        )
        extracted = extract_listing_candidates(current_page.html, current_url)
        semantic_card_parent_urls = _semantic_job_card_parent_urls(
            current_page.html, current_url
        )
        extracted = [
            candidate
            for candidate in extracted
            if not (
                candidate.origin == "parent_card"
                and candidate.url in semantic_card_parent_urls
            )
        ]
        extracted.extend(
            _extract_first_party_job_cards(current_page.html, current_url)
        )
        extracted.extend(
            _extract_conrad_inventory(current_page.html, current_url)
        )
        extracted.extend(
            _extract_applicant_manager_inventory(current_page.html, current_url)
        )
        extracted.extend(dynamic_inventory.candidates)
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
            inventory_complete = saw_pagination or dynamic_inventory.inventory_complete
            stop_reason = (
                "complete"
                if inventory_complete
                else "single_page_unbounded"
            )
            traces.append(InventoryTraceEntry(current_url, page_candidate_count, stop_reason))
            return GenericOpeningInventoryResult(
                candidates=tuple(candidates),
                pages_fetched=pages_fetched,
                inventory_complete=inventory_complete,
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
        if response_url != next_url:
            stop_reason = "unsafe_response_url"
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


def has_strong_generic_opening_inventory(page: Page) -> bool:
    """Attest that a first-party page contains a bounded structured job inventory."""

    source_url = _canonical_public_https_url(page.final_url or page.url)
    if source_url is None:
        return False
    # Keep S5 admission deliberately narrower than S6 extraction. These parsers
    # require multiple coherent detail records and reject navigation/card noise.
    dynamic = _extract_embedded_dynamic_inventory(page.html, source_url)
    return bool(
        (dynamic.inventory_complete and len(dynamic.candidates) >= 2)
        or len(_extract_semantic_job_cards(page.html, source_url)) >= 2
        or len(_extract_conrad_inventory(page.html, source_url)) >= 2
        or len(_extract_applicant_manager_inventory(page.html, source_url)) >= 2
    )


def parse_dynamic_inventory_payload(
    body: str,
    *,
    endpoint_url: str,
    detail_url_template: str | None,
    complete_hint: bool,
) -> DynamicInventoryPayloadResult:
    """Validate a public JSON inventory against an evidence-derived detail route."""

    empty = DynamicInventoryPayloadResult((), False, None)
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_DYNAMIC_INVENTORY_BYTES:
        return empty
    endpoint = _canonical_public_https_url(endpoint_url)
    if endpoint is None:
        return empty
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return empty

    records, total = _dynamic_records(payload)
    if records is None or len(records) > MAX_CANDIDATES:
        return empty
    candidates: list[ListingCandidate] = []
    seen: set[str] = set()
    for record in records:
        candidate = _dynamic_record_candidate(
            record,
            endpoint_url=endpoint,
            detail_url_template=detail_url_template,
        )
        if candidate is None:
            return empty
        key = candidate.url.rstrip("/")
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)

    complete = bool(
        (complete_hint and total in {None, len(records)})
        or (total is not None and total == len(records))
    )
    return DynamicInventoryPayloadResult(tuple(candidates), complete, total)


def _explicit_next_urls(html: str, source_url: str) -> tuple[list[str], bool]:
    parser = _PaginationParser(source_url)
    try:
        parser.feed(html)
        parser.close()
    except (_LimitReached, TypeError, ValueError):
        return [], True
    output = list(dict.fromkeys(parser.explicit_next_urls(source_url)))
    if parser.malformed or len(output) > 1:
        return [], True
    return output, False


def _is_continuous_pagination_url(source_url: str, target_url: str) -> bool:
    source = urlparse(source_url)
    target = urlparse(target_url)
    if not _same_url_origin(source, target):
        # Preserve the unsafe-URL classification for explicitly marked links.
        return True

    target_route = _PAGE_ROUTE.fullmatch(target.path)
    if target_route is not None:
        return _is_consecutive_page_route(source_url, target_url)

    target_query = _query_page_number(target.query)
    if _has_page_query(target.query) and target_query is None:
        return False
    if target_query is None:
        return True
    source_query = _query_page_number(source.query)
    if _has_page_query(source.query) and source_query is None:
        return False
    source_number = source_query if source_query is not None else 1
    return bool(
        source.path == target.path
        and target_query == source_number + 1
        and _query_without_page(source.query) == _query_without_page(target.query)
    )


def _is_consecutive_page_route(source_url: str, target_url: str) -> bool:
    source = urlparse(source_url)
    target = urlparse(target_url)
    if not _same_url_origin(source, target) or source.query != target.query:
        return False
    target_match = _PAGE_ROUTE.fullmatch(target.path)
    if target_match is None:
        return False
    source_match = _PAGE_ROUTE.fullmatch(source.path)
    if source_match is None:
        return bool(
            int(target_match.group("number")) == 2
            and source.path.rstrip("/") == target_match.group("prefix").rstrip("/")
        )
    return bool(
        source_match.group("prefix").rstrip("/")
        == target_match.group("prefix").rstrip("/")
        and int(target_match.group("number"))
        == int(source_match.group("number")) + 1
    )


def _query_page_number(query: str) -> int | None:
    values = [
        value
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key == "page"
    ]
    if len(values) != 1 or not re.fullmatch(r"[1-9]\d*", values[0]):
        return None
    return int(values[0])


def _has_page_query(query: str) -> bool:
    return any(
        key == "page"
        for key, _value in parse_qsl(query, keep_blank_values=True)
    )


def _query_without_page(query: str) -> list[tuple[str, str]]:
    return sorted(
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key != "page"
    )


def _same_url_origin(source, target) -> bool:
    try:
        source_port = source.port or (
            443 if source.scheme.casefold() == "https" else None
        )
        target_port = target.port or (
            443 if target.scheme.casefold() == "https" else None
        )
    except ValueError:
        return False
    return bool(
        source.scheme.casefold() == target.scheme.casefold() == "https"
        and (source.hostname or "").casefold().rstrip(".")
        == (target.hostname or "").casefold().rstrip(".")
        and source_port == target_port
    )


def _extract_first_party_job_cards(html: str, source_url: str) -> list[ListingCandidate]:
    if not isinstance(html, str):
        return []
    parser = _FirstPartyJobCardParser(source_url)
    try:
        parser.feed(html)
        parser.close()
        parser.close_open_elements()
    except (_LimitReached, TypeError, ValueError):
        pass
    return parser.results[:MAX_CANDIDATES]


def _extract_semantic_job_cards(html: str, source_url: str) -> list[ListingCandidate]:
    return [
        candidate
        for candidate in _extract_first_party_job_cards(html, source_url)
        if candidate.origin == "semantic_job_card"
    ]


def _semantic_job_card_parent_urls(html: str, source_url: str) -> set[str]:
    if not isinstance(html, str):
        return set()
    parser = _FirstPartyJobCardParser(source_url)
    try:
        parser.feed(html)
        parser.close()
        parser.close_open_elements()
    except (_LimitReached, TypeError, ValueError):
        return set()
    return parser.semantic_card_parent_urls


def _semantic_job_card_detail_url(href: str, source_url: str) -> str | None:
    """Validate an SSR job-card's concrete, first-party detail route."""

    url = _canonical_public_https_url(urljoin(source_url, href), origin_url=source_url)
    if url is None:
        return None
    source = urlparse(source_url)
    target = urlparse(url)
    if (
        not _same_url_origin(source, target)
        or target.query
        or _SEMANTIC_JOB_DETAIL_PATH.fullmatch(target.path) is None
    ):
        return None
    return url


def _extract_conrad_inventory(html: str, source_url: str) -> list[ListingCandidate]:
    if not isinstance(html, str):
        return []
    parser = _ConradInventoryParser(source_url)
    try:
        parser.feed(html)
        parser.close()
        parser.close_open_elements()
    except (_LimitReached, TypeError, ValueError):
        return []
    return parser.candidates()


def _extract_applicant_manager_inventory(
    html: str,
    source_url: str,
) -> list[ListingCandidate]:
    if not isinstance(html, str) or not _is_applicant_manager_careers_url(source_url):
        return []
    parser = _ApplicantManagerInventoryParser(source_url)
    try:
        parser.feed(html)
        parser.close()
        parser.close_open_elements()
    except (_LimitReached, TypeError, ValueError):
        pass
    return parser.candidates()


def _is_applicant_manager_careers_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except (TypeError, ValueError):
        return False
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    return bool(
        parsed.scheme.casefold() == "https"
        and (
            hostname == "theapplicantmanager.com"
            or hostname.endswith(".theapplicantmanager.com")
        )
        and parsed.path.rstrip("/").casefold() == "/careers"
        and len(query) == 1
        and query[0][0].casefold() == "co"
        and _APPLICANT_MANAGER_POSITION.fullmatch(query[0][1])
    )


def _applicant_manager_detail_url(
    href: str,
    source_url: str,
    row_id: str,
) -> str | None:
    try:
        candidate = urljoin(source_url, href)
        parsed = urlparse(candidate)
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except (TypeError, ValueError):
        return None
    if (
        parsed.path.rstrip("/").casefold() != "/jobs"
        or len(query) != 1
        or query[0][0].casefold() != "pos"
        or not _APPLICANT_MANAGER_POSITION.fullmatch(query[0][1])
        or row_id.casefold() != f"tr{query[0][1]}".casefold()
    ):
        return None
    return _canonical_public_https_url(candidate, origin_url=source_url)


def _extract_embedded_dynamic_inventory(
    html: str,
    source_url: str,
) -> DynamicInventoryPayloadResult:
    empty = DynamicInventoryPayloadResult((), False, None)
    if (
        not isinstance(html, str)
        or len(html.encode("utf-8")) > MAX_DYNAMIC_INVENTORY_BYTES
    ):
        return empty
    matches = re.findall(
        r'<script\b(?=[^>]*\btype=["\']application/json["\'])'
        r'(?=[^>]*\bdata-dynamic-job-inventory(?:=["\'][^"\']*["\'])?)'
        r'[^>]*>([\s\S]{1,5000000}?)</script\s*>',
        html[: MAX_DYNAMIC_INVENTORY_BYTES + 100_000],
        flags=re.IGNORECASE,
    )
    if len(matches) != 1:
        return _extract_sveltekit_ssr_inventory(html, source_url)
    try:
        envelope = json.loads(matches[0])
    except (json.JSONDecodeError, TypeError, ValueError):
        return empty
    if not isinstance(envelope, dict) or set(envelope) != {
        "endpoint_url",
        "inventory_complete",
        "jobs",
        "total",
    }:
        return empty
    if envelope.get("inventory_complete") is not True:
        return empty
    endpoint_url = envelope.get("endpoint_url")
    jobs = envelope.get("jobs")
    total = envelope.get("total")
    if (
        not isinstance(endpoint_url, str)
        or not isinstance(jobs, list)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total != len(jobs)
        or total > MAX_CANDIDATES
    ):
        return empty
    endpoint = _canonical_public_https_url(endpoint_url, origin_url=source_url)
    if endpoint is None:
        return empty
    candidates: list[ListingCandidate] = []
    for record in jobs:
        if not isinstance(record, dict) or not set(record).issubset(
            {"title", "url", "location"}
        ) or not {"title", "url"}.issubset(record):
            return empty
        title = record.get("title")
        url = record.get("url")
        location = record.get("location")
        if (
            not isinstance(title, str)
            or not isinstance(url, str)
            or (location is not None and not isinstance(location, str))
        ):
            return empty
        validated = _validate_dynamic_detail_url(
            url,
            source_url,
            title,
            template_derived=True,
        )
        if validated is None:
            return empty
        candidates.append(
            ListingCandidate(
                title=title,
                url=validated,
                source_url=endpoint,
                origin="first_party_dynamic_inventory",
                location=location,
            )
        )
    return DynamicInventoryPayloadResult(tuple(candidates), True, total)


class _JsLiteralParser:
    """Parse a deliberately small JSON-compatible subset of JS literals."""

    _TOKEN = re.compile(
        r'\s*(?:(?P<string>"(?:\\.|[^"\\])*")|'
        r'(?P<number>-?(?:0|[1-9]\d*))|'
        r'(?P<identifier>[A-Za-z_$][A-Za-z0-9_$]*)|'
        r'(?P<punct>[{}\[\]:,]))'
    )

    def __init__(self, source: str, start: int) -> None:
        self.source = source
        self.position = start
        self.items = 0

    def parse(self) -> object:
        value = self._value(0)
        return value

    def _token(self) -> tuple[str, str]:
        match = self._TOKEN.match(self.source, self.position)
        if match is None:
            raise ValueError("unsupported JavaScript literal")
        self.position = match.end()
        self.items += 1
        if self.items > 50_000:
            raise ValueError("JavaScript literal token limit exceeded")
        for kind in ("string", "number", "identifier", "punct"):
            value = match.group(kind)
            if value is not None:
                return kind, value
        raise ValueError("missing JavaScript literal token")

    def _value(self, depth: int) -> object:
        if depth > 6:
            raise ValueError("JavaScript literal nesting limit exceeded")
        kind, token = self._token()
        if kind == "string":
            return json.loads(token)
        if kind == "number":
            return int(token)
        if kind == "identifier":
            if token == "true":
                return True
            if token == "false":
                return False
            if token == "null":
                return None
            raise ValueError("unsupported JavaScript identifier")
        if token == "[":
            return self._array(depth + 1)
        if token == "{":
            return self._object(depth + 1)
        raise ValueError("expected JavaScript literal value")

    def _array(self, depth: int) -> list[object]:
        values: list[object] = []
        if self._peek_punctuation("]"):
            self._token()
            return values
        while True:
            if len(values) >= MAX_CANDIDATES:
                raise ValueError("JavaScript literal array limit exceeded")
            values.append(self._value(depth))
            _, punctuation = self._token()
            if punctuation == "]":
                return values
            if punctuation != ",":
                raise ValueError("malformed JavaScript literal array")

    def _object(self, depth: int) -> dict[str, object]:
        values: dict[str, object] = {}
        if self._peek_punctuation("}"):
            self._token()
            return values
        while True:
            kind, key = self._token()
            if kind not in {"string", "identifier"}:
                raise ValueError("invalid JavaScript object key")
            if kind == "string":
                key = json.loads(key)
            if key in values or len(values) >= 100:
                raise ValueError("invalid JavaScript object shape")
            _, punctuation = self._token()
            if punctuation != ":":
                raise ValueError("missing JavaScript object colon")
            values[key] = self._value(depth)
            _, punctuation = self._token()
            if punctuation == "}":
                return values
            if punctuation != ",":
                raise ValueError("malformed JavaScript object")

    def _peek_punctuation(self, punctuation: str) -> bool:
        match = self._TOKEN.match(self.source, self.position)
        return bool(match and match.group("punct") == punctuation)


def _extract_sveltekit_ssr_inventory(
    html: str,
    source_url: str,
) -> DynamicInventoryPayloadResult:
    """Read a title-filtered SvelteKit SSR inventory without executing script."""

    empty = DynamicInventoryPayloadResult((), False, None)
    jobs_marker = "jobs:{currentPage:"
    request_marker = "initialJobsListRequest:"
    if html.count(jobs_marker) != 1 or html.count(request_marker) != 1:
        return empty
    try:
        parsed_source = urlparse(source_url)
        query_pairs = parse_qsl(parsed_source.query, keep_blank_values=True)
    except (TypeError, ValueError):
        return empty
    query_values = [
        value.strip()
        for key, value in query_pairs
        if key.casefold() == "query" and value.strip()
    ]
    if len(query_values) != 1:
        return empty
    if not parsed_source.path.rstrip("/").casefold().endswith("/careers/jobs"):
        return empty

    jobs_start = html.index(jobs_marker) + len("jobs:")
    request_start = html.index(request_marker) + len(request_marker)
    try:
        jobs = _JsLiteralParser(html, jobs_start).parse()
        request = _JsLiteralParser(html, request_start).parse()
    except (ValueError, TypeError, json.JSONDecodeError):
        return empty
    if not isinstance(jobs, dict) or set(jobs) != {"currentPage", "total"}:
        return empty
    records = jobs.get("currentPage")
    total = jobs.get("total")
    if (
        not isinstance(records, list)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total < 0
        or total > MAX_CANDIDATES
        or len(records) > MAX_CANDIDATES
    ):
        return empty
    if not isinstance(request, dict):
        return empty
    request_query = request.get("query")
    page = request.get("page")
    page_limit = request.get("pageLimit")
    if (
        not isinstance(request_query, str)
        or " ".join(request_query.casefold().split())
        != " ".join(query_values[0].casefold().split())
        or isinstance(page, bool)
        or not isinstance(page, int)
        or page < 1
        or isinstance(page_limit, bool)
        or not isinstance(page_limit, int)
        or not 1 <= page_limit <= MAX_CANDIDATES
    ):
        return empty

    allowed_keys = {
        "id",
        "internalId",
        "requisitionId",
        "title",
        "bu",
        "employeeType",
        "jobFunction",
        "isRemote",
        "location",
        "publicationDate",
    }
    detail_base = urlunparse(
        (parsed_source.scheme, parsed_source.netloc, parsed_source.path.rstrip("/"), "", "", "")
    )
    candidates: list[ListingCandidate] = []
    seen_ids: set[int] = set()
    for record in records:
        if (
            not isinstance(record, dict)
            or not {"id", "title"}.issubset(record)
            or not set(record).issubset(allowed_keys)
        ):
            return empty
        job_id = record.get("id")
        title = record.get("title")
        location = record.get("location")
        if (
            isinstance(job_id, bool)
            or not isinstance(job_id, int)
            or not 1 <= job_id <= 9_999_999_999_999_999_999
            or job_id in seen_ids
            or not isinstance(title, str)
            or not title.strip()
            or len(title) > 300
            or (location is not None and not isinstance(location, str))
            or (isinstance(location, str) and len(location) > 300)
        ):
            return empty
        detail_url = _canonical_public_https_url(
            f"{detail_base}/{job_id}",
            origin_url=source_url,
        )
        if detail_url is None:
            return empty
        seen_ids.add(job_id)
        candidates.append(
            ListingCandidate(
                title=title.strip(),
                url=detail_url,
                source_url=source_url,
                origin="sveltekit_ssr_inventory",
                location=location.strip() if isinstance(location, str) else None,
            )
        )
    complete = page == 1 and total == len(records) and len(records) <= page_limit
    return DynamicInventoryPayloadResult(tuple(candidates), complete, total)


def _dynamic_records(payload: object) -> tuple[list[object] | None, int | None]:
    if isinstance(payload, list):
        return payload, None
    if not isinstance(payload, dict):
        return None, None
    list_values = [
        payload[key]
        for key in ("jobs", "data", "records", "Records")
        if key in payload
    ]
    if len(list_values) != 1 or not isinstance(list_values[0], list):
        return None, None
    totals = [
        payload[key]
        for key in ("total", "count", "TotalRecordCount")
        if key in payload
    ]
    if len(totals) > 1 or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in totals
    ):
        return None, None
    return list_values[0], totals[0] if totals else None


def _dynamic_record_candidate(
    record: object,
    *,
    endpoint_url: str,
    detail_url_template: str | None,
) -> ListingCandidate | None:
    if not isinstance(record, dict):
        return None
    title_values = [
        _dynamic_plain_text(record[key])
        for key in ("title", "Title")
        if isinstance(record.get(key), str) and record[key].strip()
    ]
    tracking = record.get("TrackingObject")
    if isinstance(tracking, dict) and isinstance(tracking.get("TitleJson"), str):
        title_values.append(_dynamic_plain_text(tracking["TitleJson"]))
    title_values = [value for value in title_values if value]
    titles = set(title_values)
    if len(titles) != 1:
        return None
    title = title_values[0]
    if not title or len(title) > 300:
        return None

    raw_urls = [
        record[key]
        for key in ("url", "Url", "jobUrl", "detailUrl")
        if isinstance(record.get(key), str) and record[key].strip()
    ]
    if len(set(raw_urls)) > 1:
        return None
    raw_url = raw_urls[0] if raw_urls else None
    template_derived = raw_url is None and detail_url_template is not None
    if template_derived:
        raw_id = record.get("id", record.get("ID"))
        identifier = str(raw_id) if isinstance(raw_id, (str, int)) else ""
        if isinstance(raw_id, bool) or not _DYNAMIC_ID.fullmatch(identifier):
            return None
        raw_url = detail_url_template.replace("{id}", identifier)
        if "{slug}" in raw_url:
            raw_url = raw_url.replace("{slug}", _dynamic_title_slug(title))
        if "{" in raw_url or "}" in raw_url:
            return None
    if raw_url is None:
        return None
    validated = _validate_dynamic_detail_url(
        raw_url,
        endpoint_url,
        title,
        template_derived=template_derived,
    )
    if validated is None:
        return None
    return ListingCandidate(
        title=title,
        url=validated,
        source_url=endpoint_url,
        origin="first_party_dynamic_inventory",
        location=_dynamic_record_location(record),
    )


def _dynamic_record_location(record: dict) -> str | None:
    """Prefer the most specific scalar location exposed by a public inventory."""

    for key in ("metro", "locationName", "jobLocation", "location", "Location"):
        value = record.get(key)
        if not isinstance(value, str):
            continue
        location = _dynamic_plain_text(value)
        if location and len(location) <= 500:
            return location
    parts: list[str] = []
    for key in ("city", "state", "region", "country"):
        value = record.get(key)
        if not isinstance(value, str):
            continue
        part = _dynamic_plain_text(value)
        if part and part.casefold() not in {item.casefold() for item in parts}:
            parts.append(part)
    location = ", ".join(parts)
    return location[:500] or None


def _dynamic_plain_text(value: str) -> str:
    parser = _TextOnlyParser()
    try:
        parser.feed(value[:2_000])
        parser.close()
    except (TypeError, ValueError):
        return ""
    return " ".join("".join(parser.parts).split())


class _TextOnlyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _dynamic_title_slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")


def _validate_dynamic_detail_url(
    value: str,
    source_url: str,
    title: str,
    *,
    template_derived: bool = False,
) -> str | None:
    if template_derived:
        normalized = _canonical_public_https_url(value, origin_url=source_url)
        if normalized is None:
            return None
        parsed = urlparse(normalized)
        source = urlparse(source_url)
        path_parts = [part.casefold() for part in parsed.path.split("/") if part]
        if (
            parsed.hostname != source.hostname
            or parsed.port != source.port
            or parsed.query
            or parsed.fragment
            or not any(part.startswith("job") for part in path_parts[:-1])
            or len(path_parts) < 2
            or not _DYNAMIC_ID.fullmatch(path_parts[-1])
        ):
            return None
        return normalized
    validated = validate_output_url(value, source_url, title=title)
    if validated is not None:
        return validated
    # Some declared routers use a compound segment such as ``jobdetails``.
    # Keep that evidence-backed exception same-origin and title-bound.
    normalized = _canonical_public_https_url(value, origin_url=source_url)
    if normalized is None:
        return None
    path = urlparse(normalized).path.casefold()
    title_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", title.casefold())
        if len(token) > 1
    }
    path_tokens = set(re.findall(r"[a-z0-9]+", path))
    if "job" not in path or not title_tokens.intersection(path_tokens):
        return None
    return normalized


def _conrad_detail_schema(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    if parsed.query:
        return None
    match = _CONRAD_DETAIL_PATH.fullmatch(parsed.path)
    if match is None:
        return None
    return (
        (match.group("directory") or "/").casefold(),
        (match.group("locale") or "").casefold(),
    )


def _is_blocked_element(tag: str, attrs: dict[str, str]) -> bool:
    return (
        tag in {"nav", "script", "style", "template", "noscript"}
        or "hidden" in attrs
        or attrs.get("aria-hidden", "").casefold() == "true"
        or bool(_HIDDEN_STYLE.search(attrs.get("style", "")))
    )


def _clean_card_text(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())


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
