from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlparse

from .scoring import is_ats_url, is_likely_job_detail, score_job_link
from .web import RawLink, safe_normalize_url


MAX_NODES = 20_000
MAX_TEXT_CHARS = 500_000
MAX_CANDIDATES = 500
MAX_CARD_LINKS = 12
MAX_CARD_TITLES = 6
MAX_CARD_LOCATIONS = 4
MAX_TITLE_CHARS = 200
MAX_LOCATION_CHARS = 160

_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_IGNORED_TAGS = {"script", "style", "template", "noscript"}
_TITLE_TAGS = {"h2", "h3", "h4", "h5", "h6"}
_CARD_MARKER = re.compile(
    r"(?:^|[-_\s])(?:job|career|role|position|opening|vacanc(?:y|ies))"
    r"[-_\s]*(?:card|item|row|box|listing|result|post)(?:$|[-_\s])"
    r"|(?:^|[-_\s])(?:card|item|row|box|listing|result)"
    r"[-_\s]*(?:job|role|position|opening|vacanc(?:y|ies))(?:$|[-_\s])",
    re.I,
)
_STANDALONE_CARD_MARKER = re.compile(
    r"(?:^|[-_\s])(?:job[-_\s]*opening|open[-_\s]*(?:position|role)|"
    r"career[-_\s]*opportunity|vacancy|single[-_\s]*job|job[-_\s]*entry)"
    r"(?:$|[-_\s])",
    re.I,
)
_CARD_PRESENTATION_MARKER = re.compile(
    r"(?:^|[-_\s])card[-_\s]*(?:body|content|des|description|footer|header|image|title)"
    r"(?:$|[-_\s])",
    re.I,
)
_TITLE_MARKER = re.compile(
    r"(?:^|[-_\s])(?:job|role|position|opening|career(?:[-_\s]*blog)?|card)"
    r"[-_\s]*title(?:$|[-_\s])",
    re.I,
)
_LOCATION_MARKER = re.compile(
    r"(?:^|[-_\s])(?:job[-_\s]*)?location(?:$|[-_\s])",
    re.I,
)
_MAP_PIN_MARKER = re.compile(r"(?:^|[-_\s])map[-_\s]*pin(?:$|[-_\s])", re.I)
_UUID_DETAIL_PATH = re.compile(
    r"^/(?:jobs?|roles?|careers?|openings?|positions?)/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/?$",
    re.I,
)
_NON_LOCATION_TEXT = re.compile(
    r"(?:[$\u00a3\u20ac\u00a5]|\b(?:salary|compensation|per (?:hour|year)|"
    r"full[- ]?time|part[- ]?time|posted|days? ago|hours? ago)\b)",
    re.I,
)
_HIDDEN_STYLE = re.compile(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", re.I)
_ACTION_TEXT = re.compile(
    r"^(?:apply(?: now)?|details?|learn more|read more|see (?:role|job|details?)|"
    r"view (?:role|job|details?)|more)$",
    re.I,
)
_GENERIC_TITLE = re.compile(
    r"^(?:careers?|jobs?|job openings?|open (?:roles?|positions?)|all (?:roles?|jobs?)|"
    r"current openings?|search jobs?|engineering roles?)$",
    re.I,
)
_DETAIL_PATH_WORDS = {
    "apply",
    "application",
    "career",
    "careers",
    "detail",
    "details",
    "job",
    "jobs",
    "opening",
    "openings",
    "position",
    "positions",
    "role",
    "roles",
}
_DETAIL_QUERY_KEYS = {
    "id",
    "jid",
    "job_id",
    "jobid",
    "jobreqid",
    "reqid",
    "requisitionid",
}
_TITLE_STOP_WORDS = {
    "a",
    "an",
    "and",
    "at",
    "for",
    "in",
    "of",
    "on",
    "remote",
    "the",
    "to",
}
_ROLE_TITLE_WORDS = {
    "administrator",
    "analyst",
    "architect",
    "consultant",
    "designer",
    "developer",
    "director",
    "engineer",
    "executive",
    "lead",
    "manager",
    "officer",
    "recruiter",
    "scientist",
    "specialist",
    "technician",
}


@dataclass(frozen=True)
class CardListingCandidate:
    title: str
    url: str
    source_url: str
    origin: str = "parent_card"
    location: str | None = None

    def as_raw_link(self) -> RawLink:
        return RawLink(
            self.url,
            self.title,
            self.source_url,
            self.origin,
            location=self.location,
        )


@dataclass
class _TextCapture:
    card: _Card
    fallback: bool = False
    chunks: list[str] = field(default_factory=list)
    chars: int = 0
    overflow: bool = False


@dataclass
class _AnchorCapture(_TextCapture):
    href: str = ""


@dataclass
class _Card:
    titles: list[str] = field(default_factory=list)
    fallback_titles: list[str] = field(default_factory=list)
    anchors: list[tuple[str, str]] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    anchor_as_card: bool = False
    explicit_title_nodes: int = 0
    has_nested_card: bool = False
    title_overflow: bool = False
    location_overflow: bool = False


@dataclass
class _Element:
    tag: str
    blocked: bool
    marker: str = ""
    navigation: bool = False
    card: _Card | None = None
    title_capture: _TextCapture | None = None
    location_capture: _TextCapture | None = None
    anchor_capture: _AnchorCapture | None = None


class _LimitReached(Exception):
    pass


class _CardParser(HTMLParser):
    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.stack: list[_Element] = []
        self.nodes = 0
        self.text_chars = 0
        self.results: list[CardListingCandidate] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise _LimitReached

        values = {name.casefold(): (value or "") for name, value in attrs}
        inherited_block = bool(self.stack and self.stack[-1].blocked)
        blocked = inherited_block or _is_hidden(tag, values)
        marker = _attribute_marker(values)
        icon_marker = " ".join(
            (marker, values.get("data-lucide", ""), values.get("data-icon", ""))
        )
        navigation = self._inside_navigation() or _is_navigation(tag, values)
        card = None
        parent = self._current_card()
        anchor_as_card = (
            tag == "a"
            and bool(values.get("href"))
            and parent is None
            and _could_be_anchor_card(values["href"], self.source_url)
        )
        if not blocked and not navigation:
            if anchor_as_card:
                card = _Card(anchor_as_card=True)
            elif parent is None or not parent.anchor_as_card:
                if _is_semantic_card(tag, values, self.stack):
                    if parent is not None:
                        parent.has_nested_card = True
                    card = _Card()

        element = _Element(tag=tag, blocked=blocked, marker=marker, navigation=navigation, card=card)
        owner = card or self._current_card()
        if not blocked:
            if owner is not None and (
                tag in _TITLE_TAGS
                or (owner.anchor_as_card and tag == "h1")
                or tag == "p"
                or _TITLE_MARKER.search(marker)
            ):
                element.title_capture = _TextCapture(owner, fallback=tag == "p")
            if owner is not None and _LOCATION_MARKER.search(marker):
                element.location_capture = _TextCapture(owner)
            if owner is not None and tag == "a" and values.get("href"):
                element.anchor_capture = _AnchorCapture(owner, href=values["href"])
        if (
            owner is not None
            and not inherited_block
            and not navigation
            and _MAP_PIN_MARKER.search(icon_marker)
        ):
            self._start_enclosing_span_location_capture(owner)

        if tag not in _VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not data or (self.stack and self.stack[-1].blocked):
            return
        remaining = MAX_TEXT_CHARS - self.text_chars
        if remaining <= 0:
            raise _LimitReached
        value = data[:remaining]
        self.text_chars += len(value)
        for element in self.stack:
            if element.title_capture is not None:
                element.title_capture.chunks.append(value)
            if element.location_capture is not None:
                self._append_bounded_location(element.location_capture, value)
            if element.anchor_capture is not None:
                element.anchor_capture.chunks.append(value)
        if len(data) > remaining:
            raise _LimitReached

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        match_index = next(
            (index for index in range(len(self.stack) - 1, -1, -1) if self.stack[index].tag == tag),
            None,
        )
        if match_index is None:
            return
        while len(self.stack) > match_index:
            self._close_element(self.stack.pop())

    def close_open_elements(self) -> None:
        while self.stack:
            self._close_element(self.stack.pop())

    def _close_element(self, element: _Element) -> None:
        if element.title_capture is not None:
            title = _clean_text(element.title_capture.chunks, MAX_TITLE_CHARS)
            if title:
                destination = (
                    element.title_capture.card.fallback_titles
                    if element.title_capture.fallback
                    else element.title_capture.card.titles
                )
                if not element.title_capture.fallback:
                    element.title_capture.card.explicit_title_nodes += 1
                if len(destination) < MAX_CARD_TITLES:
                    destination.append(title)
                else:
                    element.title_capture.card.title_overflow = True
        if element.location_capture is not None:
            capture = element.location_capture
            location = _clean_text(capture.chunks, MAX_LOCATION_CHARS)
            if capture.overflow:
                capture.card.location_overflow = True
            elif location and _plausible_location(location):
                if len(capture.card.locations) < MAX_CARD_LOCATIONS:
                    capture.card.locations.append(location)
                else:
                    capture.card.location_overflow = True
        if element.anchor_capture is not None:
            anchor = element.anchor_capture
            text = _clean_text(anchor.chunks, MAX_TITLE_CHARS)
            if len(anchor.card.anchors) < MAX_CARD_LINKS:
                anchor.card.anchors.append((anchor.href, text))
        if element.card is not None:
            candidate = _candidate_from_card(element.card, self.source_url)
            if candidate is not None:
                self.results.append(candidate)
                if len(self.results) >= MAX_CANDIDATES:
                    raise _LimitReached

    def _current_card(self) -> _Card | None:
        for element in reversed(self.stack):
            if element.card is not None:
                return element.card
        return None

    def _inside_navigation(self) -> bool:
        return any(element.navigation for element in self.stack)

    def _start_enclosing_span_location_capture(self, owner: _Card) -> None:
        for element in reversed(self.stack):
            if element.card is not None and element.card is not owner:
                return
            if element.tag == "span":
                if element.location_capture is None:
                    element.location_capture = _TextCapture(owner)
                return

    @staticmethod
    def _append_bounded_location(capture: _TextCapture, value: str) -> None:
        remaining = MAX_LOCATION_CHARS - capture.chars
        if remaining > 0:
            captured = value[:remaining]
            capture.chunks.append(captured)
            capture.chars += len(captured)
        if len(value) > remaining:
            capture.overflow = True


def extract_card_listing_candidates(html: str, source_url: str) -> list[CardListingCandidate]:
    """Extract title/detail pairs owned by the same bounded semantic job card."""

    if not isinstance(html, str) or not safe_normalize_url(source_url):
        return []
    parser = _CardParser(source_url)
    try:
        parser.feed(html)
        parser.close()
        parser.close_open_elements()
    except (_LimitReached, TypeError, ValueError):
        pass

    seen: set[tuple[str, str]] = set()
    output: list[CardListingCandidate] = []
    for candidate in parser.results:
        key = (candidate.url.rstrip("/"), candidate.title.casefold())
        if key not in seen:
            seen.add(key)
            output.append(candidate)
    return output[:MAX_CANDIDATES]


def _candidate_from_card(card: _Card, source_url: str) -> CardListingCandidate | None:
    if (
        card.has_nested_card
        or card.title_overflow
        or not card.anchors
        or len(card.anchors) >= MAX_CARD_LINKS
        or (card.anchor_as_card and card.explicit_title_nodes != 1)
    ):
        return None

    titles = list(dict.fromkeys(title for title in card.titles if _plausible_title(title)))
    if not titles and not card.anchor_as_card:
        titles = list(
            dict.fromkeys(
                title
                for title in card.fallback_titles
                if _plausible_title(title) and _looks_like_role_title(title)
            )
        )
    detail_links: list[tuple[str, str]] = []
    allow_uuid_detail = card.explicit_title_nodes == 1 and len(titles) == 1
    for href, anchor_text in card.anchors:
        title_evidence = titles[0] if len(titles) == 1 else anchor_text
        normalized = _validated_detail_url(
            href,
            source_url,
            anchor_text,
            title=title_evidence,
            allow_uuid_detail=allow_uuid_detail,
        )
        if normalized:
            detail_links.append((normalized, anchor_text))

    unique_urls = list(dict.fromkeys(url for url, _text in detail_links))
    if len(unique_urls) != 1:
        return None
    url = unique_urls[0]

    if not titles and not card.anchor_as_card:
        titles = list(
            dict.fromkeys(
                text for candidate_url, text in detail_links
                if candidate_url == url and _plausible_title(text)
            )
        )
    uuid_detail = allow_uuid_detail and _is_uuid_detail_path(urlparse(url))
    if len(titles) != 1 or (not uuid_detail and not _title_matches_url(titles[0], url, source_url)):
        return None
    locations = list(dict.fromkeys(card.locations))
    location = locations[0] if len(locations) == 1 and not card.location_overflow else None
    return CardListingCandidate(titles[0], url, source_url, location=location)


def _validated_detail_url(
    href: str,
    source_url: str,
    text: str,
    *,
    title: str = "",
    allow_uuid_detail: bool = False,
) -> str | None:
    normalized = safe_normalize_url(href, source_url)
    if not normalized:
        return None
    parsed = urlparse(normalized)
    source = urlparse(source_url)
    if parsed.username or parsed.password:
        return None
    try:
        if parsed.port is not None:
            return None
    except ValueError:
        return None

    same_origin = (
        parsed.scheme.casefold() == source.scheme.casefold()
        and (parsed.hostname or "").casefold() == (source.hostname or "").casefold()
    )
    if not same_origin and not is_ats_url(normalized):
        return None
    candidate = score_job_link(RawLink(normalized, text, source_url, "parent_card"), source_url)
    if is_likely_job_detail(candidate):
        return normalized
    if same_origin and _is_explicit_query_detail(parsed):
        return normalized
    if same_origin and allow_uuid_detail and _is_uuid_detail_path(parsed):
        return normalized
    if (
        same_origin
        and _looks_like_role_title(title)
        and _title_matches_url(title, normalized, source_url)
        and len([part for part in parsed.path.split("/") if part]) >= 2
        and bool(set(_words(parsed.path)).intersection(_DETAIL_PATH_WORDS))
    ):
        return normalized
    return None


def _is_uuid_detail_path(parsed) -> bool:
    return not parsed.query and not parsed.fragment and bool(_UUID_DETAIL_PATH.fullmatch(parsed.path))


def _could_be_anchor_card(href: str, source_url: str) -> bool:
    """Admit a bounded anchor container before its heading has been parsed."""

    normalized = safe_normalize_url(href, source_url)
    if not normalized:
        return False
    parsed = urlparse(normalized)
    source = urlparse(source_url)
    if parsed.username or parsed.password:
        return False
    try:
        if parsed.port not in {None, 80, 443}:
            return False
    except ValueError:
        return False
    if is_ats_url(normalized):
        return True
    same_origin = (
        parsed.scheme.casefold() == source.scheme.casefold()
        and (parsed.hostname or "").casefold() == (source.hostname or "").casefold()
    )
    path_parts = [part for part in parsed.path.split("/") if part]
    return (
        same_origin
        and parsed.scheme.casefold() == "https"
        and len(path_parts) >= 2
        and bool(set(_words(parsed.path)).intersection(_DETAIL_PATH_WORDS))
    )


def _is_explicit_query_detail(parsed) -> bool:
    if not re.search(r"(?:apply|career|detail|job|opening|position|role|vacanc)", parsed.path, re.I):
        return False
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if len(query) != 1:
        return False
    key, value = query[0]
    return (
        key.casefold() in _DETAIL_QUERY_KEYS
        and 1 <= len(value) <= 128
        and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._~-]*", value))
    )


def _title_matches_url(title: str, url: str, source_url: str) -> bool:
    if is_ats_url(url) or _is_explicit_query_detail(urlparse(url)):
        return True
    target_words = {
        word for word in _words(urlparse(url).path)
        if word not in _DETAIL_PATH_WORDS and not word.isdigit() and len(word) > 1
    }
    if not target_words:
        return True
    title_words = {
        word for word in _words(title)
        if word not in _TITLE_STOP_WORDS and len(word) > 1
    }
    return bool(title_words.intersection(target_words))


def _plausible_title(value: str) -> bool:
    if not (2 <= len(value) <= MAX_TITLE_CHARS) or len(value.split()) > 20:
        return False
    if _ACTION_TEXT.fullmatch(value) or _GENERIC_TITLE.fullmatch(value):
        return False
    return bool(re.search(r"[A-Za-z]", value))


def _looks_like_role_title(value: str) -> bool:
    return bool(set(_words(value)).intersection(_ROLE_TITLE_WORDS))


def _plausible_location(value: str) -> bool:
    return (
        1 <= len(value) <= MAX_LOCATION_CHARS
        and len(value.split()) <= 16
        and bool(re.search(r"[A-Za-z]", value))
        and not _NON_LOCATION_TEXT.search(value)
    )


def _is_semantic_card(tag: str, attrs: dict[str, str], stack: list[_Element]) -> bool:
    marker = _attribute_marker(attrs)
    if _CARD_PRESENTATION_MARKER.search(marker):
        return False
    if tag == "article":
        return True
    if "schema.org/jobposting" in attrs.get("itemtype", "").casefold():
        return True
    if any(name in attrs for name in ("data-job-id", "data-jobid", "data-requisition-id")):
        return True
    if _CARD_MARKER.search(marker) or _STANDALONE_CARD_MARKER.search(marker):
        return True
    if tag == "div" and "card" in marker.casefold().split():
        return True
    if tag in {"li", "tr"} and stack:
        parent_marker = ""
        for element in reversed(stack):
            if element.tag in {"ul", "ol", "table", "tbody"}:
                parent_marker = element.marker
                break
        return bool(_CARD_MARKER.search(parent_marker))
    return False


def _is_hidden(tag: str, attrs: dict[str, str]) -> bool:
    return (
        tag in _IGNORED_TAGS
        or "hidden" in attrs
        or attrs.get("aria-hidden", "").casefold() == "true"
        or bool(_HIDDEN_STYLE.search(attrs.get("style", "")))
    )


def _is_navigation(tag: str, attrs: dict[str, str]) -> bool:
    marker = _attribute_marker(attrs).casefold()
    return (
        tag in {"header", "footer", "nav"}
        or attrs.get("role", "").casefold() in {"menu", "menubar", "navigation"}
        or any(token in marker.split() for token in ("menu", "navbar", "navigation"))
    )


def _attribute_marker(attrs: dict[str, str]) -> str:
    return " ".join(
        attrs.get(name, "")
        for name in ("class", "id", "data-testid", "data-component", "itemprop")
    )


def _clean_text(chunks: list[str], limit: int) -> str:
    return " ".join("".join(chunks)[:limit].split())


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())
