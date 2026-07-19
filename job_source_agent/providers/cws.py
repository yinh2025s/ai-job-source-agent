from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import ipaddress
import json
import re
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_MAX_HTML_CHARS = 2_000_000
_MAX_SCRIPT_CHARS = 200_000
_MAX_RESPONSE_CHARS = 2_000_000
_MAX_PAGES = 10
_MAX_ROWS = 1_000
_DEFAULT_LIMIT = 12
_MAX_LIMIT = 100
_MAX_FILTERS = 20
_MAX_CRITERION_CHARS = 500
_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$",
    re.IGNORECASE,
)
_ORG_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}"
    r"(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,79}){0,3}$"
)
_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_CALL = re.compile(
    r"\bCWS\s*\.\s*jobs\s*\.\s*(set_api|set_options|sortby)\s*\("
)
_OPTION = re.compile(
    r"(?:^|[,\{])\s*(?:['\"])?(org_id|jobdetail_path|boost|limit)(?:['\"])?\s*:\s*"
    r"(?:(['\"])(.*?)\2|(\d+))",
    re.DOTALL,
)
_FILTERS_OPTION = re.compile(
    r"(?:^|[,\{])\s*(?:['\"])?filters(?:['\"])?\s*:\s*(\[[^\]]*\])",
    re.DOTALL,
)
_PROTOCOL_OPTION = re.compile(
    r"(?:^|[,\{])\s*(?:['\"])?(org_id|jobdetail_path|boost|limit|filters)"
    r"(?:['\"])?\s*:",
)
_SMARTPOST_ORG = re.compile(
    r"(?:['\"]smartPost_org['\"]|\bsmartPost_org)\s*:\s*(['\"])([0-9]{1,20})\1"
)
_INTERNAL_API_URL = "https://jobsapi-internal.m-cloud.io/api/"


@dataclass
class _InventoryRun:
    candidates: list[JobCandidate]
    reason_code: str | None
    pages_fetched: int
    records_seen: int
    expected_total: int | None
    response_source: str | None
    stop_reason: str
    closed_count: int = 0

    @property
    def complete(self) -> bool:
        return self.reason_code is None and self.expected_total == self.records_seen


class CWSAdapter:
    name = "cws"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return False

    def identify_board(self, url: str) -> JobBoard | None:
        return None

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        parsed_page = _safe_public_https_url(page_url)
        config = _page_config(page.html, parsed_page)
        if parsed_page is None or config is None:
            return None
        (
            api_url,
            org_id,
            smartpost_org,
            detail_path,
            limit,
            filters,
            boost,
            sort,
        ) = config
        board_url = urlunparse(parsed_page._replace(query="", fragment=""))
        identifier = _identifier(
            board_url,
            api_url,
            org_id,
            smartpost_org,
            detail_path,
            limit,
            filters,
            boost,
            sort,
        )
        return JobBoard(
            url=board_url,
            provider=self.name,
            identifier=identifier,
            replay_safe=True,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        scope = "title_filtered" if query.title else "full"
        identity = _board_identity(board)
        title = _clean(query.title) if query.title is not None else None
        if identity is None:
            return _failure(
                board,
                scope,
                "PROVIDER_VARIANT_UNSUPPORTED",
                "invalid_board_identity",
            )
        if not title or len(title) > 200 or _has_controls(title):
            return _failure(
                board,
                scope,
                "PROVIDER_VARIANT_UNSUPPORTED",
                "bounded_title_required",
            )

        (
            board_url,
            api_url,
            org_id,
            smartpost_org,
            detail_path,
            limit,
            filters,
            boost,
            sort,
        ) = identity
        primary = _collect_inventory(
            fetcher,
            endpoint=api_url + "job",
            title=title,
            org_id=org_id,
            board_url=board_url,
            detail_path=detail_path,
            limit=limit,
            filters=filters,
            boost=boost,
            sort=sort,
        )
        selected = primary
        fallback_attempts: list[dict] = []
        if primary.reason_code == "HTTP_NOT_FOUND" and smartpost_org is not None:
            for variant_name, fallback_title in _internal_title_variants(title):
                attempted = _collect_inventory(
                    fetcher,
                    endpoint=_INTERNAL_API_URL + "job",
                    title=fallback_title,
                    org_id=smartpost_org,
                    board_url=board_url,
                    detail_path=detail_path,
                    limit=limit,
                    filters=filters,
                    boost=boost,
                    sort=sort,
                    require_smartpost_org=True,
                )
                exact_title_present = any(
                    _title_key(candidate.title) == _title_key(title)
                    for candidate in attempted.candidates
                )
                fallback_attempts.append(
                    {
                        "variant": variant_name,
                        "status": "verified" if attempted.complete else "incomplete",
                        "reason_code": attempted.reason_code,
                        "page_count": attempted.pages_fetched,
                        "records_seen": attempted.records_seen,
                        "total": attempted.expected_total,
                        "candidate_count": len(attempted.candidates),
                        "exact_title_present": exact_title_present,
                    }
                )
                if attempted.complete and exact_title_present:
                    selected = attempted
                    break

        inventory_complete = selected.complete
        reason_code = selected.reason_code
        if inventory_complete and not selected.candidates:
            reason_code = "EMPTY_PROVIDER_RESPONSE"

        trace = {
            "adapter": self.name,
            "variant": "m_cloud_jsonp",
            "board_url": board.url,
            "api_host": urlparse(api_url).hostname,
            "org_id": org_id,
            "filter_count": len(filters),
            "boost_configured": boost is not None,
            "smartpost_fallback_configured": smartpost_org is not None,
            "transport": "internal_smartpost" if selected is not primary else "declared_api",
            "fallback_attempts": fallback_attempts,
            "page_count": selected.pages_fetched,
            "records_seen": selected.records_seen,
            "total": selected.expected_total,
            "candidate_count": len(selected.candidates),
            "closed_count": selected.closed_count,
            "response_source": selected.response_source,
            "stop_reason": selected.stop_reason,
            "inventory_scope": scope,
            "inventory_complete": inventory_complete,
        }
        if selected.reason_code is not None:
            trace["error_classification"] = selected.reason_code
        exposed_candidates = selected.candidates if inventory_complete else []
        trace["exposed_candidate_count"] = len(exposed_candidates)
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=exposed_candidates,
            reason_code=reason_code,
            retryable=reason_spec(reason_code).retryable,
            inventory_scope=scope,
            inventory_complete=inventory_complete,
            trace=trace,
        )


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self._parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "script":
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._parts is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._parts is not None:
            self.scripts.append("".join(self._parts))
            self._parts = None


def _page_config(
    html: str,
    parsed_page,
) -> tuple[
    str,
    str,
    str | None,
    str,
    int,
    tuple[str, ...],
    str | None,
    tuple[str, str] | None,
] | None:
    if parsed_page is None or not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    parser = _ScriptParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None

    api_urls: set[str] = set()
    org_ids: set[str] = set()
    smartpost_orgs: set[str] = set()
    detail_paths: set[str] = set()
    filter_sets: set[tuple[str, ...]] = set()
    boosts: set[str] = set()
    sorts: set[tuple[str, str]] = set()
    limit = _DEFAULT_LIMIT
    for raw_script in parser.scripts:
        if len(raw_script) > _MAX_SCRIPT_CHARS:
            continue
        script = _strip_js_comments(raw_script)
        for match in _SMARTPOST_ORG.finditer(script):
            smartpost_orgs.add(match.group(2))
        for name, argument in _calls(script):
            if name == "set_api":
                value = _string_literal(argument)
                api = _safe_api_url(value) if value is not None else None
                if api is None:
                    return None
                api_urls.add(api)
                continue
            if name == "sortby":
                values = _string_array_literal(f"[{argument}]")
                if (
                    values is None
                    or len(values) != 2
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", values[0])
                    or values[1] not in {"ascending", "descending"}
                ):
                    return None
                sorts.add((values[0], values[1]))
                continue
            if not argument.startswith("{") or not argument.endswith("}"):
                return None
            option_matches = list(_OPTION.finditer(argument))
            filters_matches = list(_FILTERS_OPTION.finditer(argument))
            declared = [match.group(1) for match in _PROTOCOL_OPTION.finditer(argument)]
            matched = [match.group(1) for match in option_matches]
            matched.extend("filters" for _ in filters_matches)
            if sorted(declared) != sorted(matched):
                return None
            for match in option_matches:
                key = match.group(1)
                value = match.group(3) if match.group(2) else match.group(4)
                value = value.strip() if isinstance(value, str) else value
                if key == "org_id":
                    if not isinstance(value, str) or not _ORG_ID.fullmatch(value):
                        return None
                    org_ids.add(value)
                elif key == "jobdetail_path":
                    path = _safe_detail_path(value)
                    if path is None:
                        return None
                    detail_paths.add(path)
                elif key == "limit":
                    parsed_limit = int(value)
                    if not 1 <= parsed_limit <= _MAX_LIMIT:
                        return None
                    limit = parsed_limit
                elif key == "boost":
                    criterion = _criterion(value)
                    if criterion is None:
                        return None
                    boosts.add(criterion)
            for match in filters_matches:
                filters = _string_array_literal(match.group(1))
                if filters is None:
                    return None
                filter_sets.add(filters)

    if (
        len(api_urls) != 1
        or len(org_ids) != 1
        or len(smartpost_orgs) > 1
        or len(detail_paths) != 1
        or len(filter_sets) > 1
        or len(boosts) > 1
        or len(sorts) > 1
    ):
        return None
    filters = next(iter(filter_sets), ())
    boost = next(iter(boosts), None)
    sort = next(iter(sorts), None)
    return (
        next(iter(api_urls)),
        next(iter(org_ids)),
        next(iter(smartpost_orgs), None),
        next(iter(detail_paths)),
        limit,
        filters,
        boost,
        sort,
    )


def _calls(script: str):
    for match in _CALL.finditer(script):
        start = match.end()
        depth = 1
        quote_char: str | None = None
        escaped = False
        for index in range(start, len(script)):
            char = script[index]
            if quote_char is not None:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote_char:
                    quote_char = None
                continue
            if char in "'\"":
                quote_char = char
            elif char in "({[":
                depth += 1
            elif char in ")}]":
                depth -= 1
                if depth == 0:
                    yield match.group(1), script[start:index].strip()
                    break


def _strip_js_comments(source: str) -> str:
    output: list[str] = []
    index = 0
    quote_char: str | None = None
    escaped = False
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if quote_char is not None:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote_char:
                quote_char = None
            index += 1
        elif char in "'\"`":
            quote_char = char
            output.append(char)
            index += 1
        elif char == "/" and following == "/":
            index += 2
            while index < len(source) and source[index] not in "\r\n":
                index += 1
        elif char == "/" and following == "*":
            end = source.find("*/", index + 2)
            index = len(source) if end < 0 else end + 2
        else:
            output.append(char)
            index += 1
    return "".join(output)


def _string_literal(value: str) -> str | None:
    match = re.fullmatch(r"(['\"])(.*?)\1", value.strip(), re.DOTALL)
    if match is None or "\\" in match.group(2):
        return None
    return match.group(2).strip()


def _string_array_literal(value: str) -> tuple[str, ...] | None:
    text = value.strip()
    if not text.startswith("[") or not text.endswith("]"):
        return None
    body = text[1:-1].strip()
    if not body:
        return ()

    values: list[str] = []
    start = 0
    quote_char: str | None = None
    escaped = False
    for index, char in enumerate(body):
        if quote_char is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote_char:
                quote_char = None
        elif char in "'\"":
            quote_char = char
        elif char == ",":
            item = _string_literal(body[start:index])
            criterion = _criterion(item)
            if criterion is None:
                return None
            values.append(criterion)
            start = index + 1
    if quote_char is not None:
        return None
    item = _string_literal(body[start:])
    criterion = _criterion(item)
    if criterion is None:
        return None
    values.append(criterion)
    if len(values) > _MAX_FILTERS or len(set(values)) != len(values):
        return None
    return tuple(values)


def _criterion(value) -> str | None:
    cleaned = _clean(value)
    if (
        cleaned is None
        or len(cleaned) > _MAX_CRITERION_CHARS
        or _has_controls(cleaned)
    ):
        return None
    return cleaned


def _safe_api_url(value: str | None) -> str | None:
    parsed = _safe_public_https_url(value) if value is not None else None
    host = (parsed.hostname or "").casefold() if parsed is not None else ""
    if (
        parsed is None
        or not host.endswith(".m-cloud.io")
        or parsed.path != "/api/"
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f"https://{host}/api/"


def _safe_detail_path(value: str | None) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(
        r"/[A-Za-z0-9][A-Za-z0-9/_-]{0,199}", value
    ):
        return None
    if "//" in value or any(segment in {".", ".."} for segment in value.split("/")):
        return None
    return value.rstrip("/")


def _safe_public_https_url(value: str | None):
    try:
        parsed = urlparse(value or "")
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or "." not in host
        or not _HOSTNAME.fullmatch(host)
        or any(not label or len(label) > 63 for label in host.split("."))
        or host == "localhost"
        or host.endswith((".localhost", ".local"))
    ):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return None
    return parsed


def _identifier(
    board_url: str,
    api_url: str,
    org_id: str,
    smartpost_org: str | None,
    detail_path: str,
    limit: int,
    filters: tuple[str, ...],
    boost: str | None,
    sort: tuple[str, str] | None,
    *,
    include_smartpost: bool = True,
) -> str:
    identity = {
        "api_url": api_url,
        "board_url": board_url,
        "detail_path": detail_path,
        "filters": list(filters),
        "limit": limit,
        "org_id": org_id,
        "boost": boost,
        "sort": list(sort) if sort is not None else None,
    }
    if include_smartpost:
        identity["smartpost_org"] = smartpost_org
    return json.dumps(
        identity,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _board_identity(
    board: JobBoard,
) -> tuple[
    str,
    str,
    str | None,
    str,
    str,
    int,
    tuple[str, ...],
    str | None,
    tuple[str, str] | None,
] | None:
    if board.provider != "cws" or not board.replay_safe or not board.identifier:
        return None
    parsed_board = _safe_public_https_url(board.url)
    try:
        value = json.loads(board.identifier)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    base_keys = {
        "api_url", "board_url", "boost", "detail_path", "filters", "limit", "org_id",
        "sort",
    }
    if (
        parsed_board is None
        or not isinstance(value, dict)
        or set(value) not in {frozenset(base_keys), frozenset(base_keys | {"smartpost_org"})}
    ):
        return None
    has_smartpost_key = "smartpost_org" in value
    api_url = _safe_api_url(value.get("api_url"))
    org_id = value.get("org_id")
    smartpost_org = value.get("smartpost_org")
    detail_path = _safe_detail_path(value.get("detail_path"))
    limit = value.get("limit")
    raw_filters = value.get("filters")
    filters = (
        tuple(raw_filters)
        if isinstance(raw_filters, list)
        and all(isinstance(item, str) for item in raw_filters)
        else None
    )
    boost = value.get("boost")
    raw_sort = value.get("sort")
    sort = tuple(raw_sort) if isinstance(raw_sort, list) else None
    if (
        value.get("board_url") != board.url
        or parsed_board.query
        or parsed_board.fragment
        or api_url != value.get("api_url")
        or not isinstance(org_id, str)
        or not _ORG_ID.fullmatch(org_id)
        or (
            smartpost_org is not None
            and (
                not isinstance(smartpost_org, str)
                or not re.fullmatch(r"[0-9]{1,20}", smartpost_org)
            )
        )
        or detail_path != value.get("detail_path")
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= _MAX_LIMIT
        or filters is None
        or len(filters) > _MAX_FILTERS
        or len(set(filters)) != len(filters)
        or any(_criterion(item) != item for item in filters)
        or (boost is not None and _criterion(boost) != boost)
        or (
            sort is not None
            and (
                len(sort) != 2
                or not all(isinstance(item, str) for item in sort)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", sort[0])
                or sort[1] not in {"ascending", "descending"}
            )
        )
        or board.identifier
        != _identifier(
            board.url,
            api_url,
            org_id,
            smartpost_org,
            detail_path,
            limit,
            filters,
            boost,
            sort,
            include_smartpost=has_smartpost_key,
        )
    ):
        return None
    return (
        board.url,
        api_url,
        org_id,
        smartpost_org,
        detail_path,
        limit,
        filters,
        boost,
        sort,
    )


def _request_url(
    endpoint: str,
    title: str,
    org_id: str,
    limit: int,
    offset: int,
    filters: tuple[str, ...],
    boost: str | None,
    sort: tuple[str, str] | None,
) -> str:
    criteria = [("SearchText", title)]
    if sort is not None:
        criteria.extend((("sortfield", sort[0]), ("sortorder", sort[1])))
    criteria.extend(("facet[]", item) for item in filters)
    if boost is not None:
        criteria.append(("boost", boost))
    criteria.extend(
        (
            ("Limit", str(limit)),
            ("Organization", org_id),
            ("offset", str(offset)),
            ("callback", "CWS.jobs.jobCallback"),
        )
    )
    return endpoint + "?" + urlencode(criteria)


def _collect_inventory(
    fetcher,
    *,
    endpoint: str,
    title: str,
    org_id: str,
    board_url: str,
    detail_path: str,
    limit: int,
    filters: tuple[str, ...],
    boost: str | None,
    sort: tuple[str, str] | None,
    require_smartpost_org: bool = False,
) -> _InventoryRun:
    candidates: list[JobCandidate] = []
    seen_ids: set[str] = set()
    expected_total: int | None = None
    pages_fetched = 0
    records_seen = 0
    response_source: str | None = None
    stop_reason = "not_started"
    failure_reason: str | None = None
    closed_count = 0

    for page_number in range(_MAX_PAGES):
        offset = page_number * limit + 1
        request_url = _request_url(
            endpoint,
            title,
            org_id,
            limit,
            offset,
            filters,
            boost,
            sort,
        )
        try:
            response = fetcher.fetch(
                request_url,
                headers={"Accept": "application/javascript, application/json"},
            )
        except (FetchError, OSError, TimeoutError) as error:
            failure_reason = _fetch_reason(error)
            stop_reason = "fetch_failed"
            break

        pages_fetched += 1
        if not _same_response_endpoint(response.final_url or response.url, request_url):
            failure_reason = "PROVIDER_VARIANT_UNSUPPORTED"
            stop_reason = "unsafe_response_url"
            break
        response_source = response_source or response.source
        payload = _payload(response.html)
        parsed = _inventory_page(
            payload,
            org_id,
            require_smartpost_org=require_smartpost_org,
        )
        if isinstance(parsed, str):
            failure_reason = "INVALID_STRUCTURED_DATA"
            stop_reason = parsed
            break
        rows, total = parsed
        if expected_total is None:
            expected_total = total
            if total > _MAX_ROWS:
                failure_reason = "FETCH_BUDGET_EXHAUSTED"
                stop_reason = "row_cap_exceeded"
                break
        elif total != expected_total:
            failure_reason = "INVALID_STRUCTURED_DATA"
            stop_reason = "contradictory_total"
            break

        remaining = total - records_seen
        expected_rows = min(limit, remaining)
        if remaining < 0 or len(rows) != expected_rows:
            failure_reason = "INVALID_STRUCTURED_DATA"
            stop_reason = "pagination_count_mismatch"
            break

        page_candidates: list[JobCandidate] = []
        for row in rows:
            if require_smartpost_org:
                status = _clean(row.get("entity_status"))
                if status is None:
                    failure_reason = "INVALID_STRUCTURED_DATA"
                    stop_reason = "missing_opening_status"
                    break
                if status.casefold() != "open":
                    closed_count += 1
                    continue
            candidate = _candidate(row, board_url, org_id, detail_path)
            if candidate is None:
                failure_reason = "INVALID_STRUCTURED_DATA"
                stop_reason = "invalid_or_cross_tenant_record"
                break
            job_id = candidate.raw["job_id"].casefold()
            if job_id in seen_ids:
                failure_reason = "INVALID_STRUCTURED_DATA"
                stop_reason = "duplicate_job_id"
                break
            seen_ids.add(job_id)
            page_candidates.append(candidate)
        if failure_reason is not None:
            break
        candidates.extend(page_candidates)
        records_seen += len(rows)
        if records_seen == total:
            stop_reason = "complete"
            break
    else:
        failure_reason = "FETCH_BUDGET_EXHAUSTED"
        stop_reason = "page_cap_reached"

    if failure_reason is None and expected_total != records_seen:
        failure_reason = "FETCH_BUDGET_EXHAUSTED"
        stop_reason = "page_cap_reached"
    return _InventoryRun(
        candidates=candidates,
        reason_code=failure_reason,
        pages_fetched=pages_fetched,
        records_seen=records_seen,
        expected_total=expected_total,
        response_source=response_source,
        stop_reason=stop_reason,
        closed_count=closed_count,
    )


def _internal_title_variants(title: str) -> tuple[tuple[str, str], ...]:
    variants: list[tuple[str, str]] = []
    for value in re.findall(r"\(([^()]*)\)", title):
        compact = "".join(re.findall(r"[A-Za-z0-9]+", value))
        if 2 <= len(compact) <= 30:
            variants.append(("parenthetical_compact", compact))
    variants.append(("full_title", title))
    return tuple(dict.fromkeys(variants))


def _title_key(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", value.casefold()))


def _same_response_endpoint(actual_url: str, request_url: str) -> bool:
    actual = _safe_public_https_url(actual_url)
    expected = urlparse(request_url)
    return bool(
        actual is not None
        and actual.hostname == expected.hostname
        and actual.path == expected.path
        and parse_qsl(actual.query, keep_blank_values=True)
        == parse_qsl(expected.query, keep_blank_values=True)
        and not actual.fragment
    )


def _payload(raw: str):
    if not isinstance(raw, str) or len(raw) > _MAX_RESPONSE_CHARS:
        return None
    text = raw.strip()
    callback = "CWS.jobs.jobCallback"
    if text.startswith(callback):
        match = re.fullmatch(
            re.escape(callback) + r"\s*\(\s*([\s\S]*)\s*\)\s*;?",
            text,
        )
        if match is None:
            return None
        text = match.group(1)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _inventory_page(
    payload,
    org_id: str,
    *,
    require_smartpost_org: bool = False,
) -> tuple[list[dict], int] | str:
    if not isinstance(payload, dict) or set(payload) - {
        "totalHits", "queryResult", "organization", "Organization", "org_id",
        "aggregations", "histogramResults", "location", "titles",
    }:
        return "invalid_response_schema"
    total = payload.get("totalHits")
    rows = payload.get("queryResult")
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total < 0
        or not isinstance(rows, list)
        or any(not isinstance(row, dict) for row in rows)
    ):
        return "invalid_response_schema"
    if require_smartpost_org:
        if any(str(row.get("scout_orgid")) != org_id for row in rows):
            return "cross_tenant_response"
    elif not _org_continuity(payload, org_id):
        return "cross_tenant_response"
    return rows, total


def _org_continuity(value, expected: str) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.casefold() in {"organization", "org_id", "organization_id"}:
                if not isinstance(item, str) or item != expected:
                    return False
            elif isinstance(item, (dict, list)) and not _org_continuity(item, expected):
                return False
    elif isinstance(value, list):
        return all(_org_continuity(item, expected) for item in value)
    return True


def _candidate(
    row: dict,
    board_url: str,
    org_id: str,
    detail_path: str,
) -> JobCandidate | None:
    raw_job_id = row.get("id")
    job_id = (
        str(raw_job_id)
        if (
            not isinstance(raw_job_id, bool)
            and isinstance(raw_job_id, int)
            and 0 <= raw_job_id <= 10**20 - 1
        )
        else _clean(raw_job_id)
    )
    title = _clean(row.get("title"))
    if job_id is None or title is None or not _JOB_ID.fullmatch(job_id):
        return None
    parsed_board = urlparse(board_url)
    city = _clean(row.get("primary_city"))
    state = _clean(row.get("primary_state"))
    country = _clean(row.get("primary_country"))
    slug = _slug(title, city, state or country)
    if not slug:
        return None
    path = f"{detail_path}/{quote(job_id, safe='._:-')}/{quote(slug, safe='-')}"
    detail_url = urlunparse(parsed_board._replace(path=path, query="", fragment=""))
    parsed_detail = _safe_public_https_url(detail_url)
    if parsed_detail is None or parsed_detail.hostname != parsed_board.hostname:
        return None
    parts = [
        city,
        state,
        country,
    ]
    location = ", ".join(part for part in parts if part) or None
    return JobCandidate(
        title=title,
        url=detail_url,
        provider="cws",
        location=location,
        raw={"job_id": job_id, "org_id": org_id},
    )


def _slug(title: str, city: str | None, region: str | None) -> str:
    if city is None or region is None:
        return ""
    source = " ".join((title, city, region))
    if not source.isascii():
        return ""
    words = re.findall(r"[A-Za-z0-9]+", source.casefold())
    return "-".join(words)[:200]


def _clean(value) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _has_controls(value: str) -> bool:
    return any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)


def _fetch_reason(error: Exception) -> str:
    typed = getattr(error, "reason_code", None)
    if isinstance(typed, str) and typed:
        return typed
    classified = classify_fetch_error(str(error))
    return "PROVIDER_FETCH_FAILED" if classified == "FETCH_FAILED" else classified


def _failure(
    board: JobBoard,
    scope: str,
    reason_code: str,
    stop_reason: str,
) -> AdapterResult:
    return AdapterResult(
        provider="cws",
        board=board,
        reason_code=reason_code,
        retryable=reason_spec(reason_code).retryable,
        inventory_scope=scope,
        inventory_complete=False,
        trace={
            "adapter": "cws",
            "variant": "m_cloud_jsonp",
            "stop_reason": stop_reason,
            "error_classification": reason_code,
            "inventory_scope": scope,
            "inventory_complete": False,
        },
    )


ADAPTER = CWSAdapter()
