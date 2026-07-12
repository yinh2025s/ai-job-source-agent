from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import json
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from ..web import safe_normalize_url
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST_SUFFIXES = ("successfactors.com", "sapsf.com")
_DETAIL_ID_FIELDS = ("career_job_req_id", "jobReqId", "job_req_id")
_TITLE_FIELDS = ("jobTitle", "title", "job_title", "name")
_URL_FIELDS = ("jobUrl", "job_url", "detailUrl", "jobDetailUrl", "url", "href")
_DETAIL_QUERY_KEYS = {"career_job_req_id", "jobreqid", "job_req_id"}
_SEARCH_QUERY_KEYS = {"keyword", "q", "search"}


class SuccessFactorsAdapter:
    name = "successfactors"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            return False
        return any(host == suffix or host.endswith(f".{suffix}") for suffix in _HOST_SUFFIXES)

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        normalized = safe_normalize_url(url)
        if not normalized:
            return None

        parsed = urlparse(normalized)
        query = []
        company = ""
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            key_folded = key.casefold()
            if key_folded in {"company", "companyid", "company_id"} and value and not company:
                company = value
            if key_folded in _DETAIL_QUERY_KEYS or key_folded in _SEARCH_QUERY_KEYS:
                continue
            if key_folded == "career_ns" and value.casefold() == "job_listing":
                continue
            query.append((key, value))

        board_url = urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))
        identifier = company or (parsed.hostname or "").lower()
        return JobBoard(url=board_url, provider=self.name, identifier=identifier or None)

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not board.identifier:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "missing SuccessFactors board identifier"},
            )

        search_url = _search_url(board.url, query.title)
        page = fetcher.fetch(search_url)
        parser = _SuccessFactorsHTMLParser()
        parser.feed(page.html or "")

        candidates = _anchor_candidates(parser.links, board)
        values, malformed_json = _embedded_json_values(page.html or "", parser.scripts)
        for value in values:
            candidates.extend(_walk_candidates(value, board))
        candidates = _dedupe_candidates(candidates)

        reason_code = None
        if not candidates:
            reason_code = "INVALID_STRUCTURED_DATA" if malformed_json else "EMPTY_PROVIDER_RESPONSE"
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=reason_code,
            trace={
                "adapter": self.name,
                "search_urls": [search_url],
                "response_source": page.source,
                "candidate_count": len(candidates),
                "embedded_payload_count": len(values),
            },
        )


class _SuccessFactorsHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[tuple[str, str]] = []
        self.links: list[tuple[str, str]] = []
        self._script_type = ""
        self._script_parts: list[str] | None = None
        self._href = ""
        self._link_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "script":
            self._script_type = attributes.get("type", "")
            self._script_parts = []
        elif tag.casefold() == "a" and attributes.get("href"):
            self._href = attributes["href"]
            self._link_parts = []

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)
        if self._link_parts is not None:
            self._link_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._script_parts is not None:
            self.scripts.append((self._script_type, "".join(self._script_parts)))
            self._script_parts = None
            self._script_type = ""
        elif tag.casefold() == "a" and self._link_parts is not None:
            self.links.append((self._href, " ".join("".join(self._link_parts).split())))
            self._href = ""
            self._link_parts = None


def _search_url(board_url: str, title: str | None) -> str:
    if not title or not title.strip():
        return board_url
    parsed = urlparse(board_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _SEARCH_QUERY_KEYS
    ]
    query.append(("keyword", title.strip()))
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


def _embedded_json_values(
    html: str,
    scripts: list[tuple[str, str]],
) -> tuple[list[object], bool]:
    values: list[object] = []
    malformed_json = False
    for script_type, script in scripts:
        decoded = unescape(script).strip()
        if not decoded:
            continue
        script_values = _decode_json_fragments(decoded)
        values.extend(script_values)
        if "json" in script_type.casefold() and not script_values:
            malformed_json = True

    # Some SuccessFactors themes serialize state into data attributes rather
    # than script tags. Scanning the full document catches those JSON objects.
    values.extend(_decode_json_fragments(unescape(html)))
    return values, malformed_json


def _decode_json_fragments(text: str) -> list[object]:
    decoder = json.JSONDecoder()
    values = []
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


def _walk_candidates(value: object, board: JobBoard):
    if isinstance(value, dict):
        candidate = _record_candidate(value, board)
        if candidate is not None:
            yield candidate
        for child in value.values():
            yield from _walk_candidates(child, board)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_candidates(child, board)


def _record_candidate(record: dict, board: JobBoard) -> JobCandidate | None:
    title = _first_text(record, _TITLE_FIELDS)
    if not title:
        return None
    job_req_id = _first_text(record, _DETAIL_ID_FIELDS)
    detail_url = _explicit_detail_url(record, board)
    if not detail_url and job_req_id:
        detail_url = _reconstruct_detail_url(board.url, job_req_id)
    if not detail_url:
        return None
    return JobCandidate(
        title=title,
        url=detail_url,
        provider="successfactors",
        location=_location(record),
        raw={"job_req_id": job_req_id or None},
    )


def _anchor_candidates(links: list[tuple[str, str]], board: JobBoard) -> list[JobCandidate]:
    candidates = []
    for href, text in links:
        normalized = safe_normalize_url(href, board.url)
        if not normalized or not _recognized_host(normalized):
            continue
        query = {key.casefold(): value for key, value in parse_qsl(urlparse(normalized).query)}
        job_req_id = next((query[key] for key in _DETAIL_QUERY_KEYS if query.get(key)), "")
        if not job_req_id or not text.strip():
            continue
        candidates.append(
            JobCandidate(
                title=text.strip(),
                url=normalized,
                provider="successfactors",
                raw={"job_req_id": job_req_id},
            )
        )
    return candidates


def _explicit_detail_url(record: dict, board: JobBoard) -> str | None:
    raw_url = _first_text(record, _URL_FIELDS)
    if not raw_url:
        return None
    normalized = safe_normalize_url(urljoin(board.url, raw_url))
    return normalized if normalized and _recognized_host(normalized) else None


def _reconstruct_detail_url(board_url: str, job_req_id: str) -> str:
    parsed = urlparse(board_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _DETAIL_QUERY_KEYS and key.casefold() != "career_ns"
    ]
    query.extend(
        (("career_ns", "job_listing"), ("career_job_req_id", job_req_id.strip()))
    )
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


def _location(record: dict) -> str | None:
    value = next(
        (record[field] for field in ("location", "jobLocation", "locationName") if record.get(field)),
        None,
    )
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        address = value.get("address") if isinstance(value.get("address"), dict) else value
        name = _first_text(address, ("name", "addressLocality", "city"))
        region = _first_text(address, ("addressRegion", "state"))
        country = _first_text(address, ("addressCountry", "country"))
        parts = []
        for part in (name, region, country):
            if part and part.casefold() not in {existing.casefold() for existing in parts}:
                parts.append(part)
        return ", ".join(parts) or None
    return None


def _first_text(record: dict, fields: tuple[str, ...]) -> str:
    for field in fields:
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return ""


def _recognized_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _HOST_SUFFIXES)


def _dedupe_candidates(candidates: list[JobCandidate]) -> list[JobCandidate]:
    seen = set()
    deduped = []
    for candidate in candidates:
        key = (candidate.url.rstrip("/"), candidate.title.casefold())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


ADAPTER = SuccessFactorsAdapter()
