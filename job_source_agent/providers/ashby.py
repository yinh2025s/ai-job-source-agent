from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST = "jobs.ashbyhq.com"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_URL_FIELDS = ("jobUrl", "jobURL", "job_url", "url", "externalLink")
_JOB_CONTAINER_KEYS = {"jobs", "jobpostings", "job_postings", "openings"}


class AshbyAdapter:
    name = "ashby"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _parsed_public_url(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        parsed = _parsed_public_url(url)
        if parsed is None:
            return None
        parts = _path_parts(parsed.path)
        if not parts or not _IDENTIFIER_PATTERN.fullmatch(parts[0]):
            return None
        identifier = parts[0]
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
        location=_location_name(job.get("location")),
        raw={"id": job.get("id")},
    )


def _job_url(value: object, identifier: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = _parsed_public_url(urljoin(_board_url(identifier), value.strip()))
    except ValueError:
        return None
    if parsed is None:
        return None
    parts = _path_parts(parsed.path)
    if len(parts) != 2 or parts[0].casefold() != identifier.casefold():
        return None
    if not parts[1] or parts[1] in {".", ".."}:
        return None
    path = "/".join(quote(part, safe="-._~") for part in parts)
    return urlunparse(("https", _HOST, f"/{path}", "", "", ""))


def _parsed_public_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    scheme = parsed.scheme.casefold()
    standard_port = port is None or (scheme == "https" and port == 443) or (
        scheme == "http" and port == 80
    )
    if scheme not in {"http", "https"}:
        return None
    if (parsed.hostname or "").casefold() != _HOST:
        return None
    if parsed.username or parsed.password or not standard_port:
        return None
    return parsed


def _path_parts(path: str) -> list[str]:
    parts = [unquote(part) for part in path.split("/") if part]
    if any(not part or "/" in part or "\\" in part or part in {".", ".."} for part in parts):
        return []
    return parts


def _board_url(identifier: str) -> str:
    return f"https://{_HOST}/{quote(identifier, safe='-_')}"


def _location_name(location: object) -> str | None:
    if isinstance(location, str) and location.strip():
        return location.strip()
    if isinstance(location, dict):
        name = location.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


ADAPTER = AshbyAdapter()
