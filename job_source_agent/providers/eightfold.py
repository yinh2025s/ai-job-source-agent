from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_CAREERS_PATH = re.compile(r"^/careers/?$")
_DETAIL_PATH = re.compile(r"^/careers/job/(?P<job_id>[0-9]{1,24})/?$")
_DOMAIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_MAX_STATE_CHARS = 2_000_000
_MAX_PAGES = 5
_PAGE_SIZE = 10


class EightfoldAdapter:
    name = "eightfold"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        parsed = _safe_url(url)
        if parsed is None or not (parsed.hostname or "").casefold().endswith(".eightfold.ai"):
            return False
        return bool(_CAREERS_PATH.fullmatch(parsed.path) or _DETAIL_PATH.fullmatch(parsed.path))

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        parsed = _safe_url(url)
        if parsed is None:
            return None
        host = (parsed.hostname or "").casefold()
        tenant = host.removesuffix(".eightfold.ai")
        return JobBoard(
            url=f"https://{host}/careers",
            provider=self.name,
            identifier=tenant,
        )

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        parsed = _safe_url(page_url)
        if parsed is None or not _CAREERS_PATH.fullmatch(parsed.path):
            return None
        state = _smart_apply_state(page.html)
        if not _is_eightfold_state(state):
            return None
        domain = str(state.get("domain") or "").strip().casefold()
        return JobBoard(
            url=f"https://{(parsed.hostname or '').casefold()}/careers",
            provider=self.name,
            identifier=domain,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identity = _board_identity(board)
        if identity is None:
            return _unsupported(board, "invalid Eightfold board")
        host, domain = identity
        search_url = _search_url(board.url, query)
        board_urls = [search_url]
        api_urls: list[str] = []
        try:
            shell = fetcher.fetch(search_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, board_urls, api_urls, error)
        if not _same_origin(shell.final_url or shell.url, board.url):
            return _unsupported(board, "Eightfold board redirected outside the tenant", shell.final_url or shell.url)

        state = _smart_apply_state(shell.html)
        state_domain = str(state.get("domain") or "").strip().casefold()
        active_domain = _resolved_state_domain(host, domain, state_domain)
        if not _is_eightfold_state(state) or active_domain is None:
            return _invalid(board, board_urls, api_urls, "missing or mismatched Eightfold smartApplyData")

        candidates: list[JobCandidate] = []
        seen: set[str] = set()
        rejected_urls: list[str] = []
        total_found = _nonnegative_int(state.get("count"))
        pages_fetched = 1
        _append_candidates(state.get("positions"), board, candidates, seen, rejected_urls)
        target = _normalized_title(query.title)

        for page_index in range(1, _MAX_PAGES):
            if target and any(_normalized_title(item.title) == target for item in candidates):
                break
            if total_found is not None and page_index * _PAGE_SIZE >= total_found:
                break
            if len(state.get("positions") or []) < _PAGE_SIZE:
                break
            api_url = _api_url(board.url, active_domain, query, page_index * _PAGE_SIZE)
            api_urls.append(api_url)
            try:
                response = fetcher.fetch(
                    api_url,
                    headers={"Accept": "application/json", "Referer": search_url},
                )
            except (FetchError, OSError, TimeoutError) as error:
                if candidates:
                    break
                return _fetch_failure(board, board_urls, api_urls, error)
            if not _same_api(response.final_url or response.url, host):
                return _unsupported(board, "Eightfold API redirected outside the tenant", response.final_url or response.url)
            try:
                state = json.loads(response.html)
            except (json.JSONDecodeError, TypeError):
                return _invalid(board, board_urls, api_urls, "invalid Eightfold jobs response")
            if not _is_inventory(state) or str(state.get("domain") or "").casefold() != active_domain:
                return _invalid(board, board_urls, api_urls, "missing or mismatched Eightfold inventory")
            pages_fetched += 1
            total_found = _nonnegative_int(state.get("count"), total_found)
            records = state.get("positions")
            _append_candidates(records, board, candidates, seen, rejected_urls)
            if len(records) < _PAGE_SIZE:
                break

        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            trace={
                "adapter": self.name,
                "variant": "smart_apply_public_jobs_v2",
                "board_urls": board_urls,
                "api_urls": api_urls,
                "response_source": shell.source,
                "candidate_count": len(candidates),
                "pages_fetched": pages_fetched,
                "total_found": total_found,
                "inventory_scope": "title_filtered" if query.title else "full",
                "rejected_job_urls": list(dict.fromkeys(rejected_urls)),
            },
        )


class _SmartApplyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.active = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "code" and dict(attrs).get("id") == "smartApplyData":
            self.active = True

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "code" and self.active:
            self.active = False

    def handle_data(self, data: str) -> None:
        if self.active and sum(map(len, self.parts)) < _MAX_STATE_CHARS:
            self.parts.append(data)


def _smart_apply_state(html: str) -> dict:
    parser = _SmartApplyParser()
    try:
        parser.feed((html or "")[: _MAX_STATE_CHARS * 2])
        body = "".join(parser.parts)
        value = json.loads(body) if body else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _is_eightfold_state(state: dict) -> bool:
    return (
        _is_inventory(state)
        and bool(state.get("isPcsEnabled") is True or state.get("pcsOctupleMigration0Enabled") is True)
        and bool(_DOMAIN.fullmatch(str(state.get("domain") or "")))
    )


def _is_inventory(state) -> bool:
    return (
        isinstance(state, dict)
        and isinstance(state.get("positions"), list)
        and _nonnegative_int(state.get("count")) is not None
    )


def _append_candidates(records, board, candidates, seen, rejected_urls) -> None:
    if not isinstance(records, list):
        return
    for record in records:
        candidate = _candidate(record, board)
        if candidate is None:
            if isinstance(record, dict):
                rejected_urls.append(str(record.get("canonicalPositionUrl") or record.get("id") or ""))
            continue
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        candidates.append(candidate)


def _candidate(record, board: JobBoard) -> JobCandidate | None:
    if not isinstance(record, dict):
        return None
    title = str(record.get("posting_name") or record.get("name") or "").strip()
    job_id = str(record.get("id") or "").strip()
    detail_url = str(record.get("canonicalPositionUrl") or "").strip()
    parsed = _safe_url(detail_url)
    match = _DETAIL_PATH.fullmatch(parsed.path) if parsed else None
    if (
        not title
        or match is None
        or match.group("job_id") != job_id
        or not _same_origin(detail_url, board.url)
    ):
        return None
    locations = record.get("locations")
    location = ", ".join(str(item).strip() for item in locations if str(item).strip()) if isinstance(locations, list) else ""
    location = location or str(record.get("location") or "").strip()
    return JobCandidate(
        title=title,
        url=urlunparse(parsed._replace(query="", fragment="")),
        provider="eightfold",
        location=location or None,
        raw={
            "job_id": job_id,
            "ats_job_id": record.get("ats_job_id"),
            "department": record.get("department"),
        },
    )


def _search_url(board_url: str, query: JobQuery) -> str:
    parsed = urlparse(board_url)
    params = []
    if query.title:
        params.append(("query", query.title.strip()))
    if query.location:
        params.append(("location", query.location.strip()))
    return urlunparse(parsed._replace(query=urlencode(params), fragment=""))


def _api_url(board_url: str, domain: str, query: JobQuery, start: int) -> str:
    parsed = urlparse(board_url)
    params = [("domain", domain), ("start", str(start)), ("num", str(_PAGE_SIZE))]
    if query.title:
        params.append(("query", query.title.strip()))
    if query.location:
        params.append(("location", query.location.strip()))
    return urlunparse(parsed._replace(path="/api/apply/v2/jobs", query=urlencode(params), fragment=""))


def _board_identity(board: JobBoard) -> tuple[str, str] | None:
    parsed = _safe_url(board.url)
    domain = (board.identifier or "").strip().casefold()
    if (
        board.provider != "eightfold"
        or parsed is None
        or not _CAREERS_PATH.fullmatch(parsed.path)
        or not _DOMAIN.fullmatch(domain)
    ):
        return None
    return (parsed.hostname or "").casefold(), domain


def _resolved_state_domain(host: str, identifier: str, state_domain: str) -> str | None:
    if state_domain == identifier:
        return state_domain
    if host.endswith(".eightfold.ai") and state_domain.split(".", 1)[0] == identifier:
        return state_domain
    return None


def _safe_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in {None, 443} or not parsed.hostname:
        return None
    return parsed


def _same_origin(left: str, right: str) -> bool:
    left_parsed = _safe_url(left)
    right_parsed = _safe_url(right)
    return bool(left_parsed and right_parsed and left_parsed.hostname.casefold() == right_parsed.hostname.casefold())


def _same_api(url: str, host: str) -> bool:
    parsed = _safe_url(url)
    return bool(parsed and parsed.hostname.casefold() == host and parsed.path == "/api/apply/v2/jobs")


def _normalized_title(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _nonnegative_int(value, fallback=None):
    if isinstance(value, bool):
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback


def _unsupported(board, error, rejected_url=None):
    trace = {"adapter": "eightfold", "error": error}
    if rejected_url:
        trace["rejected_response_url"] = rejected_url
    return AdapterResult(provider="eightfold", board=board, reason_code="PROVIDER_VARIANT_UNSUPPORTED", trace=trace)


def _fetch_failure(board, board_urls, api_urls, error):
    return AdapterResult(
        provider="eightfold",
        board=board,
        reason_code="PROVIDER_FETCH_FAILED",
        retryable=True,
        trace={"adapter": "eightfold", "board_urls": board_urls, "api_urls": api_urls, "error": str(error)},
    )


def _invalid(board, board_urls, api_urls, error):
    return AdapterResult(
        provider="eightfold",
        board=board,
        reason_code="INVALID_STRUCTURED_DATA",
        trace={"adapter": "eightfold", "board_urls": board_urls, "api_urls": api_urls, "error": error},
    )


ADAPTER = EightfoldAdapter()
