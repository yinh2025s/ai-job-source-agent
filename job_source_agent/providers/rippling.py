from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from urllib.parse import quote, unquote, urlparse, urlunparse

from ..web import FetchError, safe_normalize_url
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST = "ats.rippling.com"
_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_LOCALE_PATTERN = re.compile(r"^[a-z]{2}(?:-(?:[A-Z]{2}|[0-9]{3}))?$")
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

        final_parts = _board_parts(page.final_url)
        if final_parts is None or final_parts[0].casefold() != company.casefold():
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={
                    "adapter": self.name,
                    "board_urls": [board_url],
                    "final_url": page.final_url,
                    "error": "Rippling board redirected outside the expected tenant",
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

        structured_records, structured_state, structured_error = _structured_job_records(
            parser.next_data,
        )
        candidates: list[JobCandidate] = []
        seen_urls: set[str] = set()
        for record in structured_records:
            candidate = _candidate_from_record(record, company, board_url)
            if candidate is None or candidate.url in seen_urls:
                continue
            seen_urls.add(candidate.url)
            candidates.append(candidate)
        for link in parser.links:
            candidate = _candidate_from_link(link, company, board_url)
            if candidate is None or candidate.url in seen_urls:
                continue
            seen_urls.add(candidate.url)
            candidates.append(candidate)

        if candidates:
            reason_code = None
        elif structured_state == "empty":
            reason_code = "EMPTY_PROVIDER_RESPONSE"
        elif structured_state == "invalid":
            reason_code = "INVALID_STRUCTURED_DATA"
        else:
            reason_code = "PROVIDER_VARIANT_UNSUPPORTED"

        trace = {
            "adapter": self.name,
            "board_urls": [board_url],
            "response_source": page.source,
            "link_count": len(parser.links),
            "structured_state": structured_state,
            "structured_record_count": len(structured_records),
            "candidate_count": len(candidates),
        }
        if structured_error:
            trace["structured_error"] = structured_error

        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=reason_code,
            trace=trace,
        )


class _JobLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str | None]] = []
        self._active: dict[str, str | None] | None = None
        self._text: list[str] = []
        self._in_next_data = False
        self._next_data_parts: list[str] = []

    @property
    def next_data(self) -> str | None:
        if not self._next_data_parts:
            return None
        return "".join(self._next_data_parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value for key, value in attrs}
        if tag.lower() == "script" and attributes.get("id") == "__NEXT_DATA__":
            self._in_next_data = True
            return
        if tag.lower() != "a" or self._active is not None:
            return
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
        if self._in_next_data:
            self._next_data_parts.append(data)
        if self._active is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_next_data:
            self._in_next_data = False
            return
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


def _structured_job_records(
    raw_next_data: str | None,
) -> tuple[list[dict], str, str | None]:
    if raw_next_data is None:
        return [], "missing", None
    try:
        payload = json.loads(raw_next_data)
    except (json.JSONDecodeError, TypeError) as error:
        return [], "invalid", str(error)

    try:
        queries = payload["props"]["pageProps"]["dehydratedState"]["queries"]
    except (KeyError, TypeError):
        return [], "invalid", "missing Rippling dehydrated query state"
    if not isinstance(queries, list):
        return [], "invalid", "Rippling dehydrated queries is not a list"

    found_items = False
    records: list[dict] = []
    for query in queries:
        if not isinstance(query, dict):
            continue
        state = query.get("state")
        data = state.get("data") if isinstance(state, dict) else None
        if not isinstance(data, dict) or "items" not in data:
            continue
        items = data.get("items")
        if not isinstance(items, list):
            return [], "invalid", "Rippling jobs items is not a list"
        found_items = True
        records.extend(item for item in items if isinstance(item, dict))

    if not found_items:
        return [], "invalid", "missing Rippling jobs items"
    return records, "present" if records else "empty", None


def _candidate_from_record(
    record: dict,
    company: str,
    board_url: str,
) -> JobCandidate | None:
    title = _clean_text(record.get("name"))
    job_id = _clean_text(record.get("id"))
    raw_url = record.get("url")
    if not title or not job_id or not _JOB_ID_PATTERN.fullmatch(job_id):
        return None
    normalized = safe_normalize_url(raw_url, board_url) if isinstance(raw_url, str) else None
    parts = _board_parts(normalized or "")
    if (
        parts is None
        or parts[0].casefold() != company.casefold()
        or parts[1] is None
        or parts[1].casefold() != job_id.casefold()
    ):
        return None
    parsed = urlparse(normalized)
    detail_url = urlunparse(("https", _HOST, parsed.path.rstrip("/"), "", "", ""))

    locations = record.get("locations")
    location_names: list[str] = []
    if isinstance(locations, list):
        for location in locations:
            name = _clean_text(location.get("name")) if isinstance(location, dict) else None
            if name and name not in location_names:
                location_names.append(name)

    raw = {"job_id": job_id}
    department = record.get("department")
    department_name = _clean_text(department.get("name")) if isinstance(department, dict) else None
    language = _clean_text(record.get("language"))
    if department_name:
        raw["department"] = department_name
    if language:
        raw["language"] = language
    return JobCandidate(
        title=title,
        url=detail_url,
        provider="rippling",
        location="; ".join(location_names) or None,
        raw=raw,
    )


def _clean_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


ADAPTER = RipplingAdapter()
