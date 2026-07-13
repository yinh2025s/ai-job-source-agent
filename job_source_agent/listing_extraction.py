from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlparse

from .scoring import is_ats_url, is_likely_job_detail, score_job_link
from .web import RawLink, safe_normalize_url


MAX_HTML_NODES = 20_000
MAX_SCRIPT_CHARS = 2_000_000
MAX_JSON_DEPTH = 24
MAX_JSON_RECORDS = 10_000
MAX_CANDIDATES = 1_000

TITLE_FIELDS = ("title", "name", "jobTitle", "job_title", "positionTitle")
URL_FIELDS = (
    "url", "absolute_url", "absoluteUrl", "hostedUrl", "applyUrl", "jobUrl",
    "job_url", "externalPath", "detailUrl", "detail_url", "canonicalUrl", "link",
)
CARD_TAGS = {"article", "li", "tr", "section"}
TITLE_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
CARD_HINT = re.compile(r"(?:job|role|position|opening|vacan)(?:[-_\s]*(?:card|item|result|listing|row))?", re.I)


@dataclass(frozen=True)
class ListingCandidate:
    title: str
    url: str
    source_url: str
    origin: str

    def as_raw_link(self) -> RawLink:
        return RawLink(self.url, self.title, self.source_url, self.origin)


@dataclass
class _Card:
    tag: str
    depth: int
    titles: list[str]
    text_blocks: list[str]
    links: list[tuple[str, str]]


class _ListingParser(HTMLParser):
    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.depth = 0
        self.nodes = 0
        self.cards: list[_Card] = []
        self.results: list[ListingCandidate] = []
        self.capture_tag: str | None = None
        self.capture_depth = 0
        self.capture_text: list[str] = []
        self.active_link: tuple[str, int, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.nodes += 1
        if self.nodes > MAX_HTML_NODES:
            return
        self.depth += 1
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        marker = " ".join((attrs_dict.get("class", ""), attrs_dict.get("id", ""), attrs_dict.get("data-testid", "")))
        if tag in CARD_TAGS or (tag == "div" and CARD_HINT.search(marker)):
            self.cards.append(_Card(tag, self.depth, [], [], []))
        if tag in TITLE_TAGS.union({"p"}) and self.cards:
            self.capture_tag, self.capture_depth, self.capture_text = tag, self.depth, []
        if tag == "a" and self.cards and attrs_dict.get("href"):
            self.active_link = (attrs_dict["href"], self.depth, [])

    def handle_data(self, data: str) -> None:
        if self.nodes > MAX_HTML_NODES:
            return
        if self.capture_tag:
            self.capture_text.append(data)
        if self.active_link:
            self.active_link[2].append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.nodes > MAX_HTML_NODES:
            return
        if self.capture_tag == tag and self.capture_depth == self.depth:
            title = " ".join("".join(self.capture_text).split())
            if title and self.cards:
                if tag in TITLE_TAGS:
                    self.cards[-1].titles.append(title)
                else:
                    self.cards[-1].text_blocks.append(title)
            self.capture_tag = None
        if tag == "a" and self.active_link and self.active_link[1] == self.depth:
            href, _, chunks = self.active_link
            text = " ".join("".join(chunks).split())
            if self.cards:
                self.cards[-1].links.append((href, text))
            self.active_link = None
        if self.cards and self.cards[-1].tag == tag and self.cards[-1].depth == self.depth:
            self._finish_card(self.cards.pop())
        self.depth = max(0, self.depth - 1)

    def _finish_card(self, card: _Card) -> None:
        if not card.links or len(self.results) >= MAX_CANDIDATES:
            return
        title = card.titles[0] if card.titles else (card.text_blocks[-1] if card.text_blocks else "")
        if not title:
            return
        for href, _ in card.links:
            url = validate_output_url(href, self.source_url)
            if url:
                self.results.append(ListingCandidate(title, url, self.source_url, "parent_card"))
                return


def validate_output_url(url: str, source_url: str) -> str | None:
    """Return a normalized URL only when it is a plausible official job detail."""
    normalized = safe_normalize_url(url, source_url)
    if not normalized:
        return None
    parsed = urlparse(normalized)
    source = urlparse(source_url)
    if parsed.username or parsed.password:
        return None
    try:
        if parsed.port not in {None, 80, 443}:
            return None
    except ValueError:
        return None
    link = RawLink(normalized, "", source_url)
    scored = score_job_link(link, source_url)
    if not is_likely_job_detail(scored):
        return None
    same_origin = parsed.hostname == source.hostname and parsed.port == source.port
    if same_origin or is_ats_url(normalized):
        return normalized
    return None


def extract_listing_candidates(html: str, source_url: str) -> list[ListingCandidate]:
    parser = _ListingParser(source_url)
    parser.feed(html)
    candidates = parser.results + list(_structured_candidates(html, source_url))
    seen: set[tuple[str, str]] = set()
    output: list[ListingCandidate] = []
    for candidate in candidates:
        key = (candidate.url.rstrip("/"), candidate.title.casefold().strip())
        if key not in seen:
            seen.add(key)
            output.append(candidate)
        if len(output) >= MAX_CANDIDATES:
            break
    return output


def _structured_candidates(html: str, source_url: str):
    count = 0
    for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.I | re.S):
        if count >= MAX_JSON_RECORDS or len(body) > MAX_SCRIPT_CHARS:
            continue
        text = unescape(body.strip())
        payloads = []
        if "json" in attrs.lower() or text.startswith(("{", "[")):
            payloads.append(text)
        # Common hydration forms: window.__STATE__ = {...}; and const jobs = [...];
        payloads.extend(_assigned_json_values(text))
        for payload in payloads:
            try:
                data = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                continue
            for record in _walk_records(data):
                count += 1
                title = _field(record, TITLE_FIELDS)
                raw_url = _field(record, URL_FIELDS)
                url = validate_output_url(raw_url, source_url) if raw_url else None
                if title and url:
                    yield ListingCandidate(title, url, source_url, "structured_state")
                if count >= MAX_JSON_RECORDS:
                    return


def _assigned_json_values(script: str) -> list[str]:
    decoder = json.JSONDecoder()
    values: list[str] = []
    for match in re.finditer(r"(?:\b(?:const|let|var)\s+)?[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*\s*=\s*(?=[\[{])", script):
        start = match.end()
        try:
            _value, end = decoder.raw_decode(script, start)
        except json.JSONDecodeError:
            continue
        values.append(script[start:end])
        if len(values) >= 100:
            break
    return values


def _walk_records(value, depth: int = 0, in_job_container: bool = False):
    if depth > MAX_JSON_DEPTH:
        return
    if isinstance(value, dict):
        item_type = value.get("@type")
        explicit_job_record = str(item_type).casefold() == "jobposting" or any(
            field in value for field in ("jobTitle", "job_title", "positionTitle")
        )
        if in_job_container or explicit_job_record:
            yield value
        for key, child in value.items():
            child_in_job_container = in_job_container or str(key).casefold() in {
                "jobs",
                "joblist",
                "job_list",
                "jobpostings",
                "job_postings",
                "openings",
                "positions",
                "roles",
                "results",
            }
            yield from _walk_records(child, depth + 1, child_in_job_container)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_records(child, depth + 1, in_job_container)


def _field(record: dict, fields: tuple[str, ...]) -> str:
    for field in fields:
        value = record.get(field)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""
