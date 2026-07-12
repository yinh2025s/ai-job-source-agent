from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from typing import Any, Iterator
from urllib.parse import urlparse, urlunparse

from ..web import FetchError, safe_normalize_url
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_ICIMS_HOST = re.compile(r"^careers-[a-z0-9.-]+\.icims\.com$", re.IGNORECASE)
_JOB_CONTAINER_KEYS = {
    "jobs",
    "jobpostings",
    "job_postings",
    "postings",
    "results",
    "items",
    "itemlistelement",
}
_ID_FIELDS = ("id", "jobId", "job_id", "jobNumber", "job_number")
_URL_FIELDS = ("url", "jobUrl", "job_url", "detailUrl", "detail_url", "link")


class ICIMSAdapter:
    name = "icims"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except (TypeError, ValueError):
            return False
        host = (parsed.hostname or "").lower()
        parts = [part.lower() for part in parsed.path.split("/") if part]
        if not _ICIMS_HOST.fullmatch(host) or len(parts) < 2 or parts[0] != "jobs":
            return False
        return parts[1].startswith("search") or (
            parts[1].isdigit() and "job" in parts[2:]
        )

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        is_detail = len(parts) >= 2 and parts[1].isdigit()
        path = "/jobs/search" if is_detail else parsed.path.rstrip("/")
        board_url = urlunparse(
            (parsed.scheme or "https", parsed.netloc, path, "", "", "")
        )
        return JobBoard(
            url=board_url,
            provider=self.name,
            identifier=(parsed.hostname or "").lower(),
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not board.identifier or not _ICIMS_HOST.fullmatch(board.identifier):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "missing iCIMS careers board identifier"},
            )

        try:
            page = fetcher.fetch(board.url)
        except (FetchError, OSError, TimeoutError) as error:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                trace={"adapter": self.name, "board_urls": [board.url], "error": str(error)},
            )

        scripts = _ScriptParser()
        try:
            scripts.feed(page.html)
        except (TypeError, ValueError) as error:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="INVALID_STRUCTURED_DATA",
                trace={"adapter": self.name, "board_urls": [board.url], "error": str(error)},
            )

        candidates: list[JobCandidate] = []
        structured_script_count = 0
        for script_type, content in scripts.scripts:
            payload = _decode_script_json(content)
            if payload is None:
                continue
            structured_script_count += 1
            is_json_ld = script_type == "application/ld+json"
            for record in _walk_job_records(payload, json_ld=is_json_ld):
                candidate = _candidate_from_record(record, board, json_ld=is_json_ld)
                if candidate is not None:
                    candidates.append(candidate)

        candidates = _dedupe_candidates(candidates)
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            trace={
                "adapter": self.name,
                "board_urls": [board.url],
                "response_source": page.source,
                "structured_script_count": structured_script_count,
                "candidate_count": len(candidates),
            },
        )


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[tuple[str, str]] = []
        self._script_type: str | None = None
        self._content: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        attributes = {key.lower(): (value or "") for key, value in attrs}
        script_type = attributes.get("type", "").lower().split(";", 1)[0].strip()
        if script_type in {"application/ld+json", "application/json"}:
            self._script_type = script_type
            self._content = []

    def handle_data(self, data: str) -> None:
        if self._script_type is not None:
            self._content.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or self._script_type is None:
            return
        self.scripts.append((self._script_type, "".join(self._content)))
        self._script_type = None
        self._content = []


def _decode_script_json(content: str) -> Any | None:
    text = content.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    first = min((index for index in (text.find("{"), text.find("[")) if index >= 0), default=-1)
    if first < 0:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(text[first:])
    except json.JSONDecodeError:
        return None
    return payload


def _walk_job_records(
    value: Any,
    *,
    json_ld: bool,
    in_job_container: bool = False,
) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        record_type = value.get("@type")
        types = {str(item).casefold() for item in record_type} if isinstance(record_type, list) else {str(record_type).casefold()}
        is_job_posting = "jobposting" in types
        if (json_ld and is_job_posting) or (not json_ld and in_job_container):
            yield value
        for key, child in value.items():
            child_container = in_job_container or str(key).casefold() in _JOB_CONTAINER_KEYS
            yield from _walk_job_records(
                child,
                json_ld=json_ld,
                in_job_container=child_container,
            )
    elif isinstance(value, list):
        for item in value:
            yield from _walk_job_records(
                item,
                json_ld=json_ld,
                in_job_container=in_job_container,
            )


def _candidate_from_record(
    record: dict[str, Any],
    board: JobBoard,
    *,
    json_ld: bool,
) -> JobCandidate | None:
    title = _first_text(record, ("title", "name", "jobTitle", "job_title"))
    if not title:
        return None
    raw_url = _first_text(record, _URL_FIELDS)
    job_id = _first_text(record, _ID_FIELDS)
    if raw_url:
        detail_url = safe_normalize_url(raw_url, board.url)
    elif job_id:
        detail_url = safe_normalize_url(
            f"/jobs/{job_id}/{_slugify(title)}/job",
            board.url,
        )
    else:
        return None
    if not detail_url or not _is_icims_detail_url(detail_url, board.identifier):
        return None

    raw = dict(record)
    if json_ld:
        raw = {key: record.get(key) for key in ("@type", "identifier", "datePosted") if key in record}
    return JobCandidate(
        title=title,
        url=detail_url,
        provider="icims",
        location=_location(record),
        raw=raw,
    )


def _first_text(record: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = record.get(field)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _location(record: dict[str, Any]) -> str | None:
    value = record.get("location") or record.get("jobLocation") or record.get("locations")
    if isinstance(value, list):
        locations = [location for item in value if (location := _location_value(item))]
        return "; ".join(dict.fromkeys(locations)) or None
    return _location_value(value)


def _location_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None
    address = value.get("address") if isinstance(value.get("address"), dict) else value
    direct = _first_text(address, ("name", "fullLocation", "formattedAddress"))
    if direct:
        return direct
    parts = [
        _first_text(address, (field,))
        for field in ("addressLocality", "addressRegion", "addressCountry", "city", "state", "country")
    ]
    return ", ".join(dict.fromkeys(part for part in parts if part)) or None


def _is_icims_detail_url(url: str, expected_host: str | None) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    parts = [part.lower() for part in parsed.path.split("/") if part]
    return (
        (parsed.hostname or "").lower() == (expected_host or "").lower()
        and len(parts) >= 3
        and parts[0] == "jobs"
        and parts[1].isdigit()
        and "job" in parts[2:]
    )


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-") or "opening"


def _dedupe_candidates(candidates: list[JobCandidate]) -> list[JobCandidate]:
    seen: set[str] = set()
    deduped = []
    for candidate in candidates:
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        deduped.append(candidate)
    return deduped


ADAPTER = ICIMSAdapter()
