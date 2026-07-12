from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import quote, unquote, urlparse, urlunparse

from ..web import FetchError, safe_normalize_url
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST = "ats.rippling.com"
_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_LOCALE_PATTERN = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
_JOB_ID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class RipplingAdapter:
    name = "rippling"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _board_parts(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        parts = _board_parts(url)
        if parts is None:
            return None
        company, _job_id = parts
        encoded_company = quote(company, safe="-_")
        return JobBoard(
            url=f"https://{_HOST}/embed/{encoded_company}/jobs",
            provider=self.name,
            identifier=company,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        company = board.identifier
        if not company or not _SLUG_PATTERN.fullmatch(company):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "missing Rippling company identifier"},
            )

        board_url = f"https://{_HOST}/embed/{quote(company, safe='-_')}/jobs"
        try:
            page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                trace={
                    "adapter": self.name,
                    "board_urls": [board_url],
                    "error": str(error),
                },
            )

        parser = _JobLinkParser()
        try:
            parser.feed(page.html or "")
        except (TypeError, ValueError) as error:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="INVALID_STRUCTURED_DATA",
                trace={
                    "adapter": self.name,
                    "board_urls": [board_url],
                    "error": str(error),
                },
            )

        candidates: list[JobCandidate] = []
        seen_urls: set[str] = set()
        for link in parser.links:
            candidate = _candidate_from_link(link, company, board_url)
            if candidate is None or candidate.url in seen_urls:
                continue
            seen_urls.add(candidate.url)
            candidates.append(candidate)

        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            trace={
                "adapter": self.name,
                "board_urls": [board_url],
                "response_source": page.source,
                "link_count": len(parser.links),
                "candidate_count": len(candidates),
            },
        )


class _JobLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str | None]] = []
        self._active: dict[str, str | None] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._active is not None:
            return
        attributes = {key.lower(): value for key, value in attrs}
        href = attributes.get("href")
        if not href:
            return
        self._active = {
            "href": href,
            "title": attributes.get("data-job-title") or attributes.get("aria-label"),
            "location": attributes.get("data-job-location") or attributes.get("data-location"),
        }
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._active is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._active is None:
            return
        link = dict(self._active)
        visible_title = " ".join("".join(self._text).split())
        link["title"] = visible_title or _clean_text(link.get("title"))
        link["location"] = _clean_text(link.get("location"))
        self.links.append(link)
        self._active = None
        self._text = []


def _parsed_public_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if (parsed.hostname or "").lower() != _HOST or parsed.username or parsed.password:
        return None
    if port not in {None, 443}:
        return None
    return parsed


def _board_parts(url: str) -> tuple[str, str | None] | None:
    parsed = _parsed_public_url(url)
    if parsed is None:
        return None
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None

    if parts[0].casefold() == "embed":
        company_index = 1
    elif _LOCALE_PATTERN.fullmatch(parts[0]):
        company_index = 1
    else:
        company_index = 0

    if len(parts) <= company_index + 1 or parts[company_index + 1].casefold() != "jobs":
        return None
    company = parts[company_index]
    if not _SLUG_PATTERN.fullmatch(company):
        return None

    tail = parts[company_index + 2 :]
    if not tail:
        return company, None
    if len(tail) == 1 and _JOB_ID_PATTERN.fullmatch(tail[0]):
        return company, tail[0]
    return None


def _candidate_from_link(
    link: dict[str, str | None],
    company: str,
    board_url: str,
) -> JobCandidate | None:
    title = _clean_text(link.get("title"))
    href = link.get("href")
    if not title or not href:
        return None
    normalized = safe_normalize_url(href, board_url)
    parts = _board_parts(normalized or "")
    if parts is None or parts[0].casefold() != company.casefold() or parts[1] is None:
        return None
    parsed = urlparse(normalized)
    detail_url = urlunparse(("https", _HOST, parsed.path.rstrip("/"), "", "", ""))
    return JobCandidate(
        title=title,
        url=detail_url,
        provider="rippling",
        location=_clean_text(link.get("location")),
        raw={"job_id": parts[1]},
    )


def _clean_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


ADAPTER = RipplingAdapter()
