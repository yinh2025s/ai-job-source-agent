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
_NON_PRODUCTION_LABELS = {"demo", "sandbox", "staging", "test", "testing"}


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
            replay_safe=True,
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
        if not state:
            shell_evidence = _non_production_shell_evidence(shell.html, host, domain)
            if shell_evidence:
                return _unsupported(
                    board,
                    "non-production Eightfold shell has no public inventory",
                    variant="non_production_shell",
                    board_urls=board_urls,
                    response_source=shell.source,
                    shell_evidence=shell_evidence,
                    inventory_evidence="missing_smart_apply_data",
                    production_tenant_verified=False,
                    canonical_detail_verified=False,
                )
            return _unsupported(
                board,
                "missing Eightfold smartApplyData; board inventory is not verified",
                variant="missing_public_inventory",
                board_urls=board_urls,
                response_source=shell.source,
                inventory_evidence="missing_smart_apply_data",
                production_tenant_verified=False,
                canonical_detail_verified=False,
            )
        if not _is_eightfold_state(state):
            return _invalid(board, board_urls, api_urls, "invalid Eightfold smartApplyData")

        active_domain = _resolved_state_domain(host, domain, state_domain)
        state_identity_evidence = "board_identifier"
        if active_domain is None:
            # Hosted tenants often use a customer domain that differs from the
            # tenant hostname.  A same-origin canonical opening is the public
            # evidence that binds that inventory to this particular board.
            if not _inventory_binds_board(state.get("positions"), board):
                return _unsupported(
                    board,
                    "Eightfold inventory does not verify the board tenant",
                )
            active_domain = state_domain
            state_identity_evidence = "canonical_position_url"

        candidates: list[JobCandidate] = []
        seen: set[str] = set()
        rejected_urls: list[str] = []
        total_found = _nonnegative_int(state.get("count"))
        pages_fetched = 1
        _append_candidates(state.get("positions"), board, candidates, seen, rejected_urls)
        target = _normalized_title(query.title)
        inventory_scope = "title_filtered" if query.title else "full"
        inventory_complete = False

        for page_index in range(1, _MAX_PAGES):
            if total_found is not None and page_index * _PAGE_SIZE >= total_found:
                inventory_complete = True
                break
            if total_found is None and len(state.get("positions") or []) < _PAGE_SIZE:
                inventory_complete = True
                break
            if target and any(_normalized_title(item.title) == target for item in candidates):
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
            if total_found is not None and page_index * _PAGE_SIZE + len(records) >= total_found:
                inventory_complete = True
                break
            if total_found is None and len(records) < _PAGE_SIZE:
                inventory_complete = True
                break

        if total_found is not None and len(candidates) >= total_found:
            inventory_complete = True

        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            inventory_scope=inventory_scope,
            inventory_complete=inventory_complete,
            trace={
                "adapter": self.name,
                "variant": "smart_apply_public_jobs_v2",
                "board_urls": board_urls,
                "api_urls": api_urls,
                "response_source": shell.source,
                "state_identity_evidence": state_identity_evidence,
                "candidate_count": len(candidates),
                "pages_fetched": pages_fetched,
                "total_found": total_found,
                "inventory_scope": inventory_scope,
                "inventory_complete": inventory_complete,
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


class _ShellEvidenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.active_code_id: str | None = None
        self.code_parts: dict[str, list[str]] = {}
        self.robots_noindex = False
        self.demo_asset = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "code":
            code_id = attributes.get("id", "")
            if code_id:
                self.active_code_id = code_id
                self.code_parts.setdefault(code_id, [])
        if tag.casefold() == "meta" and attributes.get("name", "").casefold() == "robots":
            directives = {item.strip().casefold() for item in attributes.get("content", "").split(",")}
            self.robots_noindex = "noindex" in directives
        if any("/images/careers/demo/" in value.casefold() for value in attributes.values()):
            self.demo_asset = True

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "code":
            self.active_code_id = None

    def handle_data(self, data: str) -> None:
        if self.active_code_id is None:
            return
        parts = self.code_parts[self.active_code_id]
        if sum(map(len, parts)) < _MAX_STATE_CHARS:
            parts.append(data)


def _smart_apply_state(html: str) -> dict:
    parser = _SmartApplyParser()
    try:
        parser.feed((html or "")[: _MAX_STATE_CHARS * 2])
        body = "".join(parser.parts)
        value = json.loads(body) if body else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _non_production_shell_evidence(html: str, host: str, identifier: str) -> list[str]:
    parser = _ShellEvidenceParser()
    try:
        parser.feed((html or "")[: _MAX_STATE_CHARS * 2])
    except (TypeError, ValueError):
        return []

    markers: list[str] = []
    if _environment_label(host.removesuffix(".eightfold.ai")):
        markers.append("host_environment_label")
    if _environment_label(identifier):
        markers.append("board_identifier_environment_label")

    pcsx_body = "".join(parser.code_parts.get("pcsx-data", []))
    try:
        pcsx_state = json.loads(pcsx_body) if pcsx_body else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        pcsx_state = {}
    pcsx_domain = str(pcsx_state.get("domain") or "") if isinstance(pcsx_state, dict) else ""
    if _environment_label(pcsx_domain):
        markers.append("embedded_domain_environment_label")
    if parser.robots_noindex:
        markers.append("robots_noindex")
    if parser.demo_asset:
        markers.append("demo_asset_path")

    has_environment_identity = any(marker.endswith("environment_label") for marker in markers)
    return markers if has_environment_identity else []


def _environment_label(value: str) -> bool:
    labels = {part for part in re.split(r"[.-]+", value.casefold()) if part}
    return bool(labels & _NON_PRODUCTION_LABELS)


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


def _inventory_binds_board(records, board: JobBoard) -> bool:
    if not isinstance(records, list):
        return False
    for record in records:
        if not isinstance(record, dict):
            continue
        detail_url = str(record.get("canonicalPositionUrl") or "").strip()
        parsed = _safe_url(detail_url)
        if parsed is None or not _DETAIL_PATH.fullmatch(parsed.path):
            continue
        if _same_origin(detail_url, board.url):
            return True
    return False


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


def _unsupported(board, error, rejected_url=None, **trace_fields):
    trace = {"adapter": "eightfold", "error": error}
    if rejected_url:
        trace["rejected_response_url"] = rejected_url
    trace.update(trace_fields)
    return AdapterResult(
        provider="eightfold",
        board=board,
        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
        inventory_complete=False,
        trace=trace,
    )


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
