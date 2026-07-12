from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlparse, urlunparse

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
_NESTED_RECORD_KEYS = ("job", "fields", "data", "posting")
_PAGINATION_KEYS = {"next", "nextpage", "next_page", "nexturl", "next_url"}
_PAGINATION_QUERY_KEYS = {"o", "offset", "page", "pr", "start"}
_MAX_PAGES = 5


class ICIMSAdapter:
    name = "icims"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except (TypeError, ValueError):
            return False
        parts = [part.lower() for part in parsed.path.split("/") if part]
        if not _is_safe_icims_origin(parsed) or len(parts) < 2 or parts[0] != "jobs":
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
        if (
            not board.identifier
            or not _ICIMS_HOST.fullmatch(board.identifier)
            or not _is_icims_search_url(board.url, board.identifier)
        ):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "missing iCIMS careers board identifier"},
            )

        candidates: list[JobCandidate] = []
        structured_script_count = 0
        page_urls: list[str] = []
        page_errors: list[dict[str, str]] = []
        rejected_pagination_urls: list[str] = []
        pending = [board.url]
        queued = {board.url}
        response_source: str | None = None

        while pending and len(page_urls) < _MAX_PAGES:
            requested_url = pending.pop(0)
            try:
                page = fetcher.fetch(requested_url)
            except (FetchError, OSError, TimeoutError) as error:
                page_errors.append({"url": requested_url, "error": str(error)})
                continue

            final_url = page.final_url or page.url
            if not _is_icims_search_url(final_url, board.identifier):
                rejected_pagination_urls.append(final_url)
                continue

            queued.add(final_url)
            page_urls.append(final_url)
            if response_source is None:
                response_source = page.source
            scripts = _ScriptParser()
            try:
                scripts.feed(page.html)
            except (TypeError, ValueError) as error:
                page_errors.append({"url": final_url, "error": str(error)})
                continue

            discovered_urls = list(scripts.pagination_hrefs)
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
                discovered_urls.extend(_walk_pagination_values(payload))

            for raw_url in discovered_urls:
                normalized = safe_normalize_url(raw_url, final_url)
                if not normalized or not _is_icims_search_url(normalized, board.identifier):
                    rejected_pagination_urls.append(str(raw_url))
                    continue
                if normalized not in queued:
                    queued.add(normalized)
                    pending.append(normalized)

        candidates = _dedupe_candidates(candidates)
        if not page_urls and rejected_pagination_urls:
            reason_code = "PROVIDER_VARIANT_UNSUPPORTED"
        elif not candidates and page_errors:
            reason_code = "PROVIDER_FETCH_FAILED"
        else:
            reason_code = None if candidates else "EMPTY_PROVIDER_RESPONSE"
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=reason_code,
            retryable=reason_code == "PROVIDER_FETCH_FAILED",
            trace={
                "adapter": self.name,
                "board_urls": page_urls or [board.url],
                "response_source": response_source,
                "structured_script_count": structured_script_count,
                "candidate_count": len(candidates),
                "page_count": len(page_urls),
                "page_errors": page_errors,
                "rejected_pagination_urls": list(dict.fromkeys(rejected_pagination_urls)),
            },
        )


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[tuple[str, str]] = []
        self.pagination_hrefs: list[str] = []
        self._script_type: str | None = None
        self._content: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        attributes = {key.lower(): (value or "") for key, value in attrs}
        if tag_name == "a":
            href = attributes.get("href", "")
            rel = attributes.get("rel", "").casefold().split()
            classes = attributes.get("class", "").casefold().split()
            if href and (
                "next" in rel
                or any("paging" in class_name or "pagination" in class_name for class_name in classes)
                or _has_pagination_query(href)
            ):
                self.pagination_hrefs.append(href)
            return
        if tag_name != "script":
            return
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


def _walk_pagination_values(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in _PAGINATION_KEYS:
                if isinstance(child, str):
                    yield child
                elif isinstance(child, dict):
                    for field in ("href", "url"):
                        target = child.get(field)
                        if isinstance(target, str):
                            yield target
            yield from _walk_pagination_values(child)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_pagination_values(item)


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
    for source in _record_sources(record):
        for field in fields:
            value = source.get(field)
            if isinstance(value, dict) and "value" in value:
                value = value["value"]
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return ""


def _record_sources(record: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield record
    for key in _NESTED_RECORD_KEYS:
        nested = record.get(key)
        if isinstance(nested, dict):
            yield nested


def _location(record: dict[str, Any]) -> str | None:
    for source in _record_sources(record):
        value = source.get("location") or source.get("jobLocation") or source.get("locations")
        if isinstance(value, dict) and "value" in value:
            value = value["value"]
        if isinstance(value, list):
            locations = [location for item in value if (location := _location_value(item))]
            if locations:
                return "; ".join(dict.fromkeys(locations))
        else:
            location = _location_value(value)
            if location:
                return location
    return None


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
        _is_safe_icims_origin(parsed, expected_host)
        and len(parts) >= 3
        and parts[0] == "jobs"
        and parts[1].isdigit()
        and "job" in parts[2:]
    )


def _is_icims_search_url(url: str, expected_host: str | None) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    parts = [part.casefold() for part in parsed.path.split("/") if part]
    return (
        _is_safe_icims_origin(parsed, expected_host)
        and len(parts) >= 2
        and parts[0] == "jobs"
        and parts[1].startswith("search")
    )


def _has_pagination_query(url: str) -> bool:
    try:
        return any(key.casefold() in _PAGINATION_QUERY_KEYS for key, _ in parse_qsl(urlparse(url).query))
    except ValueError:
        return False


def _is_safe_icims_origin(parsed, expected_host: str | None = None) -> bool:
    try:
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    standard_port = port is None or (parsed.scheme == "https" and port == 443) or (
        parsed.scheme == "http" and port == 80
    )
    return (
        parsed.scheme in {"http", "https"}
        and parsed.username is None
        and parsed.password is None
        and standard_port
        and bool(_ICIMS_HOST.fullmatch(host))
        and (expected_host is None or host == expected_host.casefold())
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
