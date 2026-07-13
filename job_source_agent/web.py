from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from html import unescape
from pathlib import Path
import gzip
import hashlib
import re
import signal
import socket
import threading
import time
from contextlib import contextmanager
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPCookieProcessor, Request, build_opener


TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}

_FIXTURE_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "auth",
    "authorization",
    "code",
    "id_token",
    "key",
    "password",
    "refresh_token",
    "secret",
    "session",
    "sig",
    "signature",
    "state",
    "token",
}

MAX_EXTRACTED_LINKS = 200
MAX_EMBEDDED_SCAN_CHARS = 1_000_000
_URL_DATA_ATTRIBUTES = {
    "data-apply-url",
    "data-careers-url",
    "data-href",
    "data-job-board-url",
    "data-jobs-url",
    "data-src",
    "data-url",
}


@dataclass
class RawLink:
    url: str
    text: str
    source_url: str
    origin: str = "page_link"


@dataclass
class Page:
    url: str
    html: str
    final_url: str | None = None
    source: str = "live"
    artifacts: dict[str, bytes] = field(default_factory=dict)


class FetchError(RuntimeError):
    pass


class TimeBudgetExceeded(RuntimeError):
    """Raised when a caller-level deadline expires."""


@contextmanager
def hard_timeout(seconds: float, timeout_exception: type[Exception] = TimeoutError):
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        yield
        return

    if seconds <= 0:
        raise timeout_exception(f"operation timed out after {seconds} seconds")

    def _handle_timeout(_signum, _frame):
        if outer_timer_wins and callable(old_handler):
            old_handler(_signum, _frame)
        raise timeout_exception(f"operation timed out after {seconds} seconds")

    old_handler = signal.signal(signal.SIGALRM, _handle_timeout)
    old_delay, old_interval = signal.getitimer(signal.ITIMER_REAL)
    outer_timer_wins = old_delay > 0 and old_delay <= seconds
    effective_seconds = min(seconds, old_delay) if old_delay > 0 else seconds
    started = time.monotonic()
    signal.setitimer(signal.ITIMER_REAL, effective_seconds)
    try:
        yield
    finally:
        elapsed = time.monotonic() - started
        remaining_delay = max(0.0, old_delay - elapsed) if old_delay > 0 else 0.0
        signal.setitimer(signal.ITIMER_REAL, remaining_delay, old_interval)
        signal.signal(signal.SIGALRM, old_handler)


def normalize_url(url: str, base_url: str | None = None) -> str:
    url = unescape(url.strip())
    if base_url:
        url = urljoin(base_url, url)
    parsed = urlparse(url)
    if not parsed.scheme:
        parsed = urlparse("https://" + url)
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parsed.query) if key not in TRACKING_PARAMS],
        doseq=True,
    )
    normalized = parsed._replace(fragment="", query=query)
    return urlunparse(normalized)


def safe_normalize_url(url: str, base_url: str | None = None) -> str | None:
    try:
        normalized = normalize_url(url, base_url)
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        return normalized
    except (TypeError, ValueError):
        return None


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def path_depth(url: str) -> int:
    return len([part for part in urlparse(url).path.split("/") if part])


class _LinkParser(HTMLParser):
    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.links: list[RawLink] = []
        self.base_url = source_url
        self._active_href: str | None = None
        self._active_text: list[str] = []

    def _append_attribute_link(self, value: str, origin: str) -> None:
        if len(self.links) >= MAX_EXTRACTED_LINKS:
            return
        normalized = safe_normalize_url(value, self.base_url)
        normalized = _canonical_navigation_url(normalized) if normalized else None
        if normalized:
            self.links.append(RawLink(normalized, "", self.source_url, origin))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        attrs_dict = {key.lower(): value for key, value in attrs}
        if tag_name == "base" and attrs_dict.get("href"):
            normalized_base = safe_normalize_url(attrs_dict["href"] or "", self.source_url)
            if normalized_base:
                self.base_url = normalized_base
            return
        if tag_name == "iframe" and attrs_dict.get("src"):
            self._append_attribute_link(attrs_dict["src"] or "", "iframe_src")
        if tag_name == "form" and attrs_dict.get("action"):
            self._append_attribute_link(attrs_dict["action"] or "", "form_action")
        for name, value in attrs_dict.items():
            if value and (name in _URL_DATA_ATTRIBUTES or (name.startswith("data-") and name.endswith("-url"))):
                self._append_attribute_link(value, "data_attribute")
        if tag_name != "a":
            return
        href = attrs_dict.get("href")
        if href and not href.startswith(("mailto:", "tel:", "javascript:")):
            self._active_href = href
            self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href is not None:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._active_href is None:
            return
        text = " ".join("".join(self._active_text).split())
        normalized_href = safe_normalize_url(self._active_href, self.base_url)
        if normalized_href and len(self.links) < MAX_EXTRACTED_LINKS:
            self.links.append(
                RawLink(
                    url=normalized_href,
                    text=text,
                    source_url=self.source_url,
                    origin="page_link",
                )
            )
        self._active_href = None
        self._active_text = []


def extract_links(page: Page) -> list[RawLink]:
    parser = _LinkParser(page.final_url or page.url)
    parser.feed(page.html)
    links = parser.links
    source_url = page.final_url or page.url
    if (
        len(links) < MAX_EXTRACTED_LINKS
        and page.final_url
        and normalize_url(page.final_url) != normalize_url(page.url)
    ):
        links.append(RawLink(normalize_url(page.final_url), "", page.url, "redirect_final_url"))

    # Script and JSON payloads commonly slash- or unicode-escape ATS URLs.
    embedded = page.html[:MAX_EMBEDDED_SCAN_CHARS]
    embedded = re.sub(r"\\u00(?:2f|2F)", "/", embedded)
    embedded = embedded.replace(r"\/", "/")
    embedded = unescape(embedded)
    configured_board_urls = (
        _greenhouse_template_board_urls(embedded)
        + _lever_embed_board_urls(embedded)
    )
    provider_config_links = [
        RawLink(
            url=board_url,
            text="",
            source_url=source_url,
            origin="derived_provider_config",
        )
        for board_url in dict.fromkeys(configured_board_urls)
    ]
    if provider_config_links:
        configured_urls = {link.url for link in provider_config_links}
        links = provider_config_links + [link for link in links if link.url not in configured_urls]
        links = links[:MAX_EXTRACTED_LINKS]
    for url in re.findall(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", embedded):
        if len(links) >= MAX_EXTRACTED_LINKS:
            break
        normalized_url = safe_normalize_url(url.rstrip("'\"),.;"))
        normalized_url = _canonical_navigation_url(normalized_url) if normalized_url else None
        if not normalized_url:
            continue
        if any(existing.url == normalized_url for existing in links):
            continue
        links.append(
            RawLink(
                url=normalized_url,
                text="",
                source_url=source_url,
                origin="embedded_url",
            )
        )
    return links


def _canonical_navigation_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if host == "boards.greenhouse.io" and parsed.path.rstrip("/") == "/embed/job_board/js":
        identifier = (parse_qs(parsed.query).get("for") or [""])[0]
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", identifier):
            return f"https://job-boards.greenhouse.io/{identifier}"
    return url


def _greenhouse_template_board_urls(text: str) -> list[str]:
    variable_names = set(
        re.findall(
            r"boards-api\.greenhouse\.io/v1/boards/(?:\$\{\s*([A-Za-z_$][\w$]*)\s*\}|[\"']\s*\+\s*([A-Za-z_$][\w$]*))",
            text,
        )
    )
    flattened_names = {name for pair in variable_names for name in pair if name}
    if not flattened_names:
        return []
    assignments = {
        name: value
        for name, quote, value in re.findall(
            r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*([\"'])([A-Za-z0-9_-]+)\2",
            text,
        )
        if name in flattened_names
    }
    return [
        f"https://job-boards.greenhouse.io/{assignments[name]}"
        for name in sorted(flattened_names)
        if name in assignments
    ]


def _lever_embed_board_urls(text: str) -> list[str]:
    if not re.search(r"(?:lever-jobs-embed|\blever-job(?:s|-)\b)", text, re.I):
        return []
    identifiers = re.findall(
        r"\bleverJobsOptions\s*=\s*\{[^{}]{0,2000}?[\"']?accountName[\"']?\s*:\s*"
        r"([\"'])([A-Za-z0-9][A-Za-z0-9_-]{0,99})\1",
        text,
        re.I | re.S,
    )
    return [f"https://jobs.lever.co/{identifier}" for _quote, identifier in identifiers]


class Fetcher:
    def __init__(
        self,
        fixtures_dir: str | Path | None = None,
        offline: bool = False,
        timeout: float = 12,
    ) -> None:
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else None
        self.offline = offline
        self.timeout = timeout
        self._http_sessions = threading.local()

    def fetch(self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Page:
        normalized = normalize_url(url)
        fixture_path = self._fixture_path_for(normalized)
        if fixture_path and fixture_path.exists():
            return Page(
                url=normalized,
                html=fixture_path.read_text(encoding="utf-8"),
                final_url=normalized,
                source=str(fixture_path),
            )
        if self.offline:
            raise FetchError(f"No fixture found for {normalized}")
        return self._fetch_live(normalized, data=data, headers=headers)

    def _fetch_live(self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Page:
        socket.setdefaulttimeout(self.timeout)
        request_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if data is not None:
            request_headers["Content-Type"] = "application/json"
            request_headers["Accept"] = "application/json,text/plain,*/*"
        if headers:
            request_headers.update(headers)
        request = Request(
            url,
            data=data,
            headers=request_headers,
        )
        try:
            with hard_timeout(self.timeout + 1):
                with self._thread_opener().open(request, timeout=self.timeout) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    raw = response.read()
                    if response.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    html = raw.decode(charset, errors="replace")
                    final_url = response.geturl()
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise FetchError(str(exc)) from exc
        return Page(url=url, html=html, final_url=final_url, source="live")

    def _thread_opener(self):
        opener = getattr(self._http_sessions, "opener", None)
        if opener is None:
            opener = build_opener(HTTPCookieProcessor(CookieJar()))
            self._http_sessions.opener = opener
        return opener

    def _fixture_path_for(self, url: str) -> Path | None:
        if not self.fixtures_dir:
            return None
        candidates = fixture_path_candidates(self.fixtures_dir, url)
        if len(candidates) > 1:
            query_path, legacy = candidates
            if query_path.exists():
                return query_path
            query_variants = list(legacy.parent.glob(f"{legacy.stem}.__query_*{legacy.suffix}"))
            if query_variants:
                return query_path
            if legacy.exists():
                return legacy
        elif candidates[0].exists():
            return candidates[0]
        legacy = candidates[-1]
        if len(candidates) == 1 and legacy.suffix:
            query_variants = sorted(legacy.parent.glob(f"{legacy.stem}.__query_*{legacy.suffix}"))
            if len(query_variants) == 1 and query_variants[0].is_file():
                return query_variants[0]
        if legacy.name == "index.html":
            alternate = legacy.parent.with_suffix(".html")
            if alternate.exists():
                return alternate
        return candidates[0]


def fixture_path_candidates(fixtures_dir: str | Path, url: str) -> list[Path]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [part for part in parsed.path.split("/") if part]
    base = Path(fixtures_dir) / host
    if not parts:
        legacy = base / "index.html"
    else:
        candidate = base.joinpath(*[_safe_fixture_path_part(part) for part in parts])
        legacy = candidate if candidate.suffix else candidate / "index.html"
    if not parsed.query:
        return [legacy]
    query = urlencode(
        sorted(
            (
                key,
                "[REDACTED]" if _is_fixture_sensitive_query_key(key) else value,
            )
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ),
        doseq=True,
    )
    fingerprint = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    query_path = legacy.with_name(f"{legacy.stem}.__query_{fingerprint}{legacy.suffix}")
    return [query_path, legacy]


def _is_fixture_sensitive_query_key(key: str) -> bool:
    lowered = key.casefold()
    return lowered in _FIXTURE_SENSITIVE_QUERY_KEYS or any(
        marker in lowered for marker in ("token", "secret", "password")
    )


def _safe_fixture_path_part(part: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", part)
    return cleaned or "_"
