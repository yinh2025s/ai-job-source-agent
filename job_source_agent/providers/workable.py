from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import quote, unquote, urlparse

from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST = "apply.workable.com"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SHORTCODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_URL_FIELDS = ("url", "jobUrl", "job_url", "applicationUrl", "application_url")


class WorkableAdapter:
    name = "workable"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _parsed_workable_url(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        parsed = _parsed_workable_url(url)
        if parsed is None:
            return None
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if not parts or not _IDENTIFIER_PATTERN.fullmatch(parts[0]):
            return None
        identifier = parts[0]
        return JobBoard(
            url=f"https://{_HOST}/{quote(identifier, safe='-_')}/",
            provider=self.name,
            identifier=identifier,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not board.identifier or not _IDENTIFIER_PATTERN.fullmatch(board.identifier):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "missing Workable account identifier"},
            )

        board_url = f"https://{_HOST}/{quote(board.identifier, safe='-_')}/"
        page = fetcher.fetch(board_url)
        payloads = _json_payloads(page.html)
        if not payloads:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="INVALID_STRUCTURED_DATA",
                trace={
                    "adapter": self.name,
                    "board_urls": [board_url],
                    "response_source": page.source,
                    "error": "no valid embedded JSON payload",
                },
            )

        candidates: list[JobCandidate] = []
        seen_urls: set[str] = set()
        found_jobs_container = False
        for payload in payloads:
            found_jobs_container = found_jobs_container or _contains_jobs_container(payload)
            for record in _walk_records(payload):
                candidate = _candidate(record, board.identifier)
                if candidate is None or candidate.url in seen_urls:
                    continue
                seen_urls.add(candidate.url)
                candidates.append(candidate)

        reason_code = None if candidates else (
            "EMPTY_PROVIDER_RESPONSE" if found_jobs_container else "INVALID_STRUCTURED_DATA"
        )
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=reason_code,
            trace={
                "adapter": self.name,
                "board_urls": [board_url],
                "response_source": page.source,
                "payload_count": len(payloads),
                "candidate_count": len(candidates),
            },
        )


def _parsed_workable_url(url: str):
    try:
        parsed = urlparse(url)
        parsed.port
        if (parsed.hostname or "").lower() != _HOST:
            return None
        return parsed
    except (TypeError, ValueError):
        return None


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self._in_script = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "script":
            self._in_script = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_script:
            self.scripts.append("".join(self._parts).strip())
            self._in_script = False
            self._parts = []


def _json_payloads(html: str) -> list[object]:
    parser = _ScriptParser()
    try:
        parser.feed(html or "")
    except (TypeError, ValueError):
        return []

    payloads = []
    decoder = json.JSONDecoder()
    for script in parser.scripts:
        text = unescape(script).strip()
        if not text:
            continue
        try:
            payloads.append(json.loads(text))
            continue
        except (json.JSONDecodeError, TypeError):
            pass

        starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
        if not starts:
            continue
        try:
            payload, _ = decoder.raw_decode(text[min(starts):])
        except (json.JSONDecodeError, TypeError):
            continue
        payloads.append(payload)
    return payloads


def _walk_records(value: object):
    if isinstance(value, dict):
        if _looks_like_job(value):
            yield value
        for child in value.values():
            yield from _walk_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_records(child)


def _looks_like_job(record: dict) -> bool:
    title = record.get("title") or record.get("name")
    return bool(isinstance(title, str) and title.strip() and _record_shortcode(record))


def _contains_jobs_container(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in {"jobs", "joblist", "job_list", "positions", "openings"}:
                return isinstance(child, (list, dict))
            if _contains_jobs_container(child):
                return True
    elif isinstance(value, list):
        return any(_contains_jobs_container(child) for child in value)
    return False


def _candidate(record: dict, account: str) -> JobCandidate | None:
    title = record.get("title") or record.get("name")
    if not isinstance(title, str) or not title.strip():
        return None
    shortcode = _record_shortcode(record)
    if not shortcode:
        return None
    detail_url = f"https://{_HOST}/{quote(account, safe='-_')}/j/{quote(shortcode, safe='-_')}/"
    return JobCandidate(
        title=title.strip(),
        url=detail_url,
        provider="workable",
        location=_location(record.get("location")),
        raw={"shortcode": shortcode},
    )


def _record_shortcode(record: dict) -> str | None:
    for field in ("shortcode", "shortCode", "code"):
        value = record.get(field)
        if isinstance(value, (str, int)):
            shortcode = str(value).strip()
            if _SHORTCODE_PATTERN.fullmatch(shortcode):
                return shortcode

    for field in _URL_FIELDS:
        value = record.get(field)
        if not isinstance(value, str):
            continue
        try:
            parsed = urlparse(value)
        except ValueError:
            continue
        if parsed.hostname and parsed.hostname.lower() != _HOST:
            continue
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 3 and parts[-2] == "j" and _SHORTCODE_PATTERN.fullmatch(parts[-1]):
            return parts[-1]
    return None


def _location(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if not isinstance(value, dict):
        return None
    for key in ("name", "location_str", "fullLocation"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    parts = []
    for key in ("city", "region", "country"):
        item = value.get(key)
        if isinstance(item, str) and item.strip() and item.strip().casefold() not in {
            part.casefold() for part in parts
        }:
            parts.append(item.strip())
    return ", ".join(parts) or None


ADAPTER = WorkableAdapter()
