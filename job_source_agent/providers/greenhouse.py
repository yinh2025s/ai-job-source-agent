from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

from ..web import FetchError, Page, safe_normalize_url
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_CUSTOM_PREFIX = "custom:"
_NUXT_PREFIX = "nuxt:"
_GREENHOUSE_RECORD_MARKERS = {"data_compliance", "requisition_id", "first_published"}
_JS_STRING = r'"(?:\\.|[^"\\])*"'
_MAX_NUXT_PAYLOAD_CHARS = 5_000_000


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

    def probe_board(self, fetcher, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        payload_url = _nuxt_payload_url(page.html, page_url)
        if payload_url is None:
            return None
        try:
            payload = fetcher.fetch(payload_url)
        except (FetchError, OSError, TimeoutError):
            return None
        if not _same_safe_host(payload.final_url or payload.url, urlparse(page_url).hostname or ""):
            return None
        records = _nuxt_greenhouse_records(payload.html, page_url)
        if not records:
            return None
        host = (urlparse(page_url).hostname or "").casefold()
        return JobBoard(
            url=page_url,
            provider=self.name,
            identifier=f"{_NUXT_PREFIX}{host}|{payload_url}",
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
        if board.identifier.startswith(_NUXT_PREFIX):
            return self._list_nuxt_payload(fetcher, board)
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

    def _list_nuxt_payload(self, fetcher, board: JobBoard) -> AdapterResult:
        identity = _nuxt_board_identity(board)
        if identity is None:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "variant": "nuxt_static_payload", "error": "invalid Nuxt board"},
            )
        expected_host, payload_url = identity
        try:
            page = fetcher.fetch(payload_url)
        except (FetchError, OSError, TimeoutError):
            raise
        if not _same_safe_host(page.final_url or page.url, expected_host):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "variant": "nuxt_static_payload", "error": "payload redirected outside origin"},
            )
        records = _nuxt_greenhouse_records(page.html, board.url)
        candidates = []
        for record in records:
            detail_url = _safe_custom_url(record.get("absolute_url"), board.url)
            if not detail_url or not _same_safe_www_host(detail_url, expected_host):
                continue
            candidates.append(
                JobCandidate(
                    title=str(record.get("title") or "").strip(),
                    url=detail_url,
                    provider=self.name,
                    raw={"id": record.get("id")},
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
                "variant": "nuxt_static_payload",
                "board_urls": [board.url],
                "payload_urls": [payload_url],
                "response_source": page.source,
                "candidate_count": len(candidates),
                "inventory_scope": "full",
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
        self.nuxt_payload_hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if (
            tag.casefold() == "link"
            and attributes.get("rel", "").casefold() == "preload"
            and attributes.get("as", "").casefold() == "script"
            and attributes.get("href", "").split("?", 1)[0].endswith("/careers/payload.js")
        ):
            self.nuxt_payload_hrefs.append(attributes["href"])
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


def _nuxt_payload_url(html: str, page_url: str) -> str | None:
    if "Loading open roles" not in (html or ""):
        return None
    parser = _NextDataParser()
    try:
        parser.feed(html or "")
    except (TypeError, ValueError):
        return None
    expected_host = (urlparse(page_url).hostname or "").casefold()
    for href in parser.nuxt_payload_hrefs:
        payload_url = safe_normalize_url(href, page_url)
        if payload_url and _same_safe_host(payload_url, expected_host):
            return payload_url
    return None


def _nuxt_greenhouse_records(payload: str, board_url: str) -> list[dict[str, Any]]:
    text = (payload or "")[:_MAX_NUXT_PAYLOAD_CHARS]
    expected_host = (urlparse(board_url).hostname or "").casefold()
    records = []
    pattern = re.compile(rf"\babsolute_url:(?P<url>{_JS_STRING})")
    title_pattern = re.compile(rf"\btitle:(?P<title>{_JS_STRING}),company_name:")
    for match in pattern.finditer(text):
        segment = text[match.end() : match.end() + 12_000]
        title_match = title_pattern.search(segment)
        if title_match is None:
            continue
        try:
            detail_url = json.loads(match.group("url"))
            title = json.loads(title_match.group("title"))
        except (json.JSONDecodeError, TypeError):
            continue
        normalized = _safe_custom_url(detail_url, board_url)
        if not normalized or not _same_safe_www_host(normalized, expected_host):
            continue
        parsed = urlparse(normalized)
        ids = parse_qs(parsed.query).get("gh_jid") or []
        if parsed.path.rstrip("/").casefold() != "/careersitem" or len(ids) != 1 or not ids[0].isdigit():
            continue
        if not str(title).strip():
            continue
        records.append({"id": int(ids[0]), "title": str(title).strip(), "absolute_url": normalized})
    return _dedupe_records(records)


def _nuxt_board_identity(board: JobBoard) -> tuple[str, str] | None:
    identifier = board.identifier or ""
    if board.provider != "greenhouse" or not identifier.startswith(_NUXT_PREFIX) or "|" not in identifier:
        return None
    host, payload_url = identifier.removeprefix(_NUXT_PREFIX).split("|", 1)
    if not host or not _same_safe_host(board.url, host) or not _same_safe_host(payload_url, host):
        return None
    if not urlparse(payload_url).path.endswith("/careers/payload.js"):
        return None
    return host, payload_url


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


def _same_safe_www_host(url: str, expected_host: str) -> bool:
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return False
    if not _is_safe_web_origin(parsed):
        return False
    actual_host = (parsed.hostname or "").casefold()
    expected_host = expected_host.casefold()
    return actual_host == expected_host or actual_host.removeprefix("www.") == expected_host.removeprefix("www.")


def _safe_custom_url(value: Any, base_url: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return safe_normalize_url(value, base_url)
    except (TypeError, ValueError):
        return None


ADAPTER = GreenhouseAdapter()
