from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlparse, urlunparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST = "jobs.ashbyhq.com"
_API_HOST = "api.ashbyhq.com"
_API_PATH_PREFIX = ("posting-api", "job-board")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_DETAIL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,127}$")
_TRACKING_QUERY_KEYS = {
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}
_URL_FIELDS = ("jobUrl", "jobURL", "job_url", "url", "externalLink")
_JOB_CONTAINER_KEYS = {"jobs", "jobpostings", "job_postings", "openings"}


class AshbyAdapter:
    name = "ashby"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _board_identifier(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        identifier = _board_identifier(url)
        if identifier is None:
            return None
        return JobBoard(
            url=_board_url(identifier),
            provider=self.name,
            identifier=identifier,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identifier = board.identifier
        if not identifier or not _IDENTIFIER_PATTERN.fullmatch(identifier):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                trace={"adapter": self.name, "error": "missing Ashby board identifier"},
            )

        api_url = self.api_url(identifier)
        board_url = _board_url(identifier)
        api_error: str | None = None
        fallback_reason = "api_fetch_failed"
        try:
            api_page = fetcher.fetch(api_url)
        except (FetchError, OSError, TimeoutError) as error:
            api_page = None
            api_error = str(error)

        if api_page is not None:
            try:
                data = json.loads(api_page.html)
            except (json.JSONDecodeError, TypeError) as error:
                data = None
                api_error = str(error)
                fallback_reason = "invalid_api_json"
            jobs = data.get("jobs") if isinstance(data, dict) else None
            if isinstance(jobs, list):
                candidates = _unique_candidates(jobs, identifier)
                if candidates:
                    return AdapterResult(
                        provider=self.name,
                        board=board,
                        candidates=candidates,
                        trace={
                            "adapter": self.name,
                            "api_urls": [api_url],
                            "board_urls": [],
                            "response_source": api_page.source,
                            "response_mode": "api",
                            "candidate_count": len(candidates),
                        },
                    )
                fallback_reason = "empty_api_response"
            elif data is not None:
                fallback_reason = "invalid_api_shape"

        try:
            board_page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            trace = {
                "adapter": self.name,
                "api_urls": [api_url],
                "board_urls": [board_url],
                "fallback_reason": fallback_reason,
                "error": str(error),
            }
            if api_error:
                trace["api_error"] = api_error
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                inventory_complete=False,
                trace=trace,
            )

        payloads = _embedded_payloads(board_page.html or "")
        candidates: list[JobCandidate] = []
        seen_urls: set[str] = set()
        found_jobs_container = False
        for payload in payloads:
            found_jobs_container = found_jobs_container or _contains_jobs_container(payload)
            for record in _walk_job_records(payload):
                candidate = _candidate(record, identifier)
                if candidate is None or candidate.url in seen_urls:
                    continue
                seen_urls.add(candidate.url)
                candidates.append(candidate)

        if candidates:
            reason_code = None
        elif found_jobs_container and api_error:
            reason_code = "PROVIDER_FETCH_FAILED"
        elif found_jobs_container:
            reason_code = "EMPTY_PROVIDER_RESPONSE"
        else:
            reason_code = "INVALID_STRUCTURED_DATA"
        trace = {
            "adapter": self.name,
            "api_urls": [api_url],
            "board_urls": [board_url],
            "response_source": board_page.source,
            "response_mode": "embedded_json",
            "fallback_reason": fallback_reason,
            "embedded_payload_count": len(payloads),
            "candidate_count": len(candidates),
        }
        if api_error:
            trace["api_error"] = api_error
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=reason_code,
            retryable=reason_code == "PROVIDER_FETCH_FAILED",
            inventory_complete=reason_code in {None, "EMPTY_PROVIDER_RESPONSE"},
            trace=trace,
        )

    @staticmethod
    def api_url(board_identifier: str) -> str:
        identifier = quote(board_identifier, safe="")
        return f"https://api.ashbyhq.com/posting-api/job-board/{identifier}"


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self._parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() == "script":
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._parts is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._parts is not None:
            self.scripts.append("".join(self._parts))
            self._parts = None


def _embedded_payloads(html: str) -> list[object]:
    parser = _ScriptParser()
    try:
        parser.feed(html)
    except (TypeError, ValueError):
        return []
    payloads: list[object] = []
    for script in parser.scripts:
        payloads.extend(_decode_json_values(unescape(script)))
    return payloads


def _decode_json_values(text: str) -> list[object]:
    decoder = json.JSONDecoder()
    values: list[object] = []
    cursor = 0
    while cursor < len(text):
        starts = [position for token in ("{", "[") if (position := text.find(token, cursor)) >= 0]
        if not starts:
            break
        start = min(starts)
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        values.append(value)
        cursor = max(end, start + 1)
    return values


def _walk_job_records(value: object):
    if isinstance(value, dict):
        if _looks_like_job(value):
            yield value
        for child in value.values():
            yield from _walk_job_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_job_records(child)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                yield from _walk_job_records(json.loads(stripped))
            except json.JSONDecodeError:
                return


def _looks_like_job(record: dict) -> bool:
    title = record.get("title") or record.get("name")
    return bool(
        isinstance(title, str)
        and title.strip()
        and any(isinstance(record.get(field), str) for field in _URL_FIELDS)
    )


def _contains_jobs_container(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in _JOB_CONTAINER_KEYS and isinstance(child, (list, dict)):
                return True
            if _contains_jobs_container(child):
                return True
    elif isinstance(value, list):
        return any(_contains_jobs_container(child) for child in value)
    return False


def _unique_candidates(jobs: list[object], identifier: str) -> list[JobCandidate]:
    candidates: list[JobCandidate] = []
    seen_urls: set[str] = set()
    for job in jobs:
        candidate = _candidate(job, identifier)
        if candidate is None or candidate.url in seen_urls:
            continue
        seen_urls.add(candidate.url)
        candidates.append(candidate)
    return candidates


def _candidate(job: object, identifier: str) -> JobCandidate | None:
    if not isinstance(job, dict):
        return None
    title = job.get("title") or job.get("name")
    if not isinstance(title, str) or not title.strip():
        return None
    raw_url = next(
        (job[field] for field in _URL_FIELDS if isinstance(job.get(field), str) and job[field].strip()),
        None,
    )
    job_url = _job_url(raw_url, identifier)
    if not job_url:
        return None
    return JobCandidate(
        title=title.strip(),
        url=job_url,
        provider="ashby",
        location=_location_names(job),
        raw={"id": job.get("id")},
    )


def _job_url(value: object, identifier: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = _safe_ashby_url(urljoin(_board_url(identifier), value.strip()))
    except ValueError:
        return None
    if (
        parsed is None
        or (parsed.hostname or "").casefold() != _HOST
        or not _safe_tracking_query(parsed.query)
        or parsed.fragment
    ):
        return None
    parts = _path_parts(parsed.path)
    if len(parts) != 2 or parts[0] != identifier:
        return None
    if not parts[1] or parts[1] in {".", ".."}:
        return None
    path = "/".join(quote(part, safe="-._~") for part in parts)
    return urlunparse(("https", _HOST, f"/{path}", "", "", ""))


def _board_identifier(url: str) -> str | None:
    parsed = _safe_ashby_url(url)
    if parsed is None or parsed.fragment:
        return None
    parts = _path_parts(parsed.path)
    host = (parsed.hostname or "").casefold()
    if host == _HOST:
        if not parts or not _IDENTIFIER_PATTERN.fullmatch(parts[0]):
            return None
        if len(parts) == 1:
            if not _safe_public_query(parsed.query, {"display": {"embedded"}}):
                return None
        elif len(parts) == 2 and parts[1] == "embed":
            if not _safe_public_query(parsed.query, {"version": None}):
                return None
        elif len(parts) == 2 and _DETAIL_PATTERN.fullmatch(parts[1]):
            if not _safe_public_query(parsed.query, {"embed": {"true"}}):
                return None
        else:
            return None
        return parts[0]
    elif (
        host == _API_HOST
        and not parsed.query
        and tuple(parts[:2]) == _API_PATH_PREFIX
    ):
        tenant_parts = parts[2:]
    else:
        return None
    if len(tenant_parts) != 1 or not _IDENTIFIER_PATTERN.fullmatch(tenant_parts[0]):
        return None
    return tenant_parts[0]


def _safe_ashby_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if parsed.scheme.casefold() != "https":
        return None
    if (parsed.hostname or "").casefold() not in {_HOST, _API_HOST}:
        return None
    if parsed.username or parsed.password or port not in {None, 443}:
        return None
    return parsed


def _safe_tracking_query(query: str) -> bool:
    return _safe_public_query(query, {})


def _safe_public_query(
    query: str,
    extra_keys: dict[str, set[str] | None],
) -> bool:
    if not query:
        return True
    if len(query) > 2048:
        return False
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    seen: set[str] = set()
    for key, value in pairs:
        normalized_key = key.casefold()
        if (
            normalized_key not in _TRACKING_QUERY_KEYS | set(extra_keys)
            or normalized_key in seen
            or not value
            or len(value) > 500
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            return False
        allowed_values = extra_keys.get(normalized_key)
        if normalized_key in extra_keys:
            if allowed_values is None:
                if not value.isdigit() or len(value) > 4:
                    return False
            elif value.casefold() not in allowed_values:
                return False
        seen.add(normalized_key)
    return bool(pairs)


def _path_parts(path: str) -> list[str]:
    if not path.startswith("/") or "\\" in path:
        return []
    normalized = path[1:-1] if path.endswith("/") else path[1:]
    raw_parts = normalized.split("/") if normalized else []
    if any(not part for part in raw_parts):
        return []
    parts = [unquote(part) for part in raw_parts]
    if any(
        not part or "/" in part or "\\" in part or "%" in part or part in {".", ".."}
        for part in parts
    ):
        return []
    return parts


def _board_url(identifier: str) -> str:
    return f"https://{_HOST}/{quote(identifier, safe='-_')}"


def _location_names(job: dict) -> str | None:
    locations: list[str] = []
    primary = _location_name(job.get("location"))
    if primary:
        locations.append(primary)

    secondary_locations = job.get("secondaryLocations")
    if isinstance(secondary_locations, list):
        locations.extend(
            location
            for item in secondary_locations
            if (location := _location_name(item))
        )

    unique_locations: list[str] = []
    seen: set[str] = set()
    for location in locations:
        normalized = " ".join(location.split()).casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_locations.append(location)
    return "; ".join(unique_locations) or None


def _location_name(location: object) -> str | None:
    if isinstance(location, str):
        return _public_text(location)
    if not isinstance(location, dict):
        return None
    for key in ("location", "name"):
        name = _public_text(location.get(key))
        if name:
            return name
    address = location.get("address")
    postal_address = address.get("postalAddress") if isinstance(address, dict) else None
    if not isinstance(postal_address, dict):
        return None
    parts = [
        part
        for key in ("addressLocality", "addressRegion", "addressCountry")
        if (part := _public_text(postal_address.get(key)))
    ]
    return ", ".join(parts) or None


def _public_text(value: object) -> str | None:
    return " ".join(value.split()) if isinstance(value, str) and value.strip() else None


ADAPTER = AshbyAdapter()
