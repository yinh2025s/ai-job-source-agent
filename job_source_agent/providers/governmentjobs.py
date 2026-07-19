from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from urllib.parse import unquote, urlencode, urlparse

from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST = "www.governmentjobs.com"
_TENANT = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_BOARD_PATH = re.compile(r"^/careers/(?P<tenant>[a-z0-9-]+)/?$", re.I)
_DETAIL_PATH = re.compile(
    r"^/careers/(?P<tenant>[a-z0-9-]+)/jobs/(?P<job_id>[1-9][0-9]{0,19})"
    r"(?:-(?P<variant>[0-9]+))?/(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)/?$",
    re.I,
)
_TOTAL = re.compile(r"\b([0-9]{1,6})\s+jobs?\s+found\b", re.I)
_PAGE_TENANT = re.compile(
    r"<html\b[^>]*\bdata-agency-folder-name\s*=\s*(['\"])(?P<tenant>[a-z0-9-]+)\1",
    re.I,
)
_MAX_RESPONSE_CHARS = 5_000_000
_MAX_JOBS = 2_000


class GovernmentJobsAdapter:
    name = "governmentjobs"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _url_tenant(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        tenant = _url_tenant(url)
        return _job_board(tenant) if tenant is not None else None

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        tenant = _board_tenant(board)
        if tenant is None:
            return _failure(board, "PROVIDER_VARIANT_UNSUPPORTED", "invalid_board_identity")

        board_url = _board_url(tenant)
        inventory_url = board_url + "?" + urlencode({"sort": "PositionTitle|Ascending"})
        try:
            board_page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, error, board_url)
        if (
            _response_tenant(board_page.final_url or board_page.url) != tenant
            or _page_tenant(board_page.html) != tenant
        ):
            return _failure(
                board,
                "PROVIDER_VARIANT_UNSUPPORTED",
                "missing_or_cross_tenant_page_identity",
                inventory_url=inventory_url,
                response_source=board_page.source,
                rejected_final_url=board_page.final_url or board_page.url,
            )
        try:
            page = fetcher.fetch(
                inventory_url,
                headers={
                    "Accept": "application/json, text/html;q=0.9, */*;q=0.1",
                    "Referer": board_url,
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, error, inventory_url)

        final_url = page.final_url or page.url
        if _response_tenant(final_url) != tenant:
            return _failure(
                board,
                "PROVIDER_VARIANT_UNSUPPORTED",
                "cross_tenant_or_unsafe_response",
                inventory_url=inventory_url,
                response_source=page.source,
                rejected_final_url=final_url,
            )

        parsed = _parse_inventory(page.html, tenant)
        if isinstance(parsed, str):
            if parsed == "inventory_cap_exceeded":
                code = "FETCH_BUDGET_EXHAUSTED"
            elif parsed == "javascript_inventory_shell":
                code = "PROVIDER_VARIANT_UNSUPPORTED"
            else:
                code = "INVALID_STRUCTURED_DATA"
            return _failure(
                board,
                code,
                parsed,
                retryable=reason_spec(code).retryable,
                inventory_url=inventory_url,
                response_source=page.source,
            )
        candidates, total, variant = parsed
        title = _normalize(query.title)
        location = _normalize(query.location)
        visible_candidates = [
            candidate
            for candidate in candidates
            if not title or title in _normalize(candidate.title)
        ]
        inventory_scope = "title_filtered" if title else "full"
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=visible_candidates,
            reason_code="EMPTY_PROVIDER_RESPONSE" if not visible_candidates else None,
            inventory_scope=inventory_scope,
            inventory_complete=True,
            trace={
                "adapter": self.name,
                "variant": variant,
                "tenant": tenant,
                "board_urls": [board_url],
                "api_urls": [inventory_url],
                "response_source": page.source,
                "records_seen": len(candidates),
                "total": total,
                "candidate_count": len(visible_candidates),
                "exact_title_found": bool(
                    title
                    and any(
                        _normalize(candidate.title) == title
                        for candidate in visible_candidates
                    )
                ),
                "location_match_found": bool(
                    location
                    and any(
                        location in _normalize(candidate.location)
                        for candidate in visible_candidates
                    )
                ),
                "inventory_scope": inventory_scope,
                "inventory_complete": True,
            },
        )


def _safe_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != _HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None
    return parsed


def _normalized_path(path: str) -> str:
    return "/" + "/".join(part for part in unquote(path).split("/") if part)


def _path_tenant(path: str) -> str | None:
    normalized = _normalized_path(path)
    match = _BOARD_PATH.fullmatch(normalized) or _DETAIL_PATH.fullmatch(normalized)
    if match is None:
        return None
    tenant = match.group("tenant").casefold()
    return tenant if _TENANT.fullmatch(tenant) else None


def _url_tenant(url: str) -> str | None:
    parsed = _safe_url(url)
    return _path_tenant(parsed.path) if parsed is not None else None


def _response_tenant(url: str) -> str | None:
    parsed = _safe_url(url)
    if parsed is None:
        return None
    match = _BOARD_PATH.fullmatch(_normalized_path(parsed.path))
    if match is None:
        return None
    tenant = match.group("tenant").casefold()
    return tenant if _TENANT.fullmatch(tenant) else None


def _board_url(tenant: str) -> str:
    return f"https://{_HOST}/careers/{tenant}"


def _job_board(tenant: str) -> JobBoard:
    return JobBoard(url=_board_url(tenant), provider="governmentjobs", identifier=tenant)


def _board_tenant(board: JobBoard) -> str | None:
    if (
        board.provider != "governmentjobs"
        or not isinstance(board.identifier, str)
        or not _TENANT.fullmatch(board.identifier)
    ):
        return None
    tenant = board.identifier.casefold()
    return tenant if board.url == _board_url(tenant) else None


def _page_tenant(raw: str) -> str | None:
    if not isinstance(raw, str) or len(raw) > _MAX_RESPONSE_CHARS:
        return None
    tenants = {match.group("tenant").casefold() for match in _PAGE_TENANT.finditer(raw)}
    return tenants.pop() if len(tenants) == 1 else None


class _InventoryHTMLParser(HTMLParser):
    def __init__(self, tenant: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tenant = tenant
        self.rows: list[dict[str, str | None]] = []
        self._anchor: dict[str, str | None] | None = None
        self._anchor_parts: list[str] = []
        self._location_depth = 0
        self._location_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): value for name, value in attrs if value is not None}
        classes = set(values.get("class", "").casefold().split())
        if tag.casefold() == "a" and self._anchor is None:
            href = values.get("href")
            detail = _detail_identity(href, self.tenant) if href else None
            if detail is not None:
                self._anchor = {"url": detail[0], "job_id": detail[1], "location": None}
                self._anchor_parts = []
        if self._anchor is not None and any("location" in value for value in classes):
            self._location_depth = 1
            self._location_parts = []
        elif self._location_depth:
            self._location_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._location_depth:
            self._location_depth -= 1
            if self._location_depth == 0 and self._anchor is not None:
                location = " ".join(" ".join(self._location_parts).split())
                self._anchor["location"] = location or None
        if tag.casefold() == "a" and self._anchor is not None:
            title = " ".join(" ".join(self._anchor_parts).split())
            self._anchor["title"] = title
            self.rows.append(self._anchor)
            self._anchor = None
            self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._anchor is not None:
            self._anchor_parts.append(data)
        if self._location_depth:
            self._location_parts.append(data)


def _parse_inventory(raw: str, tenant: str):
    if not isinstance(raw, str) or len(raw) > _MAX_RESPONSE_CHARS:
        return "inventory_cap_exceeded"
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = None
    if payload is not None:
        return _parse_json_inventory(payload, tenant)
    return _parse_html_inventory(raw, tenant)


def _parse_json_inventory(payload, tenant: str):
    if not isinstance(payload, dict):
        return "invalid_inventory_envelope"
    rows = next(
        (payload[key] for key in ("jobs", "Jobs", "items", "Items", "results", "Results") if isinstance(payload.get(key), list)),
        None,
    )
    total = next(
        (payload[key] for key in ("total", "Total", "totalCount", "TotalCount", "count", "Count") if key in payload),
        None,
    )
    if rows is None or isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return "invalid_inventory_envelope"
    if total > _MAX_JOBS:
        return "inventory_cap_exceeded"
    if total != len(rows):
        return "invalid_inventory_envelope"
    candidates = []
    for row in rows:
        candidate = _json_candidate(row, tenant)
        if candidate is None:
            return "invalid_or_cross_tenant_record"
        candidates.append(candidate)
    if len({candidate.raw["job_id"] for candidate in candidates}) != len(candidates):
        return "duplicate_job_id"
    return candidates, total, "governmentjobs_public_xhr_json"


def _json_candidate(row, tenant: str) -> JobCandidate | None:
    if not isinstance(row, dict):
        return None
    job_id = row.get("jobId", row.get("JobId", row.get("id", row.get("Id"))))
    title = row.get("title", row.get("Title", row.get("jobTitle", row.get("JobTitle"))))
    location = row.get("location", row.get("Location"))
    url = row.get("url", row.get("Url", row.get("jobUrl", row.get("JobUrl"))))
    if not isinstance(title, str) or not title.strip() or len(title) > 500 or not isinstance(url, str):
        return None
    detail = _detail_identity(url, tenant)
    if detail is None or str(job_id) != detail[1]:
        return None
    if location is not None and (not isinstance(location, str) or len(location) > 500):
        return None
    return JobCandidate(
        title=" ".join(title.split()),
        url=detail[0],
        provider="governmentjobs",
        location=" ".join(location.split()) if isinstance(location, str) and location.strip() else None,
        raw={"job_id": detail[1], "tenant": tenant},
    )


def _parse_html_inventory(raw: str, tenant: str):
    if 'id="job-list-container"' in raw or "id='job-list-container'" in raw:
        return "javascript_inventory_shell"
    parser = _InventoryHTMLParser(tenant)
    try:
        parser.feed(raw)
        parser.close()
    except (TypeError, ValueError):
        return "malformed_inventory_html"
    text = " ".join(" ".join(parser.text_parts).split())
    totals = {int(value) for value in _TOTAL.findall(text)}
    if len(totals) != 1:
        return "missing_or_contradictory_total"
    total = totals.pop()
    if total > _MAX_JOBS:
        return "inventory_cap_exceeded"
    candidates = []
    for row in parser.rows:
        title = row.get("title")
        if not isinstance(title, str) or not title or len(title) > 500:
            return "invalid_inventory_record"
        candidates.append(
            JobCandidate(
                title=title,
                url=str(row["url"]),
                provider="governmentjobs",
                location=row.get("location"),
                raw={"job_id": str(row["job_id"]), "tenant": tenant},
            )
        )
    if len(candidates) != total or len({c.raw["job_id"] for c in candidates}) != total:
        return "inventory_count_mismatch"
    return candidates, total, "governmentjobs_public_xhr_html"


def _detail_identity(url: str, tenant: str) -> tuple[str, str] | None:
    if url.startswith("http"):
        parsed = _safe_url(url)
    elif url.startswith("/") and not url.startswith("//"):
        parsed = urlparse(url)
    else:
        return None
    if parsed is None or parsed.query or parsed.fragment:
        return None
    match = _DETAIL_PATH.fullmatch(_normalized_path(parsed.path))
    if match is None or match.group("tenant").casefold() != tenant:
        return None
    job_id = match.group("job_id")
    variant = f"-{match.group('variant')}" if match.group("variant") else ""
    slug = match.group("slug").casefold()
    return f"https://{_HOST}/careers/{tenant}/jobs/{job_id}{variant}/{slug}", job_id


def _normalize(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if value else ""


def _failure(
    board: JobBoard,
    reason_code: str,
    stop_reason: str,
    *,
    retryable: bool = False,
    inventory_url: str | None = None,
    response_source: str | None = None,
    rejected_final_url: str | None = None,
) -> AdapterResult:
    trace = {
        "adapter": "governmentjobs",
        "variant": "governmentjobs_public_xhr",
        "api_urls": [inventory_url] if inventory_url else [],
        "stop_reason": stop_reason,
        "inventory_scope": "unknown",
        "inventory_complete": False,
        "exposed_candidate_count": 0,
    }
    if response_source is not None:
        trace["response_source"] = response_source
    if rejected_final_url is not None:
        trace["rejected_final_url"] = rejected_final_url
    return AdapterResult(
        provider="governmentjobs",
        board=board,
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope="unknown",
        inventory_complete=False,
        trace=trace,
    )


def _fetch_failure(board: JobBoard, error: Exception, url: str) -> AdapterResult:
    reason = classify_fetch_error(str(error))
    if reason == "FETCH_FAILED":
        reason = "PROVIDER_FETCH_FAILED"
    return _failure(
        board,
        reason,
        "inventory_fetch_failed",
        retryable=reason_spec(reason).retryable,
        inventory_url=url,
    )


ADAPTER = GovernmentJobsAdapter()
