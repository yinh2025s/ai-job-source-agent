from __future__ import annotations

import json
import math
import re
from typing import Any
from urllib.parse import unquote, urlencode, urlparse

from ..fetch_failure import project_fetch_error
from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST_SUFFIX = ".hcshiring.com"
_TENANT = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_RELEASE = re.compile(r"^[a-f0-9]{8,16}$", re.I)
_VERSION_DECLARATION = re.compile(
    r"\bvar\s+V\s*=\s*\{\s*version\s*:\s*(['\"])(?P<version>[a-f0-9]{8,16})\1",
    re.I,
)
_MAX_HTML_CHARS = 2_000_000
_MAX_INVENTORY_CHARS = 5_000_000
_MAX_JOBS = 1_000
_MAX_PAGES = 100
_MAX_FIELD_CHARS = 500


class HealthcareSourceAdapter:
    name = "healthcaresource"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _url_tenant(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        tenant = _url_tenant(url)
        return _job_board(tenant) if tenant is not None else None

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        tenant = _board_tenant(board)
        if tenant is None:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="invalid HealthcareSource board locator",
            )

        board_url = _board_url(tenant)
        try:
            page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, error, board_urls=[board_url])

        final_url = page.final_url or page.url
        if _listing_tenant(final_url) != tenant:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="HealthcareSource board redirected outside the declared tenant",
                board_urls=[board_url],
                rejected_final_url=final_url,
            )

        release = _shell_release(page.html)
        if release is None:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="missing HealthcareSource public applicant shell evidence",
                board_urls=[board_url],
                response_source=page.source,
            )

        search_title = _search_title(query.title)
        inventory_scope = "title_filtered" if search_title else "full"
        candidates: list[JobCandidate] = []
        seen_ids: set[str] = set()
        api_urls: list[str] = []
        expected_meta: tuple[int, int, int] | None = None

        for page_number in range(1, _MAX_PAGES + 1):
            inventory_url = _inventory_url(
                tenant,
                release,
                page_number,
                search_title=search_title,
            )
            api_urls.append(inventory_url)
            try:
                inventory_page = fetcher.fetch(
                    inventory_url,
                    headers={"Accept": "application/json", "Referer": board_url},
                )
            except (FetchError, OSError, TimeoutError) as error:
                return _fetch_failure(
                    board,
                    error,
                    inventory_scope=inventory_scope,
                    board_urls=[board_url],
                    api_urls=api_urls,
                    response_source=page.source,
                )

            response_url = inventory_page.final_url or inventory_page.url
            if not _same_inventory_url(response_url, inventory_url):
                return _result(
                    board,
                    reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                    inventory_complete=False,
                    inventory_scope=inventory_scope,
                    error=(
                        "HealthcareSource inventory redirected outside the "
                        "verified tenant endpoint"
                    ),
                    board_urls=[board_url],
                    api_urls=api_urls,
                    rejected_final_url=response_url,
                )

            parsed = _parse_inventory(inventory_page.html, page_number)
            if parsed is None:
                return _invalid_inventory(
                    board,
                    board_url,
                    api_urls,
                    inventory_page.source,
                    "invalid HealthcareSource public inventory",
                    inventory_scope=inventory_scope,
                )
            records, per_page, total_jobs, total_pages = parsed
            meta = per_page, total_jobs, total_pages
            if expected_meta is None:
                expected_meta = meta
                if total_jobs > _MAX_JOBS or total_pages > _MAX_PAGES:
                    return _result(
                        board,
                        candidates=candidates,
                        reason_code="OPENING_DISCOVERY_INCOMPLETE",
                        inventory_complete=False,
                        inventory_scope=inventory_scope,
                        error="HealthcareSource inventory exceeded the pagination limit",
                        board_urls=[board_url],
                        api_urls=api_urls,
                        total=total_jobs,
                        pagination_limit=_MAX_JOBS,
                    )
            elif meta != expected_meta:
                return _invalid_inventory(
                    board,
                    board_url,
                    api_urls,
                    inventory_page.source,
                    "HealthcareSource inventory metadata changed during pagination",
                    inventory_scope=inventory_scope,
                )

            for record in records:
                candidate = _candidate(record, tenant)
                if candidate is None:
                    return _invalid_inventory(
                        board,
                        board_url,
                        api_urls,
                        inventory_page.source,
                        "HealthcareSource inventory contained an invalid opening",
                        inventory_scope=inventory_scope,
                    )
                job_id = candidate.raw["job_id"]
                if job_id in seen_ids:
                    return _invalid_inventory(
                        board,
                        board_url,
                        api_urls,
                        inventory_page.source,
                        "HealthcareSource inventory contained a duplicate opening",
                        inventory_scope=inventory_scope,
                    )
                seen_ids.add(job_id)
                candidates.append(candidate)

            if page_number >= total_pages:
                if len(candidates) != total_jobs:
                    return _invalid_inventory(
                        board,
                        board_url,
                        api_urls,
                        inventory_page.source,
                        "HealthcareSource inventory count contradicted its metadata",
                        inventory_scope=inventory_scope,
                    )
                break
        else:
            return _result(
                board,
                candidates=candidates,
                reason_code="OPENING_DISCOVERY_INCOMPLETE",
                inventory_complete=False,
                inventory_scope=inventory_scope,
                error="HealthcareSource pagination did not terminate",
                board_urls=[board_url],
                api_urls=api_urls,
                pagination_limit=_MAX_JOBS,
            )

        target = _normalized(search_title)
        return _result(
            board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            inventory_scope=inventory_scope,
            board_urls=[board_url],
            api_urls=api_urls,
            response_source=page.source,
            variant="hcshiring_public_api",
            identity={"tenant": tenant, "release": release},
            records_seen=len(candidates),
            total=len(candidates),
            pages_fetched=len(api_urls),
            pagination_limit=_MAX_JOBS,
            exact_title_found=bool(
                target and any(_normalized(candidate.title) == target for candidate in candidates)
            ),
        )


def _safe_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or not host.endswith(_HOST_SUFFIX)
    ):
        return None
    tenant = host[: -len(_HOST_SUFFIX)]
    return (parsed, tenant) if _TENANT.fullmatch(tenant) else None


def _url_tenant(url: str) -> str | None:
    parsed_tenant = _safe_url(url)
    if parsed_tenant is None:
        return None
    parsed, tenant = parsed_tenant
    if parsed.query or unquote(parsed.path) != parsed.path or "//" in parsed.path:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return tenant
    if len(parts) == 1 and parts[0].casefold() == "jobs":
        return tenant
    if len(parts) == 2 and parts[0].casefold() == "jobs" and _JOB_ID.fullmatch(parts[1]):
        return tenant
    return None


def _listing_tenant(url: str) -> str | None:
    parsed_tenant = _safe_url(url)
    if parsed_tenant is None:
        return None
    parsed, tenant = parsed_tenant
    if parsed.query or unquote(parsed.path) != parsed.path or "//" in parsed.path:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    return tenant if not parts or (len(parts) == 1 and parts[0].casefold() == "jobs") else None


def _board_url(tenant: str) -> str:
    return f"https://{tenant}{_HOST_SUFFIX}/jobs"


def _job_board(tenant: str) -> JobBoard:
    return JobBoard(
        url=_board_url(tenant),
        provider="healthcaresource",
        identifier=tenant,
        replay_safe=True,
    )


def _board_tenant(board: JobBoard) -> str | None:
    if (
        board.provider != "healthcaresource"
        or not isinstance(board.identifier, str)
        or not _TENANT.fullmatch(board.identifier)
    ):
        return None
    tenant = board.identifier.casefold()
    return tenant if board.url == _board_url(tenant) else None


def _shell_release(html: str) -> str | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    matches = list(_VERSION_DECLARATION.finditer(html))
    if len(matches) != 1:
        return None
    release = matches[0].group("version").casefold()
    if _RELEASE.fullmatch(release) is None:
        return None
    folded = html.casefold()
    markers = (
        f'href="/{release}/scss/',
        "cdn.healthcaresource.com/assets/applicant/",
        "applicant-cli-",
        'id="rootdiv"',
    )
    return release if all(marker in folded for marker in markers) else None


def _inventory_url(
    tenant: str,
    release: str,
    page: int,
    *,
    search_title: str | None = None,
) -> str:
    params: dict[str, object] = {"page": page}
    if search_title:
        params["job"] = search_title
    return (
        f"https://{tenant}{_HOST_SUFFIX}/{release}/api/jobs?"
        f"{urlencode(params)}"
    )


def _same_inventory_url(actual_url: str, expected_url: str) -> bool:
    actual = _safe_url(actual_url)
    expected = _safe_url(expected_url)
    if actual is None or expected is None:
        return False
    return (
        actual[1] == expected[1]
        and actual[0].path == expected[0].path
        and actual[0].query == expected[0].query
    )


def _parse_inventory(raw: str, expected_page: int) -> tuple[list[Any], int, int, int] | None:
    if not isinstance(raw, str) or len(raw) > _MAX_INVENTORY_CHARS:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"jobs", "meta"}:
        return None
    records = payload.get("jobs")
    meta = payload.get("meta")
    if not isinstance(records, list) or not isinstance(meta, dict):
        return None
    page = meta.get("page")
    per_page = meta.get("perPage")
    total_jobs = meta.get("totalJobs")
    total_pages = meta.get("totalPages")
    values = (page, per_page, total_jobs, total_pages)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        return None
    calculated_pages = math.ceil(total_jobs / per_page) if per_page > 0 else -1
    if (
        page != expected_page
        or per_page <= 0
        or per_page > 100
        or total_jobs < 0
        or total_pages != calculated_pages
        or len(records) > per_page
        or (expected_page < total_pages and len(records) != per_page)
        or (
            expected_page == total_pages
            and len(records) != total_jobs - per_page * (total_pages - 1)
        )
        or (total_pages == 0 and (expected_page != 1 or records))
    ):
        return None
    return records, per_page, total_jobs, total_pages


def _candidate(record: Any, tenant: str) -> JobCandidate | None:
    if not isinstance(record, dict):
        return None
    job_id = record.get("id")
    title = _bounded_text(record.get("title"), required=True)
    if (
        not isinstance(job_id, str)
        or _JOB_ID.fullmatch(job_id) is None
        or not isinstance(title, str)
    ):
        return None
    if record.get("isInternalOnly") is not False or record.get("hasOpening") is not True:
        return None
    location_parts: list[str] = []
    for key in ("city", "state", "zip"):
        value = _bounded_text(record.get(key), required=False)
        if value is False:
            return None
        if isinstance(value, str):
            location_parts.append(value)
    organization = _bounded_text(record.get("organization"), required=False)
    if organization is False:
        return None
    return JobCandidate(
        title=title,
        url=f"https://{tenant}{_HOST_SUFFIX}/jobs/{job_id}",
        provider="healthcaresource",
        location=", ".join(location_parts) or None,
        raw={"job_id": job_id, "organization": organization},
    )


def _bounded_text(value: object, *, required: bool) -> str | None | bool:
    if value is None:
        return False if required else None
    if not isinstance(value, str) or len(value) > _MAX_FIELD_CHARS:
        return False
    stripped = value.strip()
    if not stripped:
        return False if required else None
    return stripped


def _normalized(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if value else ""


def _search_title(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > _MAX_FIELD_CHARS:
        return None
    title = " ".join(value.split())
    return title or None


def _invalid_inventory(
    board,
    board_url,
    api_urls,
    source,
    error,
    *,
    inventory_scope="full",
) -> AdapterResult:
    return _result(
        board,
        reason_code="INVALID_STRUCTURED_DATA",
        inventory_complete=False,
        inventory_scope=inventory_scope,
        error=error,
        board_urls=[board_url],
        api_urls=api_urls,
        response_source=source,
    )


def _fetch_failure(board, error, **trace) -> AdapterResult:
    if isinstance(error, FetchError):
        failure = project_fetch_error(error)
        return _result(
            board,
            reason_code=failure.pop("reason_code"),
            retryable=failure.pop("retryable"),
            inventory_complete=False,
            **trace,
            **failure,
        )
    return _result(
        board,
        reason_code="PROVIDER_FETCH_FAILED",
        retryable=True,
        inventory_complete=False,
        error=str(error),
        **trace,
    )


def _result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None = None,
    retryable: bool = False,
    inventory_complete: bool = True,
    inventory_scope: str = "full",
    **trace: Any,
) -> AdapterResult:
    items = candidates or []
    trace.setdefault("adapter", "healthcaresource")
    trace.setdefault("inventory_scope", inventory_scope)
    trace.setdefault("inventory_complete", inventory_complete)
    trace.setdefault("candidate_count", len(items))
    return AdapterResult(
        provider="healthcaresource",
        board=board,
        candidates=items,
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope=inventory_scope,
        inventory_complete=inventory_complete,
        trace=trace,
    )


ADAPTER = HealthcareSourceAdapter()
