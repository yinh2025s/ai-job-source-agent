from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import ipaddress
import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urljoin, urlparse, urlunparse

from .request_identity import is_sensitive_key
from .web import Page


_MAX_HTML_CHARS = 1_000_000
_MAX_ELEMENTS = 25_000
_MAX_DEPTH = 80
_MAX_ANCHORS = 200
_MAX_VISIBLE_CHARS = 300_000
_MAX_CONTAINER_CHARS = 1_200
_MAX_CONTAINER_ANCESTORS = 5
_MAX_URL_BYTES = 2_048
_IGNORED_TAGS = {"script", "style", "template", "noscript"}
_CONTAINER_TAGS = {"article", "aside", "div", "li", "section"}
_BLOCK_TAGS = _CONTAINER_TAGS | {"br", "h1", "h2", "h3", "h4", "h5", "h6", "p"}
_REDIRECT_QUERY_KEYS = {
    "continue",
    "dest",
    "destination",
    "next",
    "redirect",
    "redirectto",
    "redirecturi",
    "redirecturl",
    "return",
    "returnto",
    "returnurl",
    "target",
    "url",
}
_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$",
    re.IGNORECASE,
)
_SEARCH_ALL_JOBS = re.compile(r"search\s+all\s+jobs", re.IGNORECASE)
_NOW_A_COMPANY = re.compile(
    r"^(?P<source>.{1,100}?)\s+is\s+now\s+an?\s+"
    r"(?P<parent>.{1,100}?)\s+company$",
    re.IGNORECASE,
)
_ACQUIRED_BY = re.compile(
    r"^(?P<source>.{1,100}?)\s+was\s+acquired\s+by\s+"
    r"(?P<parent>.{1,100}?)$",
    re.IGNORECASE,
)
_DISALLOWED_CONTEXT = re.compile(
    r"(?:^|[^a-z])(blog|news|press|partner|partnership|powered)(?:[^a-z]|$)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:\b(?:bearer|basic)\s+[A-Za-z0-9+/=_-]{8,}|"
    r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AcquiredBrandPortalEvidence:
    """Visible authorization to probe one acquired brand's parent job portal."""

    source_brand: str
    parent_brand: str
    target_url: str
    evidence_url: str


@dataclass
class _Container:
    tag: str
    context: str
    parts: list[str] = field(default_factory=list)
    char_count: int = 0
    overflowed: bool = False

    def append(self, value: str) -> None:
        if self.overflowed or not value:
            return
        remaining = _MAX_CONTAINER_CHARS + 1 - self.char_count
        if remaining <= 0:
            self.overflowed = True
            return
        piece = value[:remaining]
        self.parts.append(piece)
        self.char_count += len(piece)
        if self.char_count > _MAX_CONTAINER_CHARS:
            self.overflowed = True

    def text(self) -> str:
        return "".join(self.parts)


@dataclass
class _Frame:
    tag: str
    hidden: bool
    ignored: bool
    container: _Container | None


@dataclass
class _Anchor:
    href: str
    containers: tuple[_Container, ...]
    parts: list[str] = field(default_factory=list)


class _PortalParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[_Frame] = []
        self.anchors: list[_Anchor] = []
        self.active_anchor: _Anchor | None = None
        self.elements = 0
        self.visible_chars = 0
        self.exhausted = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.exhausted:
            return
        self.elements += 1
        if self.elements > _MAX_ELEMENTS or len(self.stack) >= _MAX_DEPTH:
            self.exhausted = True
            return
        name = tag.casefold()
        attributes = {key.casefold(): value for key, value in attrs}
        parent_hidden = self.stack[-1].hidden if self.stack else False
        parent_ignored = self.stack[-1].ignored if self.stack else False
        hidden = parent_hidden or _element_is_hidden(attributes)
        ignored = parent_ignored or name in _IGNORED_TAGS
        container = None
        if not hidden and not ignored and name in _CONTAINER_TAGS:
            own_context = " ".join(
                value or "" for key, value in attributes.items() if key in {"class", "id", "role"}
            )
            inherited_context = " ".join(
                frame.container.context
                for frame in self.stack
                if frame.container is not None and frame.container.context
            )
            context = " ".join(part for part in (inherited_context, own_context) if part)
            container = _Container(tag=name, context=context)
        self.stack.append(_Frame(name, hidden, ignored, container))
        if not hidden and not ignored and name in _BLOCK_TAGS:
            self._append_visible("\n")
        if (
            name == "a"
            and not hidden
            and not ignored
            and self.active_anchor is None
            and len(self.anchors) < _MAX_ANCHORS
        ):
            href = attributes.get("href") or ""
            if href and "disabled" not in attributes and "inert" not in attributes:
                containers = tuple(
                    frame.container
                    for frame in reversed(self.stack[:-1])
                    if frame.container is not None
                )[:_MAX_CONTAINER_ANCESTORS]
                self.active_anchor = _Anchor(href=href, containers=containers)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.exhausted:
            return
        name = tag.casefold()
        if self.active_anchor is not None and name == "a":
            self.anchors.append(self.active_anchor)
            self.active_anchor = None
        matching = next(
            (index for index in range(len(self.stack) - 1, -1, -1) if self.stack[index].tag == name),
            None,
        )
        if matching is None:
            return
        if not self.stack[matching].hidden and not self.stack[matching].ignored and name in _BLOCK_TAGS:
            self._append_visible("\n")
        del self.stack[matching:]

    def handle_data(self, data: str) -> None:
        if self.exhausted or not data or not self.stack:
            return
        if self.stack[-1].hidden or self.stack[-1].ignored:
            return
        self.visible_chars += len(data)
        if self.visible_chars > _MAX_VISIBLE_CHARS:
            self.exhausted = True
            return
        self._append_visible(data)
        if self.active_anchor is not None:
            self.active_anchor.parts.append(data)

    def _append_visible(self, value: str) -> None:
        for frame in self.stack:
            if frame.container is not None:
                frame.container.append(value)


def parse_acquired_brand_portal_evidence(
    page: Page,
    expected_source_brand: str,
) -> AcquiredBrandPortalEvidence | None:
    """Return one unambiguous visible acquired-brand portal handoff, if present."""

    if not isinstance(page, Page) or not isinstance(page.html, str):
        return None
    expected_key = _brand_key(expected_source_brand)
    evidence_url = _safe_portal_url(page.final_url or page.url, base_url=None)
    if not expected_key or evidence_url is None or len(page.html) > _MAX_HTML_CHARS:
        return None

    parser = _PortalParser()
    try:
        parser.feed(page.html)
        parser.close()
    except (UnicodeError, ValueError):
        return None
    if parser.exhausted:
        return None

    candidates: dict[tuple[str, str], AcquiredBrandPortalEvidence] = {}
    for anchor in parser.anchors:
        label = " ".join("".join(anchor.parts).split())
        if not _SEARCH_ALL_JOBS.fullmatch(label):
            continue
        target_url = _safe_portal_url(anchor.href, base_url=evidence_url)
        if target_url is None:
            continue
        for container in anchor.containers:
            if container.overflowed or _DISALLOWED_CONTEXT.search(container.context):
                continue
            relationship = _relationship_in_text(container.text(), expected_key)
            if relationship is None:
                continue
            source_brand, parent_brand = relationship
            candidate = AcquiredBrandPortalEvidence(
                source_brand=source_brand,
                parent_brand=parent_brand,
                target_url=target_url,
                evidence_url=evidence_url,
            )
            candidates[(_brand_key(parent_brand), target_url)] = candidate
            break

    if len(candidates) != 1:
        return None
    return next(iter(candidates.values()))


def _relationship_in_text(text: str, expected_key: str) -> tuple[str, str] | None:
    relationships: dict[str, tuple[str, str]] = {}
    for segment in re.split(r"[\n.!?]+", text):
        normalized = " ".join(segment.split()).strip(" \t:;-–—")
        if not normalized or _DISALLOWED_CONTEXT.search(normalized):
            continue
        match = _NOW_A_COMPANY.fullmatch(normalized) or _ACQUIRED_BY.fullmatch(normalized)
        if match is None:
            continue
        source = match.group("source").strip(" \t,;:-")
        parent = match.group("parent").strip(" \t,;:-")
        parent_key = _brand_key(parent)
        if (
            _brand_key(source) != expected_key
            or not parent_key
            or parent_key == expected_key
            or len(source) > 100
            or len(parent) > 100
        ):
            continue
        relationships[parent_key] = (source, parent)
    if len(relationships) != 1:
        return None
    return next(iter(relationships.values()))


def _brand_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("&", "and")
    return "".join(character for character in normalized if character.isalnum())


def _element_is_hidden(attributes: dict[str, str | None]) -> bool:
    if "hidden" in attributes or "inert" in attributes:
        return True
    if (attributes.get("aria-hidden") or "").strip().casefold() == "true":
        return True
    style = re.sub(r"\s+", "", (attributes.get("style") or "").casefold())
    return "display:none" in style or "visibility:hidden" in style


def _safe_portal_url(value: str, *, base_url: str | None) -> str | None:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > _MAX_URL_BYTES:
        return None
    if _has_controls(value) or _SECRET_VALUE.search(value):
        return None
    try:
        resolved = urljoin(base_url, value) if base_url else value
        parsed = urlparse(resolved)
        port = parsed.port
        decoded_path = unquote(parsed.path)
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except (TypeError, ValueError, UnicodeError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not _is_public_host(parsed.hostname)
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or bool(parsed.fragment)
        or _has_controls(decoded_path)
    ):
        return None
    for key, item in query:
        canonical_key = re.sub(r"[^a-z0-9]+", "", key.casefold())
        if (
            is_sensitive_key(key)
            or canonical_key in _REDIRECT_QUERY_KEYS
            or _has_controls(unquote(key))
            or _has_controls(unquote(item))
            or _SECRET_VALUE.search(item)
        ):
            return None
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    serialized_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = serialized_host if port is None else f"{serialized_host}:443"
    return urlunparse(parsed._replace(scheme="https", netloc=netloc))


def _is_public_host(value: str | None) -> bool:
    host = (value or "").casefold().rstrip(".")
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    if (
        not host
        or len(host) > 253
        or not _HOSTNAME.fullmatch(host)
        or host == "localhost"
        or host.endswith(
            (".localhost", ".local", ".internal", ".lan", ".home", ".home.arpa", ".private")
        )
    ):
        return False
    return "." in host


def _has_controls(value: str) -> bool:
    return any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
