from __future__ import annotations

import json
import re
from urllib.parse import unquote, urlparse

from ..fetch_failure import project_fetch_error
from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_PROVIDER_SUFFIX = ".pinpointhq.com"
_TENANT = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_LOCALE = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.IGNORECASE)
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_POSITIVE_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_RESERVED_TENANTS = {"api", "app", "help", "support", "www"}
_MAX_RESPONSE_CHARS = 5_000_000
_MAX_POSTINGS = 2_000
_MAX_FIELD_CHARS = 500


class PinpointAdapter:
    name = "pinpoint"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _url_tenant(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        tenant = _url_tenant(url)
        if tenant is None:
            return None
        return _job_board(tenant)

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        tenant = _board_tenant(board)
        if tenant is None:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="invalid Pinpoint board identity",
            )

        board_url = _board_url(tenant)
        inventory_url = _inventory_url(tenant)
        try:
            page = fetcher.fetch(
                inventory_url,
                headers={"Accept": "application/json", "Referer": board_url},
            )
        except (FetchError, OSError, TimeoutError) as error:
            reason_code, retryable = _fetch_classification(error)
            return _result(
                board,
                reason_code=reason_code,
                retryable=retryable,
                inventory_complete=False,
                error=str(error),
                board_urls=[board_url],
                api_urls=[inventory_url],
            )

        final_url = page.final_url or page.url
        if not _same_inventory_url(final_url, tenant):
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="Pinpoint inventory redirected outside the canonical tenant endpoint",
                board_urls=[board_url],
                api_urls=[inventory_url],
                response_source=page.source,
                rejected_final_url=final_url,
            )

        parsed = _inventory(page.html)
        if parsed == "response_cap_exceeded":
            return _result(
                board,
                reason_code="FETCH_BUDGET_EXHAUSTED",
                retryable=True,
                inventory_complete=False,
                error="Pinpoint inventory exceeded the bounded response size",
                board_urls=[board_url],
                api_urls=[inventory_url],
                response_source=page.source,
                stop_reason=parsed,
            )
        if parsed == "row_cap_exceeded":
            return _result(
                board,
                reason_code="FETCH_BUDGET_EXHAUSTED",
                retryable=True,
                inventory_complete=False,
                error="Pinpoint inventory exceeded the bounded posting count",
                board_urls=[board_url],
                api_urls=[inventory_url],
                response_source=page.source,
                stop_reason=parsed,
            )
        if not isinstance(parsed, list):
            return _result(
                board,
                reason_code="INVALID_STRUCTURED_DATA",
                inventory_complete=False,
                error="invalid Pinpoint public inventory",
                board_urls=[board_url],
                api_urls=[inventory_url],
                response_source=page.source,
            )

        candidates: list[JobCandidate] = []
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        interest_records_excluded = 0
        for record in parsed:
            if _is_register_interest_record(record, tenant):
                interest_records_excluded += 1
                continue
            candidate = _candidate(record, tenant)
            if candidate is None:
                return _result(
                    board,
                    reason_code="INVALID_STRUCTURED_DATA",
                    inventory_complete=False,
                    error="Pinpoint inventory contained an invalid or cross-tenant opening",
                    board_urls=[board_url],
                    api_urls=[inventory_url],
                    response_source=page.source,
                )
            posting_id = candidate.raw["posting_id"]
            if posting_id in seen_ids or candidate.url in seen_urls:
                return _result(
                    board,
                    reason_code="INVALID_STRUCTURED_DATA",
                    inventory_complete=False,
                    error="Pinpoint inventory contained a duplicate opening",
                    board_urls=[board_url],
                    api_urls=[inventory_url],
                    response_source=page.source,
                )
            seen_ids.add(posting_id)
            seen_urls.add(candidate.url)
            candidates.append(candidate)

        target = _normalized_title(query.title)
        return _result(
            board,
            candidates=candidates,
            reason_code="EMPTY_PROVIDER_RESPONSE" if not candidates else None,
            inventory_complete=True,
            board_urls=[board_url],
            api_urls=[inventory_url],
            response_source=page.source,
            records_seen=len(parsed),
            candidate_count=len(candidates),
            interest_records_excluded=interest_records_excluded,
            exact_title_found=bool(
                target
                and any(_normalized_title(candidate.title) == target for candidate in candidates)
            ),
        )


def _safe_url(url: object):
    if not isinstance(url, str) or len(url) > 8_192:
        return None
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
    ):
        return None
    return parsed, host


def _tenant_from_host(host: str) -> str | None:
    if not host.endswith(_PROVIDER_SUFFIX):
        return None
    tenant = host[: -len(_PROVIDER_SUFFIX)]
    if tenant in _RESERVED_TENANTS or not _TENANT.fullmatch(tenant):
        return None
    return tenant


def _url_tenant(url: str) -> str | None:
    parsed_host = _safe_url(url)
    if parsed_host is None:
        return None
    parsed, host = parsed_host
    tenant = _tenant_from_host(host)
    if tenant is None:
        return None
    path = _normalized_path(parsed.path)
    if path in {"/", "/postings.json"}:
        return tenant
    parts = [part for part in path.split("/") if part]
    if len(parts) == 1 and _LOCALE.fullmatch(parts[0]):
        return tenant
    if (
        len(parts) == 3
        and _LOCALE.fullmatch(parts[0])
        and parts[1].casefold() == "postings"
        and _UUID.fullmatch(parts[2])
    ):
        return tenant
    return None


def _normalized_path(path: str) -> str:
    decoded = unquote(path)
    return "/" + "/".join(part for part in decoded.split("/") if part)


def _board_url(tenant: str) -> str:
    return f"https://{tenant}{_PROVIDER_SUFFIX}/"


def _inventory_url(tenant: str) -> str:
    return f"https://{tenant}{_PROVIDER_SUFFIX}/postings.json"


def _job_board(tenant: str) -> JobBoard:
    return JobBoard(
        url=_board_url(tenant),
        provider="pinpoint",
        identifier=tenant,
        replay_safe=True,
    )


def _board_tenant(board: JobBoard) -> str | None:
    if (
        board.provider != "pinpoint"
        or not isinstance(board.identifier, str)
        or not _TENANT.fullmatch(board.identifier)
    ):
        return None
    tenant = board.identifier.casefold()
    if tenant in _RESERVED_TENANTS or board.url != _board_url(tenant):
        return None
    return tenant


def _same_inventory_url(url: str, tenant: str) -> bool:
    parsed_host = _safe_url(url)
    if parsed_host is None:
        return False
    parsed, host = parsed_host
    return (
        host == f"{tenant}{_PROVIDER_SUFFIX}"
        and parsed.path == "/postings.json"
        and not parsed.query
    )


def _inventory(raw: str) -> list[dict] | str | None:
    if not isinstance(raw, str):
        return None
    if len(raw) > _MAX_RESPONSE_CHARS:
        return "response_cap_exceeded"
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"data"}:
        return None
    records = payload.get("data")
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        return None
    if len(records) > _MAX_POSTINGS:
        return "row_cap_exceeded"
    return records


def _candidate(record: dict, tenant: str) -> JobCandidate | None:
    posting_id = _positive_id(record.get("id"))
    title = _field(record.get("title"), required=True)
    raw_url = record.get("url")
    path = record.get("path")
    detail = _detail_identity(raw_url, tenant)
    job = record.get("job")
    location = _location(record.get("location"))
    if (
        posting_id is None
        or title is None
        or detail is None
        or not isinstance(path, str)
        or path != detail[2]
        or not isinstance(job, dict)
        or _positive_id(job.get("id")) is None
        or location is None
    ):
        return None
    locale, posting_uuid, canonical_path = detail
    return JobCandidate(
        title=title,
        url=f"https://{tenant}{_PROVIDER_SUFFIX}{canonical_path}",
        provider="pinpoint",
        location=location,
        raw={
            "posting_id": posting_id,
            "job_id": str(job["id"]),
            "posting_uuid": posting_uuid,
            "locale": locale,
        },
    )


def _detail_identity(url: object, tenant: str) -> tuple[str, str, str] | None:
    parsed_host = _safe_url(url)
    if parsed_host is None:
        return None
    parsed, host = parsed_host
    if host != f"{tenant}{_PROVIDER_SUFFIX}" or parsed.query:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if (
        len(parts) != 3
        or not _LOCALE.fullmatch(parts[0])
        or parts[1].casefold() != "postings"
        or not _UUID.fullmatch(parts[2])
    ):
        return None
    locale = parts[0].casefold()
    posting_uuid = parts[2].casefold()
    return locale, posting_uuid, f"/{locale}/postings/{posting_uuid}"


def _location(value: object) -> str | None:
    if not isinstance(value, dict) or _positive_id(value.get("id")) is None:
        return None
    city = _field(value.get("city"), required=False)
    province = _field(value.get("province"), required=False)
    name = _field(value.get("name"), required=False)
    if city is None or province is None or name is None:
        return None
    city_province = ", ".join(part for part in (city, province) if part)
    return city_province or name or None


def _field(value: object, *, required: bool) -> str | None:
    if not isinstance(value, str) or len(value) > _MAX_FIELD_CHARS:
        return None
    cleaned = " ".join(value.split())
    if required and not cleaned:
        return None
    if any(ord(character) < 32 for character in cleaned):
        return None
    return cleaned


def _positive_id(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    return text if _POSITIVE_ID.fullmatch(text) else None


def _is_register_interest_record(record: dict, tenant: str) -> bool:
    raw_url = record.get("url")
    raw_path = record.get("path")
    parsed_host = _safe_url(raw_url)
    if parsed_host is None or not isinstance(raw_path, str):
        return False
    parsed, host = parsed_host
    path = _normalized_path(raw_path).casefold()
    return (
        host == f"{tenant}{_PROVIDER_SUFFIX}"
        and not parsed.query
        and _normalized_path(parsed.path).casefold() == path
        and path in {"/register-your-interest", "/register-your-interest/new"}
    )


def _fetch_classification(error: Exception) -> tuple[str, bool]:
    if isinstance(error, FetchError):
        projection = project_fetch_error(error)
        reason_code = projection["reason_code"]
        retryable = projection["retryable"]
    else:
        reason_code = classify_fetch_error(str(error))
        retryable = reason_spec(reason_code).retryable
    if reason_code == "FETCH_FAILED":
        return "PROVIDER_FETCH_FAILED", True
    return reason_code, retryable


def _normalized_title(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if value else ""


def _result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None,
    retryable: bool = False,
    inventory_complete: bool,
    error: str | None = None,
    board_urls: list[str] | None = None,
    api_urls: list[str] | None = None,
    response_source: str | None = None,
    rejected_final_url: str | None = None,
    stop_reason: str | None = None,
    records_seen: int | None = None,
    candidate_count: int | None = None,
    interest_records_excluded: int | None = None,
    exact_title_found: bool | None = None,
) -> AdapterResult:
    trace = {
        "adapter": "pinpoint",
        "variant": "public_postings_inventory",
        "board_urls": board_urls or [],
        "api_urls": api_urls or [],
        "inventory_scope": "full" if inventory_complete else "unknown",
        "inventory_complete": inventory_complete,
    }
    optional = {
        "error": error,
        "response_source": response_source,
        "rejected_final_url": rejected_final_url,
        "stop_reason": stop_reason,
        "records_seen": records_seen,
        "candidate_count": candidate_count,
        "interest_records_excluded": interest_records_excluded,
        "exact_title_found": exact_title_found,
    }
    trace.update({key: value for key, value in optional.items() if value is not None})
    return AdapterResult(
        provider="pinpoint",
        board=board,
        candidates=candidates or [],
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope="full" if inventory_complete else "unknown",
        inventory_complete=inventory_complete,
        trace=trace,
    )


ADAPTER = PinpointAdapter()
