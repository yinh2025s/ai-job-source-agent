from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import urlencode, urlparse, urlunparse

from ..web import FetchError, safe_normalize_url
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOSTS = {"google.com", "www.google.com"}
_CAREERS_ROOT = "/about/careers/applications"
_RESULTS_PATH = f"{_CAREERS_ROOT}/jobs/results"
_DETAIL_PATH = re.compile(
    rf"^{re.escape(_RESULTS_PATH)}/(?P<job_id>[0-9]+)-[a-z0-9-]+/?$",
    re.IGNORECASE,
)


class GoogleCareersAdapter:
    name = "google_careers"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        parsed = _safe_google_url(url)
        if parsed is None:
            return False
        path = parsed.path.rstrip("/") or "/"
        return path == _CAREERS_ROOT or path == _RESULTS_PATH or bool(
            _DETAIL_PATH.fullmatch(path)
        )

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        return JobBoard(
            url=f"https://www.google.com{_RESULTS_PATH}/",
            provider=self.name,
            identifier="www.google.com",
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not _is_canonical_board(board):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "invalid Google Careers board"},
            )

        search_url = board.url
        if query.title:
            search_url = f"{board.url}?{urlencode({'q': query.title})}"
        try:
            page = fetcher.fetch(search_url)
        except (FetchError, OSError, TimeoutError) as error:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                trace={
                    "adapter": self.name,
                    "board_urls": [search_url],
                    "errors": [{"url": search_url, "error": str(error)}],
                },
            )

        final_url = page.final_url or page.url
        if not _is_search_page(final_url):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={
                    "adapter": self.name,
                    "board_urls": [search_url],
                    "rejected_response_url": final_url,
                },
            )

        parser = _GoogleJobsParser()
        parser.feed(page.html)
        candidates: list[JobCandidate] = []
        rejected_urls: list[str] = []
        seen: set[str] = set()
        for raw_url, raw_title in parser.links:
            detail_url = safe_normalize_url(raw_url, parser.base_url or final_url)
            canonical_url = _canonical_detail_url(detail_url)
            title = _clean_title(raw_title)
            if not canonical_url or not title:
                rejected_urls.append(str(raw_url))
                continue
            if canonical_url in seen:
                continue
            seen.add(canonical_url)
            candidates.append(
                JobCandidate(
                    title=title,
                    url=canonical_url,
                    provider=self.name,
                    raw={"job_id": _DETAIL_PATH.fullmatch(urlparse(canonical_url).path).group("job_id")},
                )
            )

        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            trace={
                "adapter": self.name,
                "variant": "server_rendered_search",
                "board_urls": [final_url],
                "response_source": page.source,
                "candidate_count": len(candidates),
                "rejected_urls": list(dict.fromkeys(rejected_urls)),
            },
        )


class _GoogleJobsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url: str | None = None
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._label = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "base" and not self.base_url:
            self.base_url = attributes.get("href") or None
        if tag.casefold() != "a":
            return
        href = attributes.get("href", "")
        if "jobs/results/" in href.casefold():
            self._href = href
            self._label = attributes.get("aria-label", "") or attributes.get("title", "")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self._href is None:
            return
        text = self._label or " ".join("".join(self._text).split())
        self.links.append((self._href, text))
        self._href = None
        self._label = ""
        self._text = []


def _safe_google_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or host not in _HOSTS
    ):
        return None
    return parsed


def _is_canonical_board(board: JobBoard) -> bool:
    parsed = _safe_google_url(board.url)
    return (
        board.provider == "google_careers"
        and board.identifier == "www.google.com"
        and parsed is not None
        and (parsed.hostname or "").casefold() == "www.google.com"
        and parsed.path.rstrip("/") == _RESULTS_PATH
    )


def _is_search_page(url: str) -> bool:
    parsed = _safe_google_url(url)
    return parsed is not None and parsed.path.rstrip("/") == _RESULTS_PATH


def _canonical_detail_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = _safe_google_url(url)
    if parsed is None or not _DETAIL_PATH.fullmatch(parsed.path.rstrip("/")):
        return None
    return urlunparse(("https", "www.google.com", parsed.path.rstrip("/"), "", "", ""))


def _clean_title(value: str) -> str:
    title = " ".join(value.split())
    prefix = "learn more about "
    if title.casefold().startswith(prefix):
        title = title[len(prefix):].strip()
    return title


ADAPTER = GoogleCareersAdapter()
