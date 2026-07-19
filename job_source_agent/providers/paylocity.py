from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote, urlparse

from ..fetch_failure import project_fetch_error
from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST = "recruiting.paylocity.com"
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
_POSITIVE_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_PAGE_DATA = re.compile(r"\bwindow\.pageData\s*=\s*")
_MAX_HTML_CHARS = 2_000_000
_MAX_PAGE_DATA_CHARS = 1_000_000
_MAX_INVENTORY_JOBS = 1_000


class PaylocityAdapter:
    name = "paylocity"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _url_identity(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        identity = _url_identity(url)
        if identity is None:
            return None
        tenant, slug = identity
        return JobBoard(
            url=_board_url(tenant, slug),
            provider=self.name,
            identifier=_identifier(tenant, slug),
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identity = _board_identity(board)
        if identity is None:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="invalid Paylocity board locator",
            )
        tenant, slug = identity
        board_url = _board_url(tenant, slug)
        try:
            page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, board_url, error)

        final_url = page.final_url or page.url
        final_identity = _url_identity(final_url)
        if not _same_board_identity(identity, final_identity):
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="Paylocity board redirected outside the tenant",
                board_urls=[board_url],
                rejected_final_url=final_url,
            )

        inventory = _public_inventory(page.html, tenant)
        if inventory is None:
            return _result(
                board,
                reason_code="INVALID_STRUCTURED_DATA",
                inventory_complete=False,
                error="missing or invalid Paylocity public inventory",
                board_urls=[board_url],
                response_source=page.source,
            )
        module_id, records = inventory
        if len(records) > _MAX_INVENTORY_JOBS:
            records = records[:_MAX_INVENTORY_JOBS]
            inventory_complete = False
        else:
            inventory_complete = True

        candidates: list[JobCandidate] = []
        seen: set[str] = set()
        for record in records:
            if isinstance(record, dict) and record.get("IsInternal") is True:
                continue
            candidate = _candidate(record, module_id)
            if candidate is None or candidate.url in seen:
                return _result(
                    board,
                    reason_code="INVALID_STRUCTURED_DATA",
                    inventory_complete=False,
                    error="Paylocity inventory contained an invalid opening",
                    board_urls=[board_url],
                    response_source=page.source,
                )
            seen.add(candidate.url)
            candidates.append(candidate)

        target = _normalized(query.title)
        candidates = [
            candidate for candidate in candidates if _matches_query(candidate, query)
        ]
        inventory_scope = "title_filtered" if query.title else "full"
        return _result(
            board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            inventory_complete=inventory_complete,
            board_urls=[board_url],
            response_source=page.source,
            variant="public_page_data_inventory",
            module_id=module_id,
            records_seen=len(records),
            pages_fetched=1,
            pagination_limit=_MAX_INVENTORY_JOBS,
            exact_title_found=bool(
                target and any(_normalized(candidate.title) == target for candidate in candidates)
            ),
            inventory_scope=inventory_scope,
        )


def _url_identity(url: str) -> tuple[str, str | None] | None:
    parsed = _safe_url(url)
    if parsed is None or parsed.query:
        return None
    if unquote(parsed.path) != parsed.path or "//" in parsed.path:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) not in {4, 5} or [part.casefold() for part in parts[:3]] != [
        "recruiting",
        "jobs",
        "all",
    ]:
        return None
    tenant = _uuid(parts[3])
    slug = parts[4] if len(parts) == 5 else None
    return (
        (tenant, slug)
        if tenant and (slug is None or _SLUG.fullmatch(slug))
        else None
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


def _uuid(value: object) -> str | None:
    return value.casefold() if isinstance(value, str) and _UUID.fullmatch(value) else None


def _identifier(tenant: str, slug: str | None) -> str:
    return tenant if slug is None else f"{tenant}|{slug.casefold()}"


def _board_url(tenant: str, slug: str | None) -> str:
    suffix = f"/{slug}" if slug is not None else ""
    return f"https://{_HOST}/recruiting/jobs/All/{tenant}{suffix}"


def _board_identity(board: JobBoard) -> tuple[str, str | None] | None:
    if board.provider != "paylocity" or not isinstance(board.identifier, str):
        return None
    identity = _url_identity(board.url)
    if identity is None or board.identifier != _identifier(*identity):
        return None
    return identity


def _same_board_identity(
    requested: tuple[str, str | None],
    final: tuple[str, str | None] | None,
) -> bool:
    if final is None or requested[0] != final[0]:
        return False
    requested_slug, final_slug = requested[1], final[1]
    return requested_slug is None or (
        final_slug is not None and requested_slug.casefold() == final_slug.casefold()
    )


def _public_inventory(html: str, tenant: str) -> tuple[str, list[Any]] | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    matches = list(_PAGE_DATA.finditer(html))
    if len(matches) != 1:
        return None
    match = matches[0]
    encoded = html[match.end() :]
    leading_chars = len(encoded) - len(encoded.lstrip())
    encoded = encoded[leading_chars : _MAX_PAGE_DATA_CHARS + leading_chars + 1]
    try:
        payload, end = json.JSONDecoder().raw_decode(encoded)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if end > _MAX_PAGE_DATA_CHARS or not encoded[end:].lstrip().startswith(";"):
        return None
    if not isinstance(payload, dict):
        return None
    module_id = payload.get("ModuleId")
    jobs = payload.get("Jobs")
    lead_join_url = payload.get("LeadJoinUrl")
    if (
        not isinstance(module_id, str)
        or not _POSITIVE_ID.fullmatch(module_id)
        or not isinstance(jobs, list)
        or _lead_join_tenant(lead_join_url) != tenant
    ):
        return None
    return module_id, jobs


def _lead_join_tenant(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    parsed = _safe_lead_join_url(url)
    if parsed is None or unquote(parsed.path) != parsed.path:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 4 or [part.casefold() for part in parts[:3]] != [
        "recruiting",
        "publicleads",
        "new",
    ]:
        return None
    return _uuid(parts[3])


def _safe_lead_join_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        return None
    if parsed.scheme or parsed.netloc:
        if (
            parsed.scheme.casefold() != "https"
            or (parsed.hostname or "").casefold() != _HOST
            or port not in {None, 443}
        ):
            return None
    elif not url.startswith("/") or url.startswith("//"):
        return None
    return parsed


def _candidate(record: Any, module_id: str) -> JobCandidate | None:
    if not isinstance(record, dict):
        return None
    raw_job_id = record.get("JobId")
    title = record.get("JobTitle")
    if (
        isinstance(raw_job_id, bool)
        or not isinstance(raw_job_id, int)
        or raw_job_id <= 0
        or not isinstance(title, str)
        or not title.strip()
        or len(title) > 500
    ):
        return None
    location = _location(record, module_id)
    if location is None:
        return None
    return JobCandidate(
        title=title.strip(),
        url=f"https://{_HOST}/Recruiting/Jobs/Details/{raw_job_id}",
        provider="paylocity",
        location=location,
        raw={
            "job_id": str(raw_job_id),
            "module_id": module_id,
            "is_remote": record.get("IsRemote") is True,
        },
    )


def _location(record: dict[str, Any], module_id: str) -> str | None:
    job_location = record.get("JobLocation")
    if job_location is not None and (
        not isinstance(job_location, dict)
        or str(job_location.get("ModuleId")) != module_id
    ):
        return None
    options: list[str] = []
    if record.get("IsRemote") is True:
        options.append("Remote")
    value = record.get("LocationName")
    if isinstance(value, str) and value.strip() and len(value) <= 500:
        options.append(value.strip())
    if job_location is not None:
        parts = [job_location.get(key) for key in ("City", "State", "Country")]
        values = [
            part.strip()
            for part in parts
            if isinstance(part, str) and part.strip()
        ]
        structured = ", ".join(values)
        if structured and _normalized(structured) not in {
            _normalized(option) for option in options
        }:
            options.append(structured)
    return "; ".join(options) if options else None


def _normalized(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if value else ""


def _matches_query(candidate: JobCandidate, query: JobQuery) -> bool:
    title = _normalized(query.title)
    return not title or title in _normalized(candidate.title)


def _fetch_failure(board: JobBoard, board_url: str, error: Exception) -> AdapterResult:
    if isinstance(error, FetchError):
        projection = project_fetch_error(error)
        reason_code = projection["reason_code"]
        retryable = projection["retryable"]
    else:
        reason_code = "PROVIDER_FETCH_FAILED"
        retryable = True
    return _result(
        board,
        reason_code=reason_code,
        retryable=retryable,
        inventory_complete=False,
        error=str(error),
        board_urls=[board_url],
    )


def _result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None = None,
    retryable: bool = False,
    inventory_complete: bool,
    error: str | None = None,
    inventory_scope: str = "full",
    **trace: Any,
) -> AdapterResult:
    trace.update(
        {
            "adapter": "paylocity",
            "inventory_scope": inventory_scope,
            "inventory_complete": inventory_complete,
        }
    )
    if error is not None:
        trace["error"] = error
    return AdapterResult(
        provider="paylocity",
        board=board,
        candidates=candidates or [],
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope=inventory_scope,
        inventory_complete=inventory_complete,
        trace=trace,
    )


ADAPTER = PaylocityAdapter()
