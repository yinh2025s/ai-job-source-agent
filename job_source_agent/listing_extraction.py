from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlparse

from .card_listing_extraction import extract_card_listing_candidates
from .scoring import is_ats_url, is_likely_job_detail, score_job_link
from .web import RawLink, safe_normalize_url


MAX_HTML_NODES = 20_000
MAX_SCRIPT_CHARS = 2_000_000
MAX_JSON_DEPTH = 24
MAX_JSON_RECORDS = 10_000
MAX_CANDIDATES = 1_000
MAX_VISIBLE_TEXT_CHARS = 500_000

TITLE_FIELDS = ("title", "name", "jobTitle", "job_title", "positionTitle")
URL_FIELDS = (
    "url", "absolute_url", "absoluteUrl", "hostedUrl", "applyUrl", "jobUrl",
    "job_url", "externalPath", "externalUrl", "detailUrl", "detail_url", "canonicalUrl", "link",
)
EXPLICIT_EMPTY_INVENTORY = re.compile(
    r"\b(?:"
    r"no open (?:jobs?|roles?|positions?|openings?)(?: are)? available "
    r"(?:at the moment|right now|currently)"
    r"|there are (?:currently )?no open (?:jobs?|roles?|positions?|openings?)"
    r"|we (?:currently )?(?:have no|do not have|don't have) open "
    r"(?:jobs?|roles?|positions?|openings?)"
    r")\b",
    re.I,
)


@dataclass(frozen=True)
class ListingCandidate:
    title: str
    url: str
    source_url: str
    origin: str

    def as_raw_link(self) -> RawLink:
        return RawLink(self.url, self.title, self.source_url, self.origin)


class _VisibleTextParser(HTMLParser):
    _IGNORED_TAGS = {"script", "style", "template", "noscript"}
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

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ignored_depth = 0
        self.hidden_depth = 0
        self.parts: list[str] = []
        self.character_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in self._IGNORED_TAGS:
            self.ignored_depth += 1
        values = {name.casefold(): (value or "") for name, value in attrs}
        if (
            normalized_tag not in self._VOID_TAGS
            and (
                self.hidden_depth
                or "hidden" in values
                or values.get("aria-hidden", "").casefold() == "true"
            )
        ):
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in self._IGNORED_TAGS and self.ignored_depth:
            self.ignored_depth -= 1
        if self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.ignored_depth or self.hidden_depth or self.character_count >= MAX_VISIBLE_TEXT_CHARS:
            return
        remaining = MAX_VISIBLE_TEXT_CHARS - self.character_count
        value = data[:remaining]
        self.parts.append(value)
        self.character_count += len(value)


class _ScriptBodyParser(HTMLParser):
    """Collect script bodies while enforcing one bounded document-wide budget."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.scripts: list[tuple[str, str]] = []
        self._attrs = ""
        self._parts: list[str] | None = None
        self._script_chars = 0
        self._exhausted = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script" or self._parts is not None:
            return
        self._attrs = " ".join(f'{key}="{value or ""}"' for key, value in attrs)
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._parts is None or self._exhausted:
            return
        self._script_chars += len(data)
        if self._script_chars > MAX_SCRIPT_CHARS:
            self._parts.clear()
            self._exhausted = True
            return
        self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script" or self._parts is None:
            return
        if not self._exhausted and len(self.scripts) < MAX_JSON_RECORDS:
            self.scripts.append((self._attrs, "".join(self._parts)))
        self._attrs = ""
        self._parts = None


def explicit_empty_inventory_evidence(html: str) -> str | None:
    """Return bounded visible first-party evidence that public inventory is empty."""

    if not isinstance(html, str):
        return None
    parser = _VisibleTextParser()
    try:
        parser.feed(html[:2_000_000])
        parser.close()
    except (TypeError, ValueError):
        return None
    text = " ".join(" ".join(parser.parts).split())
    match = EXPLICIT_EMPTY_INVENTORY.search(text)
    return match.group(0) if match else None


def validate_output_url(url: str, source_url: str, *, title: str = "") -> str | None:
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
    link = RawLink(normalized, title, source_url)
    scored = score_job_link(link, source_url)
    if not is_likely_job_detail(scored) and not _title_bound_same_origin_detail(
        parsed,
        source,
        title,
    ):
        return None
    same_origin = parsed.hostname == source.hostname and parsed.port == source.port
    if same_origin or is_ats_url(normalized):
        return normalized
    return None


def _title_bound_same_origin_detail(parsed, source, title: str) -> bool:
    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname != source.hostname
        or parsed.port != source.port
    ):
        return False
    path_words = {
        word
        for word in re.findall(r"[a-z0-9]+", parsed.path.casefold())
        if word not in {"career", "careers", "job", "jobs", "role", "roles"}
        and len(word) > 1
    }
    title_words = {
        word
        for word in re.findall(r"[a-z0-9]+", title.casefold())
        if word not in {"a", "an", "and", "at", "for", "in", "of", "on", "the", "to"}
        and len(word) > 1
    }
    path_parts = [part for part in parsed.path.split("/") if part]
    return (
        len(path_parts) >= 2
        and bool({"career", "careers", "job", "jobs", "role", "roles"} & {
            word for word in re.findall(r"[a-z0-9]+", parsed.path.casefold())
        })
        and bool(path_words)
        and bool(path_words & title_words)
    )


def extract_listing_candidates(html: str, source_url: str) -> list[ListingCandidate]:
    strict_card_candidates = [
        ListingCandidate(
            candidate.title,
            candidate.url,
            candidate.source_url,
            candidate.origin,
        )
        for candidate in extract_card_listing_candidates(html, source_url)
    ]
    candidates = (
        strict_card_candidates
        + list(_structured_candidates(html, source_url))
    )
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
    candidate_count = 0
    for data in _structured_values(html):
        for record in _walk_records(data):
            count += 1
            title = _field(record, TITLE_FIELDS)
            raw_url = _field(record, URL_FIELDS)
            url = validate_output_url(raw_url, source_url, title=title) if raw_url else None
            if title and url:
                yield ListingCandidate(title, url, source_url, "structured_state")
                candidate_count += 1
                if candidate_count >= MAX_CANDIDATES:
                    return
            if count >= MAX_JSON_RECORDS:
                return


def _structured_values(html: str):
    parser = _ScriptBodyParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return

    flight_chunks: list[str] = []
    for attrs, body in parser.scripts:
        text = unescape(body.strip())
        payloads = []
        if "json" in attrs.lower() or text.startswith(("{", "[")):
            payloads.append(text)
        # Common hydration forms: window.__STATE__ = {...}; and const jobs = [...];
        payloads.extend(_assigned_json_values(text))
        for payload in payloads:
            try:
                yield json.loads(payload)
            except (json.JSONDecodeError, RecursionError, TypeError):
                continue
        flight_chunks.extend(_next_f_string_chunks(body))

    flight_data = "".join(flight_chunks)
    if len(flight_data) > MAX_SCRIPT_CHARS:
        return
    yield from _flight_json_values(flight_data)


def _next_f_string_chunks(script: str) -> list[str]:
    decoder = json.JSONDecoder()
    chunks: list[str] = []
    pattern = re.compile(r"\bself\s*\.\s*__next_f\s*\.\s*push\s*\(\s*")
    for match in pattern.finditer(script):
        try:
            argument, end = decoder.raw_decode(script, match.end())
        except (json.JSONDecodeError, RecursionError):
            continue
        closing = end
        while closing < len(script) and script[closing].isspace():
            closing += 1
        if closing >= len(script) or script[closing] != ")" or not isinstance(argument, list):
            continue
        for value in argument:
            if isinstance(value, str):
                chunks.append(value)
        if len(chunks) >= MAX_JSON_RECORDS:
            break
    return chunks


def _flight_json_values(data: str):
    decoder = json.JSONDecoder()
    for line in data.splitlines():
        frame_id, separator, payload = line.partition(":")
        if (
            not separator
            or not 1 <= len(frame_id) <= 32
            or not frame_id.isascii()
            or not frame_id.isalnum()
        ):
            continue
        try:
            value, end = decoder.raw_decode(payload)
        except (json.JSONDecodeError, RecursionError):
            continue
        if payload[end:].strip():
            continue
        yield value


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
                "departments",
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
