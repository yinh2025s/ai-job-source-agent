from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse

from ..fetch_failure import project_fetch_error
from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST = re.compile(r"^secure(?P<shard>[1-9][0-9]{0,2})\.saashr\.com$", re.I)
_ACCOUNT = re.compile(r"^[1-9][0-9]{0,19}$")
_PATH = re.compile(r"^/ta/(?P<account>[1-9][0-9]{0,19})\.careers$", re.I)
_JOB_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z]{2})?$")
_MAX_HTML_CHARS = 2_000_000
_MAX_INVENTORY_CHARS = 5_000_000
_PAGE_SIZE = 100
_MAX_JOBS = 1_000
_MAX_PAGES = _MAX_JOBS // _PAGE_SIZE


class SaaSHRAdapter:
    name = "saashr"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _url_identity(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        identity = _url_identity(url)
        if identity is None:
            return None
        host, account = identity
        return _job_board(host, account)

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identity = _board_identity(board)
        if identity is None:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="invalid SaaSHR board locator",
            )
        host, account = identity
        board_url = _board_url(host, account)
        try:
            page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, error, board_urls=[board_url])

        final_url = page.final_url or page.url
        if _listing_identity(final_url) != identity:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="SaaSHR board redirected outside the declared account",
                board_urls=[board_url],
                rejected_final_url=final_url,
            )

        shell_reason = _shell_reason(page.html)
        if shell_reason is not None:
            return _result(
                board,
                reason_code=shell_reason,
                inventory_complete=False,
                error="SaaSHR public board returned a blocked page",
                board_urls=[board_url],
                response_source=page.source,
            )
        if not _valid_public_shell(page.html):
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="missing SaaSHR public careers shell evidence",
                board_urls=[board_url],
                response_source=page.source,
            )

        candidates: list[JobCandidate] = []
        api_urls: list[str] = []
        seen_ids: set[str] = set()
        expected_total: int | None = None
        offset = 0
        for _page_number in range(_MAX_PAGES):
            inventory_url = _inventory_url(host, account, offset)
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
                    error="SaaSHR inventory redirected outside the verified account endpoint",
                    board_urls=[board_url],
                    api_urls=api_urls,
                    rejected_final_url=response_url,
                )

            parsed = _parse_inventory(inventory_page.html, offset)
            if parsed is None:
                return _result(
                    board,
                    reason_code="INVALID_STRUCTURED_DATA",
                    inventory_complete=False,
                    error="invalid SaaSHR public inventory",
                    board_urls=[board_url],
                    api_urls=api_urls,
                    response_source=inventory_page.source,
                )
            records, total = parsed
            if expected_total is None:
                expected_total = total
            if total != expected_total:
                return _invalid_record_result(
                    board, board_url, api_urls, inventory_page.source,
                    "SaaSHR inventory total changed during pagination",
                )
            if total > _MAX_JOBS:
                return _result(
                    board,
                    candidates=candidates,
                    reason_code="OPENING_DISCOVERY_INCOMPLETE",
                    inventory_complete=False,
                    error="SaaSHR inventory exceeded the pagination limit",
                    board_urls=[board_url],
                    api_urls=api_urls,
                    records_seen=len(candidates),
                    total=total,
                    pagination_limit=_MAX_JOBS,
                )

            page_candidates: list[JobCandidate] = []
            for record in records:
                candidate = _candidate(record, host, account)
                job_id = candidate.raw["job_id"] if candidate is not None else None
                if candidate is None or job_id in seen_ids:
                    return _invalid_record_result(
                        board, board_url, api_urls, inventory_page.source,
                        "SaaSHR inventory contained an invalid or duplicate opening",
                    )
                seen_ids.add(job_id)
                page_candidates.append(candidate)
            candidates.extend(page_candidates)
            offset += len(records)
            if offset == total:
                break
            if not records or offset > total:
                return _invalid_record_result(
                    board, board_url, api_urls, inventory_page.source,
                    "SaaSHR pagination did not cover the declared inventory",
                )
        else:
            return _result(
                board,
                candidates=candidates,
                reason_code="OPENING_DISCOVERY_INCOMPLETE",
                inventory_complete=False,
                error="SaaSHR pagination limit reached",
                board_urls=[board_url],
                api_urls=api_urls,
                records_seen=len(candidates),
                total=expected_total,
                pagination_limit=_MAX_JOBS,
            )

        target = _normalized(query.title)
        return _result(
            board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            inventory_complete=True,
            board_urls=[board_url],
            api_urls=api_urls,
            response_source=page.source,
            variant="ukg_ready_public_requisitions",
            identity={"host": host, "account_id": account},
            records_seen=len(candidates),
            total=expected_total or 0,
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
        or _HOST.fullmatch(host) is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None
    return parsed, host


def _url_identity(url: str) -> tuple[str, str] | None:
    parsed_host = _safe_url(url)
    if parsed_host is None:
        return None
    parsed, host = parsed_host
    match = _PATH.fullmatch(unquote(parsed.path))
    if match is None:
        return None
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if len(query) not in {1, 2}:
        return None
    route_key, route_value = query[0]
    route_key = route_key.casefold()
    if route_key == "careerssearch":
        if route_value:
            return None
    elif route_key == "showjob":
        if not _JOB_ID.fullmatch(route_value):
            return None
    else:
        return None
    if len(query) == 2 and (
        query[1][0].casefold() != "lang" or not _LOCALE.fullmatch(query[1][1])
    ):
        return None
    return host, match.group("account")


def _listing_identity(url: str) -> tuple[str, str] | None:
    identity = _url_identity(url)
    if identity is None:
        return None
    query = parse_qsl(urlparse(url).query, keep_blank_values=True)
    return identity if query[0][0].casefold() == "careerssearch" else None


def _identifier(host: str, account: str) -> str:
    return f"{host}|{account}"


def _board_url(host: str, account: str) -> str:
    return f"https://{host}/ta/{account}.careers?CareersSearch="


def _job_board(host: str, account: str) -> JobBoard:
    return JobBoard(
        url=_board_url(host, account),
        provider="saashr",
        identifier=_identifier(host, account),
    )


def _board_identity(board: JobBoard) -> tuple[str, str] | None:
    if board.provider != "saashr" or not isinstance(board.identifier, str):
        return None
    host, separator, account = board.identifier.partition("|")
    if not separator or _HOST.fullmatch(host) is None or not _ACCOUNT.fullmatch(account):
        return None
    identity = _url_identity(board.url)
    if identity == (host, account) and board.url == _board_url(host, account):
        return identity
    return None


def _inventory_url(host: str, account: str, offset: int) -> str:
    query = urlencode(
        {
            "offset": offset,
            "size": _PAGE_SIZE,
            "sort": "",
            "ein_id": "",
            "lang": "en-US",
        }
    )
    return (
        f"https://{host}/ta/rest/ui/recruitment/companies/%7C{account}/"
        f"job-requisitions?{query}"
    )


def _same_inventory_url(actual_url: str, expected_url: str) -> bool:
    actual = _safe_url(actual_url)
    expected = _safe_url(expected_url)
    if actual is None or expected is None:
        return False
    actual_parsed, actual_host = actual
    expected_parsed, expected_host = expected
    return (
        actual_host == expected_host
        and unquote(actual_parsed.path) == unquote(expected_parsed.path)
        and parse_qsl(actual_parsed.query, keep_blank_values=True)
        == parse_qsl(expected_parsed.query, keep_blank_values=True)
    )


def _valid_public_shell(html: str) -> bool:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return False
    text = html.casefold()
    return all(
        marker in text
        for marker in (
            "<title>career search</title>",
            'data-assets-path="/ta/client/"',
            'id="_app"',
            "/ta/client/./jobs-",
        )
    )


def _shell_reason(html: str) -> str | None:
    text = " ".join((html or "").casefold().split())
    if any(marker in text for marker in ("403 forbidden", "access denied", "request forbidden")):
        return "HTTP_FORBIDDEN"
    if any(marker in text for marker in ('<title>login', 'type="password"', "sign in to continue")):
        return "LOGIN_REQUIRED"
    return None


def _parse_inventory(raw: str, expected_offset: int) -> tuple[list[Any], int] | None:
    if not isinstance(raw, str) or len(raw) > _MAX_INVENTORY_CHARS:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("job_requisitions"), list):
        return None
    paging = payload.get("_paging")
    if not isinstance(paging, dict):
        return None
    offset = paging.get("offset")
    size = paging.get("size")
    total = paging.get("total")
    records = payload["job_requisitions"]
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (offset, size, total)
        )
        or offset != expected_offset
        or size <= 0
        or size > _PAGE_SIZE
        or total < 0
        or len(records) > size
        or expected_offset + len(records) > total
    ):
        return None
    return records, total


def _candidate(record: Any, host: str, account: str) -> JobCandidate | None:
    if not isinstance(record, dict):
        return None
    raw_id = record.get("id")
    title = record.get("job_title")
    if (
        isinstance(raw_id, bool)
        or not isinstance(raw_id, (int, str))
        or not _JOB_ID.fullmatch(str(raw_id))
        or not isinstance(title, str)
        or not title.strip()
        or len(title) > 500
    ):
        return None
    location = _location(record)
    if location is False:
        return None
    job_id = str(raw_id)
    return JobCandidate(
        title=title.strip(),
        url=f"https://{host}/ta/{account}.careers?ShowJob={job_id}",
        provider="saashr",
        location=location,
        raw={"job_id": job_id, "account_id": account},
    )


def _location(record: dict[str, Any]) -> str | None | bool:
    raw_location = record.get("location")
    if raw_location is not None and not isinstance(raw_location, dict):
        return False
    remote = record.get("is_remote_job")
    if remote is not None and not isinstance(remote, bool):
        return False
    parts: list[str] = []
    if remote is True:
        parts.append("Remote")
    if isinstance(raw_location, dict):
        for key in ("city", "state", "country"):
            value = raw_location.get(key)
            if value is not None and (not isinstance(value, str) or len(value) > 200):
                return False
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return ", ".join(parts) if parts else None


def _normalized(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if value else ""


def _invalid_record_result(
    board: JobBoard,
    board_url: str,
    api_urls: list[str],
    response_source: str,
    error: str,
) -> AdapterResult:
    return _result(
        board,
        reason_code="INVALID_STRUCTURED_DATA",
        inventory_complete=False,
        error=error,
        board_urls=[board_url],
        api_urls=api_urls,
        response_source=response_source,
    )


def _fetch_failure(board: JobBoard, error: Exception, **trace: Any) -> AdapterResult:
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
        **trace,
    )


def _result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None = None,
    retryable: bool = False,
    inventory_complete: bool,
    error: str | None = None,
    **trace: Any,
) -> AdapterResult:
    trace.update(
        {
            "adapter": "saashr",
            "inventory_scope": "full" if inventory_complete else "unknown",
            "inventory_complete": inventory_complete,
        }
    )
    if error is not None:
        trace["error"] = error
    return AdapterResult(
        provider="saashr",
        board=board,
        candidates=candidates or [],
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope="full" if inventory_complete else "unknown",
        inventory_complete=inventory_complete,
        trace=trace,
    )


ADAPTER = SaaSHRAdapter()
