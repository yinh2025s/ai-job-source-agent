from __future__ import annotations

from html.parser import HTMLParser
import json
from typing import Any, Iterator
from urllib.parse import urlparse

from ..web import Page, safe_normalize_url
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_CUSTOM_PREFIX = "custom:"
_GREENHOUSE_RECORD_MARKERS = {"data_compliance", "requisition_id", "first_published"}


class GreenhouseAdapter:
    name = "greenhouse"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except (TypeError, ValueError):
            return False
        host = (parsed.hostname or "").casefold()
        return _is_safe_web_origin(parsed) and (
            host == "greenhouse.io" or host.endswith(".greenhouse.io")
        )

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        parts = [part for part in urlparse(url).path.split("/") if part]
        if not parts:
            return None
        return JobBoard(url=url, provider=self.name, identifier=parts[0])

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        try:
            parsed = urlparse(page_url)
        except (TypeError, ValueError):
            return None
        if not _is_safe_web_origin(parsed) or not _greenhouse_records(page.html):
            return None
        return JobBoard(
            url=page_url,
            provider=self.name,
            identifier=f"{_CUSTOM_PREFIX}{(parsed.hostname or '').casefold()}",
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not board.identifier:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "missing Greenhouse board identifier"},
            )
        if board.identifier.startswith(_CUSTOM_PREFIX):
            return self._list_custom_frontend(fetcher, board)
        api_url = self.api_url(board.identifier)
        page = fetcher.fetch(api_url)
        try:
            data = json.loads(page.html)
        except json.JSONDecodeError:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="INVALID_STRUCTURED_DATA",
                trace={"adapter": self.name, "api_urls": [api_url]},
            )

        candidates = [
            JobCandidate(
                title=str(job.get("title") or ""),
                url=str(job.get("absolute_url") or ""),
                provider=self.name,
                location=_location_name(job),
                raw={"id": job.get("id")},
            )
            for job in data.get("jobs", [])
            if job.get("title") and job.get("absolute_url")
        ]
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            trace={
                "adapter": self.name,
                "api_urls": [api_url],
                "response_source": page.source,
                "candidate_count": len(candidates),
            },
        )

    def _list_custom_frontend(self, fetcher, board: JobBoard) -> AdapterResult:
        expected_host = board.identifier.removeprefix(_CUSTOM_PREFIX).casefold()
        if not expected_host or not _same_safe_host(board.url, expected_host):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "variant": "custom_frontend", "error": "invalid board origin"},
            )
        page = fetcher.fetch(board.url)
        final_url = page.final_url or page.url
        if not _same_safe_host(final_url, expected_host):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "variant": "custom_frontend", "error": "board redirected outside origin"},
            )
        candidates = []
        for record in _greenhouse_records(page.html):
            title = str(record.get("title") or "").strip()
            detail_url = _safe_custom_url(record.get("absolute_url"), final_url)
            if not title or not detail_url or not _same_safe_host(detail_url, expected_host):
                continue
            candidates.append(
                JobCandidate(
                    title=title,
                    url=detail_url,
                    provider=self.name,
                    location=_location_name(record),
                    raw={"id": record.get("id"), "requisition_id": record.get("requisition_id")},
                )
            )
        candidates = _dedupe_candidates(candidates)
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            trace={
                "adapter": self.name,
                "variant": "custom_frontend",
                "board_urls": [final_url],
                "response_source": page.source,
                "candidate_count": len(candidates),
            },
        )

    @staticmethod
    def api_url(board_identifier: str) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{board_identifier}/jobs?content=true"


def _location_name(job: dict) -> str | None:
    location = job.get("location")
    if isinstance(location, dict) and location.get("name"):
        return str(location["name"])
    return None


class _NextDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.payloads: list[str] = []
        self._capturing = False
        self._content: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if (
            tag.casefold() == "script"
            and attributes.get("id") == "__NEXT_DATA__"
            and attributes.get("type", "").casefold() == "application/json"
        ):
            self._capturing = True
            self._content = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._content.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._capturing:
            self.payloads.append("".join(self._content))
            self._capturing = False
            self._content = []


def _greenhouse_records(html: str) -> list[dict[str, Any]]:
    parser = _NextDataParser()
    try:
        parser.feed(html)
    except (TypeError, ValueError):
        return []
    records = []
    for content in parser.payloads:
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        records.extend(_walk_greenhouse_records(payload))
    return _dedupe_records(records)


def _walk_greenhouse_records(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if (
            value.get("id") is not None
            and value.get("title")
            and value.get("absolute_url")
            and _GREENHOUSE_RECORD_MARKERS.intersection(value)
        ):
            yield value
        for child in value.values():
            yield from _walk_greenhouse_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_greenhouse_records(child)


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped = []
    for record in records:
        key = str(record.get("absolute_url") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _dedupe_candidates(candidates: list[JobCandidate]) -> list[JobCandidate]:
    seen: set[str] = set()
    deduped = []
    for candidate in candidates:
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        deduped.append(candidate)
    return deduped


def _is_safe_web_origin(parsed) -> bool:
    try:
        port = parsed.port
    except ValueError:
        return False
    standard_port = port is None or (parsed.scheme == "https" and port == 443) or (
        parsed.scheme == "http" and port == 80
    )
    return (
        parsed.scheme in {"http", "https"}
        and parsed.username is None
        and parsed.password is None
        and standard_port
        and bool(parsed.hostname)
    )


def _same_safe_host(url: str, expected_host: str) -> bool:
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return False
    return _is_safe_web_origin(parsed) and (parsed.hostname or "").casefold() == expected_host


def _safe_custom_url(value: Any, base_url: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return safe_normalize_url(value, base_url)
    except (TypeError, ValueError):
        return None


ADAPTER = GreenhouseAdapter()
