from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import quote, unquote, urljoin, urlparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST = "apply.workable.com"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SHORTCODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_URL_FIELDS = ("url", "jobUrl", "job_url", "applicationUrl", "application_url", "href")
_TITLE_FIELDS = ("title", "name", "jobTitle", "job_title")
_LOCATION_FIELDS = ("location", "workplace", "jobLocation")
_PAGINATION_KEYS = {
    "currentpage",
    "current_page",
    "hasnextpage",
    "has_next_page",
    "next",
    "nextpage",
    "next_page",
    "nexturl",
    "next_url",
    "page",
    "pagecount",
    "page_count",
    "total",
    "totalcount",
    "total_count",
    "totalpages",
    "total_pages",
}


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
        try:
            page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                trace={"adapter": self.name, "board_urls": [board_url], "error": str(error)},
            )

        parser = _WorkableHTMLParser()
        try:
            parser.feed(page.html or "")
        except (TypeError, ValueError):
            parser = _WorkableHTMLParser()

        payloads = _json_payloads(parser.scripts)
        candidates = _anchor_candidates(parser.links, board.identifier)
        found_jobs_container = bool(candidates)
        pagination: dict[str, object] = {}
        for payload in payloads:
            found_jobs_container = found_jobs_container or _contains_jobs_container(payload)
            for record in _walk_records(payload):
                candidate = _candidate(record, board.identifier)
                if candidate is not None:
                    candidates.append(candidate)
            _collect_pagination(payload, pagination)
        candidates = _dedupe_candidates(candidates)

        if candidates:
            reason_code = None
        elif found_jobs_container:
            reason_code = "EMPTY_PROVIDER_RESPONSE"
        else:
            reason_code = "INVALID_STRUCTURED_DATA"
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
                "public_link_count": len(parser.links),
                "candidate_count": len(candidates),
                "pagination": pagination,
            },
        )


def _parsed_workable_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    scheme = parsed.scheme.casefold()
    standard_port = port is None or (scheme == "https" and port == 443) or (
        scheme == "http" and port == 80
    )
    if (
        scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or not standard_port
        or (parsed.hostname or "").casefold() != _HOST
    ):
        return None
    return parsed


class _WorkableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._script_parts: list[str] | None = None
        self._link_href = ""
        self._link_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "script":
            self._script_parts = []
        elif tag.casefold() == "a" and attributes.get("href"):
            self._link_href = attributes["href"]
            self._link_parts = []

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)
        if self._link_parts is not None:
            self._link_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._script_parts is not None:
            self.scripts.append("".join(self._script_parts).strip())
            self._script_parts = None
        elif tag.casefold() == "a" and self._link_parts is not None:
            title = " ".join("".join(self._link_parts).split())
            self.links.append((self._link_href, title))
            self._link_href = ""
            self._link_parts = None


def _json_payloads(scripts: list[str]) -> list[object]:
    payloads: list[object] = []
    decoder = json.JSONDecoder()
    for script in scripts:
        text = unescape(script).strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            starts = [index for token in ("{", "[") if (index := text.find(token)) >= 0]
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
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                yield from _walk_records(json.loads(text))
            except json.JSONDecodeError:
                return


def _looks_like_job(record: dict) -> bool:
    return bool(_first_text(record, _TITLE_FIELDS) and _record_shortcode(record, account=None))


def _contains_jobs_container(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in {"jobs", "joblist", "job_list", "positions", "openings"}:
                return isinstance(child, (list, dict, str))
            if _contains_jobs_container(child):
                return True
    elif isinstance(value, list):
        return any(_contains_jobs_container(child) for child in value)
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                return _contains_jobs_container(json.loads(text))
            except json.JSONDecodeError:
                return False
    return False


def _anchor_candidates(links: list[tuple[str, str]], account: str) -> list[JobCandidate]:
    candidates = []
    for raw_url, title in links:
        detail = _validated_detail(raw_url, account)
        if detail is None or not title:
            continue
        candidates.append(
            JobCandidate(title=title, url=detail[0], provider="workable", raw={"shortcode": detail[1]})
        )
    return candidates


def _candidate(record: dict, account: str) -> JobCandidate | None:
    title = _first_text(record, _TITLE_FIELDS)
    if not title:
        return None
    shortcode = _record_shortcode(record, account=account)
    if not shortcode:
        return None
    detail_url = f"https://{_HOST}/{quote(account, safe='-_')}/j/{quote(shortcode, safe='-_')}/"
    location = next((_location(record.get(field)) for field in _LOCATION_FIELDS if record.get(field)), None)
    return JobCandidate(
        title=title,
        url=detail_url,
        provider="workable",
        location=location,
        raw={"shortcode": shortcode},
    )


def _record_shortcode(record: dict, account: str | None) -> str | None:
    explicit_url = _first_text(record, _URL_FIELDS)
    if explicit_url:
        if account is None:
            try:
                parsed = urlparse(urljoin(f"https://{_HOST}/", explicit_url))
            except (TypeError, ValueError):
                return None
            parts = [unquote(part) for part in parsed.path.split("/") if part]
            return parts[-1] if len(parts) >= 3 and parts[-2] == "j" and _SHORTCODE_PATTERN.fullmatch(parts[-1]) else None
        detail = _validated_detail(explicit_url, account)
        return detail[1] if detail else None

    for field in ("shortcode", "shortCode", "code"):
        value = record.get(field)
        if isinstance(value, (str, int)):
            shortcode = str(value).strip()
            if _SHORTCODE_PATTERN.fullmatch(shortcode):
                return shortcode
    return None


def _validated_detail(raw_url: str, account: str) -> tuple[str, str] | None:
    try:
        parsed = _parsed_workable_url(urljoin(f"https://{_HOST}/{quote(account, safe='-_')}/", raw_url))
    except (TypeError, ValueError):
        return None
    if parsed is None or parsed.query or parsed.fragment:
        return None
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if (
        len(parts) != 3
        or parts[0].casefold() != account.casefold()
        or parts[1] != "j"
        or not _SHORTCODE_PATTERN.fullmatch(parts[2])
    ):
        return None
    return f"https://{_HOST}/{quote(account, safe='-_')}/j/{quote(parts[2], safe='-_')}/", parts[2]


def _first_text(record: dict, fields: tuple[str, ...]) -> str:
    for field in fields:
        value = record.get(field)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


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
        if isinstance(item, str) and item.strip() and item.strip().casefold() not in {part.casefold() for part in parts}:
            parts.append(item.strip())
    return ", ".join(parts) or None


def _collect_pagination(value: object, output: dict[str, object]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in _PAGINATION_KEYS and isinstance(child, (str, int, float, bool, type(None))):
                output.setdefault(key, child)
            _collect_pagination(child, output)
    elif isinstance(value, list):
        for child in value:
            _collect_pagination(child, output)
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                _collect_pagination(json.loads(text), output)
            except json.JSONDecodeError:
                return


def _dedupe_candidates(candidates: list[JobCandidate]) -> list[JobCandidate]:
    output = []
    seen = set()
    for candidate in candidates:
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        output.append(candidate)
    return output


ADAPTER = WorkableAdapter()
