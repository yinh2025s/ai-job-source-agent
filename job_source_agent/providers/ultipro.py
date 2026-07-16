from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST = "recruiting2.ultipro.com"
_TENANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_CONFIG_STRING = re.compile(
    r"\b(?P<name>loadUrl|opportunityLinkUrl)\s*:\s*"
    r"(?P<quote>['\"])(?P<value>[^'\"\\]{1,500})(?P=quote)"
)
_PAGE_SIZE = re.compile(r"\bpageSize\s*:\s*(?P<value>[0-9]{1,3})\b")
_JOB_BOARD = re.compile(r"\bjobBoard\s*:\s*(?=\{)")
_MAX_PAGES = 20


class UltiProAdapter:
    name = "ultipro"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _url_identity(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        identity = _url_identity(url)
        if identity is None:
            return None
        tenant, board_id, _detail_id = identity
        return JobBoard(
            url=_board_url(tenant, board_id),
            provider=self.name,
            identifier=_board_identifier(tenant, board_id),
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identity = _board_identity(board)
        inventory_scope = "title_filtered" if query.title else "full"
        if identity is None:
            return _result(
                board,
                inventory_scope,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                trace={"error": "invalid UltiPro board locator"},
            )
        tenant, board_id = identity
        board_url = _board_url(tenant, board_id)

        try:
            page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, inventory_scope, [board_url], [], error)

        final_url = page.final_url or page.url
        if not _is_exact_board_url(final_url, tenant, board_id):
            return _result(
                board,
                inventory_scope,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                trace={
                    "board_urls": [board_url],
                    "error": "UltiPro board redirected outside the public board route",
                    "rejected_final_url": final_url,
                },
            )

        config = _public_board_config(page.html, tenant, board_id)
        if config is None:
            return _result(
                board,
                inventory_scope,
                reason_code="INVALID_STRUCTURED_DATA",
                inventory_complete=False,
                trace={
                    "board_urls": [board_url],
                    "response_source": page.source,
                    "error": "missing safe UltiPro public board configuration",
                },
            )
        load_path, detail_path, page_size = config
        load_url = f"https://{_HOST}{load_path}"

        candidates: list[JobCandidate] = []
        seen_ids: set[str] = set()
        api_urls: list[str] = []
        pages_fetched = 0
        total_count: int | None = None
        inventory_complete = False
        for page_index in range(_MAX_PAGES):
            skip = page_index * page_size
            payload = _search_payload(query, page_size, skip)
            api_urls.append(load_url)
            try:
                response = fetcher.fetch(
                    load_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json; charset=utf-8",
                        "Referer": board_url,
                    },
                )
            except (FetchError, OSError, TimeoutError) as error:
                return _fetch_failure(
                    board,
                    inventory_scope,
                    [board_url],
                    api_urls,
                    error,
                    candidates=candidates,
                )

            response_url = response.final_url or response.url
            if not _is_exact_load_url(response_url, tenant, board_id):
                return _result(
                    board,
                    inventory_scope,
                    candidates=candidates,
                    reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                    inventory_complete=False,
                    trace={
                        "board_urls": [board_url],
                        "api_urls": api_urls,
                        "error": "UltiPro inventory redirected outside the public board route",
                        "rejected_final_url": response_url,
                    },
                )

            parsed = _inventory_response(response.html)
            if parsed is None:
                return _invalid_response(
                    board, inventory_scope, board_url, api_urls, response.source, candidates
                )
            records, response_total = parsed
            if total_count is not None and response_total != total_count:
                return _invalid_response(
                    board, inventory_scope, board_url, api_urls, response.source, candidates
                )
            total_count = response_total
            if skip > total_count or skip + len(records) > total_count:
                return _invalid_response(
                    board, inventory_scope, board_url, api_urls, response.source, candidates
                )

            page_candidates: list[JobCandidate] = []
            page_ids: set[str] = set()
            for record in records:
                candidate = _candidate(record, tenant, board_id, detail_path)
                if candidate is None:
                    return _invalid_response(
                        board, inventory_scope, board_url, api_urls, response.source, candidates
                    )
                candidate_id = candidate.raw["opportunity_id"]
                if candidate_id in seen_ids or candidate_id in page_ids:
                    return _invalid_response(
                        board, inventory_scope, board_url, api_urls, response.source, candidates
                    )
                page_ids.add(candidate_id)
                page_candidates.append(candidate)

            seen_ids.update(page_ids)
            candidates.extend(page_candidates)
            pages_fetched += 1
            consumed = skip + len(records)
            if consumed == total_count:
                inventory_complete = True
                break
            if not records or len(records) < page_size:
                return _invalid_response(
                    board, inventory_scope, board_url, api_urls, response.source, candidates
                )

        return _result(
            board,
            inventory_scope,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            inventory_complete=inventory_complete,
            trace={
                "variant": "public_job_board_json",
                "board_urls": [board_url],
                "api_urls": api_urls,
                "response_source": page.source,
                "candidate_count": len(candidates),
                "pages_fetched": pages_fetched,
                "page_size": page_size,
                "total_count": total_count,
            },
        )


def _url_identity(url: str) -> tuple[str, str, str | None] | None:
    parsed = _safe_url(url)
    if parsed is None:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) not in {3, 4} or parts[1] != "JobBoard":
        return None
    tenant, board_id = parts[0], _strict_uuid(parts[2])
    if not _TENANT.fullmatch(tenant) or board_id is None:
        return None
    if len(parts) == 3:
        return (tenant, board_id, None)
    if parts[3] != "OpportunityDetail":
        return None
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if len(query) != 1 or query[0][0] != "opportunityId":
        return None
    detail_id = _strict_uuid(query[0][1])
    return (tenant, board_id, detail_id) if detail_id else None


def _safe_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != _HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None
    return parsed


def _board_identifier(tenant: str, board_id: str) -> str:
    return f"{tenant}/{board_id}"


def _board_url(tenant: str, board_id: str) -> str:
    return f"https://{_HOST}/{tenant}/JobBoard/{board_id}/"


def _board_identity(board: JobBoard) -> tuple[str, str] | None:
    if board.provider != "ultipro" or not isinstance(board.identifier, str):
        return None
    parts = board.identifier.split("/")
    if len(parts) != 2 or not _TENANT.fullmatch(parts[0]):
        return None
    board_id = _strict_uuid(parts[1])
    identity = _url_identity(board.url)
    if board_id is None or identity != (parts[0], board_id, None):
        return None
    return parts[0], board_id


def _is_exact_board_url(url: str, tenant: str, board_id: str) -> bool:
    parsed = _safe_url(url)
    return bool(
        parsed
        and not parsed.query
        and _url_identity(url) == (tenant, board_id, None)
    )


def _load_path(tenant: str, board_id: str) -> str:
    return f"/{tenant}/JobBoard/{board_id}/JobBoardView/LoadSearchResults"


def _detail_path(tenant: str, board_id: str) -> str:
    return f"/{tenant}/JobBoard/{board_id}/OpportunityDetail"


def _is_exact_load_url(url: str, tenant: str, board_id: str) -> bool:
    parsed = _safe_url(url)
    return bool(
        parsed
        and parsed.path == _load_path(tenant, board_id)
        and not parsed.query
    )


def _public_board_config(
    html: str, tenant: str, board_id: str
) -> tuple[str, str, int] | None:
    if not isinstance(html, str):
        return None
    values = {match.group("name"): match.group("value") for match in _CONFIG_STRING.finditer(html)}
    load_path = values.get("loadUrl")
    detail_template = values.get("opportunityLinkUrl")
    expected_detail = _detail_path(tenant, board_id)
    if load_path != _load_path(tenant, board_id) or detail_template != (
        expected_detail + "?opportunityId=00000000-0000-0000-0000-000000000000"
    ):
        return None
    page_match = _PAGE_SIZE.search(html)
    if page_match is None:
        return None
    page_size = int(page_match.group("value"))
    if not 1 <= page_size <= 100:
        return None
    marker = _JOB_BOARD.search(html)
    if marker is None:
        return None
    try:
        job_board, _end = json.JSONDecoder().raw_decode(html, marker.end())
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(job_board, dict) or _strict_uuid(job_board.get("Id")) != board_id:
        return None
    return load_path, expected_detail, page_size


def _search_payload(query: JobQuery, page_size: int, skip: int) -> dict[str, Any]:
    title = (query.title or "").strip()
    if title:
        order_by = [{"Value": "relevance", "PropertyName": "MatchScore", "Ascending": False}]
    else:
        order_by = [{"Value": "postedDateDesc", "PropertyName": "PostedDate", "Ascending": False}]
    return {
        "opportunitySearch": {
            "Top": page_size,
            "Skip": skip,
            "QueryString": title,
            "OrderBy": order_by,
            "OrderByKey": None,
            "Filters": [],
            "Coordinates": None,
            "Extent": None,
            "ProximitySearchType": 0,
        }
    }


def _inventory_response(html: str) -> tuple[list[Any], int] | None:
    try:
        payload = json.loads(html)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or set(payload) < {"opportunities", "totalCount"}:
        return None
    records = payload.get("opportunities")
    total_count = payload.get("totalCount")
    if (
        not isinstance(records, list)
        or isinstance(total_count, bool)
        or not isinstance(total_count, int)
        or total_count < 0
    ):
        return None
    return records, total_count


def _candidate(
    record: Any, tenant: str, board_id: str, detail_path: str
) -> JobCandidate | None:
    if not isinstance(record, dict):
        return None
    opportunity_id = _strict_uuid(record.get("Id"))
    title = record.get("Title")
    if opportunity_id is None or not isinstance(title, str) or not title.strip():
        return None
    url = f"https://{_HOST}{detail_path}?opportunityId={opportunity_id}"
    return JobCandidate(
        title=" ".join(title.split()),
        url=url,
        provider="ultipro",
        location=_location(record.get("Locations")),
        raw={
            "opportunity_id": opportunity_id,
            "requisition_number": record.get("RequisitionNumber"),
        },
    )


def _location(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("LocalizedName") or item.get("LocalizedDescription")
        address = item.get("Address")
        if not name and isinstance(address, dict):
            city = address.get("City")
            state = address.get("State")
            state_name = state.get("Name") if isinstance(state, dict) else None
            name = ", ".join(
                part.strip()
                for part in (city, state_name)
                if isinstance(part, str) and part.strip()
            )
        if isinstance(name, str) and name.strip() and name.strip() not in names:
            names.append(name.strip())
    return "; ".join(names) or None


def _strict_uuid(value: Any) -> str | None:
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        return None
    return value.casefold()


def _fetch_failure(
    board: JobBoard,
    inventory_scope: str,
    board_urls: list[str],
    api_urls: list[str],
    error: Exception,
    *,
    candidates: list[JobCandidate] | None = None,
) -> AdapterResult:
    return _result(
        board,
        inventory_scope,
        candidates=candidates,
        reason_code="PROVIDER_FETCH_FAILED",
        retryable=True,
        inventory_complete=False,
        trace={
            "board_urls": board_urls,
            "api_urls": api_urls,
            "error": str(error),
        },
    )


def _invalid_response(
    board: JobBoard,
    inventory_scope: str,
    board_url: str,
    api_urls: list[str],
    response_source: str,
    candidates: list[JobCandidate],
) -> AdapterResult:
    return _result(
        board,
        inventory_scope,
        candidates=candidates,
        reason_code="INVALID_STRUCTURED_DATA",
        inventory_complete=False,
        trace={
            "board_urls": [board_url],
            "api_urls": api_urls,
            "response_source": response_source,
            "error": "invalid UltiPro public inventory response",
        },
    )


def _result(
    board: JobBoard,
    inventory_scope: str,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None = None,
    retryable: bool = False,
    inventory_complete: bool,
    trace: dict[str, Any],
) -> AdapterResult:
    trace.update(
        {
            "adapter": "ultipro",
            "inventory_scope": inventory_scope,
            "inventory_complete": inventory_complete,
        }
    )
    return AdapterResult(
        provider="ultipro",
        board=board,
        candidates=candidates or [],
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope=inventory_scope,
        inventory_complete=inventory_complete,
        trace=trace,
    )


ADAPTER = UltiProAdapter()
