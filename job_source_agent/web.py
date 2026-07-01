from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import gzip
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}


@dataclass
class RawLink:
    url: str
    text: str
    source_url: str


@dataclass
class Page:
    url: str
    html: str
    final_url: str | None = None
    source: str = "live"


class FetchError(RuntimeError):
    pass


def normalize_url(url: str, base_url: str | None = None) -> str:
    url = url.strip()
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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        attrs_dict = {key.lower(): value for key, value in attrs}
        if tag_name == "base" and attrs_dict.get("href"):
            normalized_base = safe_normalize_url(attrs_dict["href"] or "", self.source_url)
            if normalized_base:
                self.base_url = normalized_base
            return
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
        if normalized_href:
            self.links.append(
                RawLink(
                    url=normalized_href,
                    text=text,
                    source_url=self.source_url,
                )
            )
        self._active_href = None
        self._active_text = []


def extract_links(page: Page) -> list[RawLink]:
    parser = _LinkParser(page.final_url or page.url)
    parser.feed(page.html)
    links = parser.links
    # Some modern sites store navigation targets in data attributes or escaped
    # JSON blobs. This conservative pass catches obvious absolute URLs.
    for url in re.findall(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", page.html):
        normalized_url = safe_normalize_url(url)
        if not normalized_url:
            continue
        if any(existing.url == normalized_url for existing in links):
            continue
        links.append(RawLink(url=normalized_url, text="", source_url=page.final_url or page.url))
    return links


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

    def fetch(self, url: str) -> Page:
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
        return self._fetch_live(normalized)

    def _fetch_live(self, url: str) -> Page:
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "gzip",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                html = raw.decode(charset, errors="replace")
                final_url = response.geturl()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise FetchError(str(exc)) from exc
        return Page(url=url, html=html, final_url=final_url, source="live")

    def _fixture_path_for(self, url: str) -> Path | None:
        if not self.fixtures_dir:
            return None
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        parts = [part for part in parsed.path.split("/") if part]
        base = self.fixtures_dir / host
        if not parts:
            return base / "index.html"
        candidate = base.joinpath(*parts)
        if candidate.suffix:
            return candidate
        index_candidate = candidate / "index.html"
        if index_candidate.exists():
            return index_candidate
        return candidate.with_suffix(".html")
