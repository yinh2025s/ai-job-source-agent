from __future__ import annotations

import json
import re
from urllib.parse import urlencode, urlparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_BOARD_PATH = re.compile(r"^/careersection/(?P<code>[A-Za-z0-9_-]{1,64})/jobsearch\.ftl/?$")
_DETAIL_PATH = re.compile(r"^/careersection/(?P<code>[A-Za-z0-9_-]{1,64})/jobdetail\.ftl/?$")
_PORTAL = re.compile(r"^[0-9]{1,20}$")
_JOB_ID = re.compile(r"^[0-9]{1,20}$")
_MAX_CONFIG_CHARS = 250_000
_MAX_PAGES = 5
_PAGE_SIZE = 25


class TaleoAdapter:
    name = "taleo"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        parsed = _safe_url(url)
        return parsed is not None and bool(
            _BOARD_PATH.fullmatch(parsed.path) or _DETAIL_PATH.fullmatch(parsed.path)
        )

    def identify_board(self, url: str) -> JobBoard | None:
        parsed = _safe_url(url)
        if parsed is None:
            return None
        match = _BOARD_PATH.fullmatch(parsed.path) or _DETAIL_PATH.fullmatch(parsed.path)
        if match is None:
            return None
        host = (parsed.hostname or "").casefold()
        code = match.group("code")
        return JobBoard(
            url=f"https://{host}/careersection/{code}/jobsearch.ftl",
            provider=self.name,
            identifier=f"{host}|{code}",
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identity = _board_identity(board)
        if identity is None:
            return _unsupported(board, "invalid Taleo board")
        host, code = identity
        board_url = f"https://{host}/careersection/{code}/jobsearch.ftl"
        try:
            shell = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, board_url, [], error)

        final = _safe_url(shell.final_url or shell.url)
        if final is None or (final.hostname or "").casefold() != host:
            return _unsupported(board, "Taleo board redirected outside the tenant", shell.final_url or shell.url)
        final_match = _BOARD_PATH.fullmatch(final.path)
        if final_match is None or final_match.group("code") != code:
            return _unsupported(board, "Taleo board redirected outside the career section", shell.final_url or shell.url)

        config = _portal_config(shell.html, code)
        if config is None:
            return _invalid(board, board_url, [], "missing Taleo FacetedSearch configuration")
        portal, lang, source = config
        api_params = {"lang": lang, "portal": portal}
        if source:
            api_params["src"] = source
        api_url = f"https://{host}/careersection/rest/jobboard/searchjobs?" + urlencode(api_params)
        candidates: list[JobCandidate] = []
        seen: set[str] = set()
        api_urls: list[str] = []
        pages_fetched = 0
        total_found: int | None = None
        target = _normalized_title(query.title)

        for page_no in range(1, _MAX_PAGES + 1):
            api_urls.append(api_url)
            try:
                response = fetcher.fetch(
                    api_url,
                    data=json.dumps(_search_payload(query, page_no)).encode(),
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Referer": board_url,
                        "tz": "GMT+00:00",
                        "tzname": "UTC",
                    },
                )
            except (FetchError, OSError, TimeoutError) as error:
                if candidates:
                    break
                return _fetch_failure(board, board_url, api_urls, error)
            if not _is_expected_api(response.final_url or response.url, host):
                return _unsupported(board, "Taleo API redirected outside the tenant", response.final_url or response.url)
            try:
                body = json.loads(response.html)
            except (json.JSONDecodeError, TypeError):
                return _invalid(board, board_url, api_urls, "invalid Taleo search response")
            if isinstance(body, dict) and body.get("careerSectionUnAvailable") is True:
                return _unsupported(board, "Taleo career section is unavailable", response.final_url or response.url)
            records = body.get("requisitionList") if isinstance(body, dict) else None
            paging = body.get("pagingData") if isinstance(body, dict) else None
            if not isinstance(records, list) or not isinstance(paging, dict):
                return _invalid(board, board_url, api_urls, "missing Taleo inventory fields")
            pages_fetched += 1
            total_found = _nonnegative_int(paging.get("totalCount"), total_found)
            for record in records:
                candidate = _candidate(record, host, code, source)
                if candidate is None or candidate.url in seen:
                    continue
                seen.add(candidate.url)
                candidates.append(candidate)
            if target and any(_normalized_title(item.title) == target for item in candidates):
                break
            page_size = _positive_int(paging.get("pageSize"), _PAGE_SIZE)
            if not records or len(records) < page_size:
                break
            if total_found is not None and page_no * page_size >= total_found:
                break

        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            trace={
                "adapter": self.name,
                "variant": "faceted_search_rest",
                "board_urls": [board_url],
                "api_urls": api_urls,
                "response_source": shell.source,
                "candidate_count": len(candidates),
                "pages_fetched": pages_fetched,
                "total_found": total_found,
                "inventory_scope": "title_filtered" if query.title else "full",
            },
        )


def _portal_config(html: str, expected_code: str) -> tuple[str, str, str] | None:
    text = html[:_MAX_CONFIG_CHARS]
    if not re.search(r"js/facetedsearch/FacetedSearchPage\.js", text, re.I):
        return None
    portal_match = re.search(r"\bportalNo\s*:\s*['\"]([0-9]{1,20})['\"]", text)
    code_match = re.search(r"\burlCode\s*:\s*['\"]([A-Za-z0-9_-]{1,64})['\"]", text)
    lang_match = re.search(r"\blang\s*:\s*['\"]([A-Za-z]{2}(?:-[A-Za-z]{2})?)['\"]", text)
    source_match = re.search(r"\bsrc\s*:\s*['\"]([A-Za-z0-9_-]{0,128})['\"]", text)
    if portal_match is None or code_match is None or code_match.group(1) != expected_code:
        return None
    return (
        portal_match.group(1),
        lang_match.group(1) if lang_match else "en",
        source_match.group(1) if source_match else "",
    )


def _search_payload(query: JobQuery, page_no: int) -> dict:
    return {
        "fieldData": {
            "fields": {
                "KEYWORD": (query.title or "").strip(),
                "LOCATION": (query.location or "").strip(),
            },
            "valid": True,
        },
        "filterSelectionParam": {"searchFilterSelections": []},
        "sortingSelection": {"sortBySelectionParam": 5, "ascendingSortingOrder": True},
        "multilineEnabled": False,
        "pageNo": page_no,
    }


def _candidate(record, host: str, code: str, source: str) -> JobCandidate | None:
    if not isinstance(record, dict):
        return None
    contest_no = str(record.get("contestNo") or "").strip()
    columns = record.get("column")
    if not _JOB_ID.fullmatch(contest_no) or not isinstance(columns, list) or not columns:
        return None
    title = str(columns[0] or "").strip()
    if not title:
        return None
    location = _location(columns[1] if len(columns) > 1 else None)
    detail_params = {"job": contest_no}
    if source:
        detail_params["src"] = source
    return JobCandidate(
        title=title,
        url=f"https://{host}/careersection/{code}/jobdetail.ftl?{urlencode(detail_params)}",
        provider="taleo",
        location=location,
        raw={"contest_no": contest_no, "job_id": record.get("jobId")},
    )


def _location(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = value
    if isinstance(decoded, list):
        return ", ".join(str(item).strip() for item in decoded if str(item).strip()) or None
    return str(decoded).strip() or None


def _safe_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in {None, 443} or not parsed.hostname:
        return None
    return parsed


def _board_identity(board: JobBoard) -> tuple[str, str] | None:
    parsed = _safe_url(board.url)
    match = _BOARD_PATH.fullmatch(parsed.path) if parsed else None
    if board.provider != "taleo" or match is None:
        return None
    identity = ((parsed.hostname or "").casefold(), match.group("code"))
    return identity if board.identifier == "|".join(identity) else None


def _is_expected_api(url: str, host: str) -> bool:
    parsed = _safe_url(url)
    return bool(parsed and (parsed.hostname or "").casefold() == host and parsed.path == "/careersection/rest/jobboard/searchjobs")


def _nonnegative_int(value, fallback):
    return value if isinstance(value, int) and value >= 0 else fallback


def _positive_int(value, fallback):
    return value if isinstance(value, int) and 0 < value <= 100 else fallback


def _normalized_title(title: str | None) -> str:
    return " ".join((title or "").casefold().split())


def _unsupported(board, error, rejected_url=None):
    trace = {"adapter": "taleo", "error": error}
    if rejected_url:
        trace["rejected_final_url"] = rejected_url
    return AdapterResult(provider="taleo", board=board, reason_code="PROVIDER_VARIANT_UNSUPPORTED", trace=trace)


def _fetch_failure(board, board_url, api_urls, error):
    return AdapterResult(provider="taleo", board=board, reason_code="PROVIDER_FETCH_FAILED", retryable=True, trace={"adapter": "taleo", "board_urls": [board_url], "api_urls": api_urls, "error": str(error)})


def _invalid(board, board_url, api_urls, error):
    return AdapterResult(provider="taleo", board=board, reason_code="INVALID_STRUCTURED_DATA", trace={"adapter": "taleo", "board_urls": [board_url], "api_urls": api_urls, "error": error})


ADAPTER = TaleoAdapter()
