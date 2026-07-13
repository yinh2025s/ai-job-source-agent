from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

from ..reasons import REASON_SPECS, classify_fetch_error, reason_spec
from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_WIDGET_SCRIPT_URL = "https://jobsapi.ceipal.com/APISource/widget.js"
_CAREER_API_URL = "https://careerapi.ceipal.com/careerPortalWidget/"
_IFRAME_HOST = "jobsapi.ceipal.com"
_IFRAME_PATH = "/APISource/v1/index.html"
_IFRAME_REFERER = f"https://{_IFRAME_HOST}{_IFRAME_PATH}"
_INVENTORY_HOST = "careerapi.ceipal.com"
_INVENTORY_METHOD = "CareerPortalJobPostings"
_INVENTORY_ENDPOINT_LABEL = "career_portal_job_postings"
_REFERER_HOST = "https://jobsapi.ceipal.com/"
_MULTIPART_BOUNDARY = "----AIJobSourceAgentCEIPALBoundary7MA4YWxkTrZu0gW"
_MAX_HTML_CHARS = 2_000_000
_MAX_FIELD_CHARS = 20_000
_MAX_PAGES = 50
_OMITTABLE_EMPTY_RESPONSE_PARAMS = frozenset({"themeid", "bgcolor", "job_id"})
_HEX_COLOR = re.compile(r"[0-9a-fA-F]{6}")
_SAFE_JOB_ID = re.compile(r"[^\x00-\x1f\x7f]{1,1000}")


class CeipalAdapter:
    name = "ceipal"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        # CEIPAL widgets live on first-party pages and require DOM evidence.
        return False

    def identify_board(self, url: str) -> JobBoard | None:
        return None

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        parsed = _safe_first_party_url(page_url)
        if parsed is None:
            return None
        tenant = _widget_tenant(page.html)
        if tenant is None:
            return None
        api_key, career_portal_id = tenant
        origin = _origin(parsed)
        return JobBoard(
            url=urlunparse(parsed._replace(query="", fragment="")),
            provider=self.name,
            identifier=_identifier(origin, api_key, career_portal_id),
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identity = _board_identity(board)
        search_title = query.title.strip() if isinstance(query.title, str) else None
        if not search_title:
            search_title = None
        scope = "title_filtered" if search_title is not None else "full"
        if identity is None:
            return _unsupported(board, "invalid CEIPAL board", scope=scope)
        api_key, career_portal_id = identity

        iframe_result = self._fetch_iframe(
            fetcher,
            board,
            api_key,
            career_portal_id,
            scope,
        )
        if isinstance(iframe_result, AdapterResult):
            return iframe_result

        return self._fetch_inventory(
            fetcher,
            board,
            search_title,
            api_key,
            career_portal_id,
            scope,
        )

    def _fetch_iframe(
        self,
        fetcher,
        board: JobBoard,
        api_key: str,
        career_portal_id: str,
        scope: str,
    ) -> str | AdapterResult:
        request_url = _career_api_url(api_key, career_portal_id)
        try:
            response = fetcher.fetch(
                request_url,
                headers={
                    "Accept": "application/json",
                    "X-Referer-Host": _REFERER_HOST,
                },
            )
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, error, scope=scope, phase="wrapper")

        response_url = response.final_url or response.url
        if not _response_url_matches_request(response_url, request_url):
            return _unsupported(
                board,
                "CEIPAL API response URL did not match the frozen widget endpoint",
                scope=scope,
            )
        try:
            payload = json.loads(response.html)
        except (json.JSONDecodeError, TypeError, ValueError):
            return _unsupported(board, "unrecognized CEIPAL response schema", scope=scope)

        if _is_bot_response(payload):
            return _bot_protection(board, scope=scope, phase="wrapper")
        if not isinstance(payload, dict) or not isinstance(payload.get("html"), str):
            return _unsupported(board, "unrecognized CEIPAL response schema", scope=scope)

        iframe_url = _single_iframe_url(payload["html"])
        if iframe_url is None or not _valid_iframe_url(
            iframe_url,
            api_key,
            career_portal_id,
        ):
            return _unsupported(board, "unsupported CEIPAL iframe variant", scope=scope)

        try:
            iframe_page = fetcher.fetch(iframe_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, error, scope=scope, phase="iframe")
        final_iframe_url = iframe_page.final_url or iframe_page.url
        if not _valid_iframe_url(final_iframe_url, api_key, career_portal_id):
            return _unsupported(board, "unsafe CEIPAL iframe redirect", scope=scope)
        if _is_bot_response(iframe_page.html):
            return _bot_protection(board, scope=scope, phase="iframe")
        if not _has_inventory_structure(iframe_page.html):
            return _unsupported(board, "unrecognized CEIPAL iframe structure", scope=scope)
        return iframe_url

    def _fetch_inventory(
        self,
        fetcher,
        board: JobBoard,
        search_title: str | None,
        api_key: str,
        career_portal_id: str,
        scope: str,
    ) -> AdapterResult:
        candidates: list[JobCandidate] = []
        seen_ids: set[str] = set()
        seen_next_pages: set[int] = set()
        expected_count: int | None = None
        expected_limit: int | None = None
        expected_pages: int | None = None
        records_seen = 0
        pages_fetched = 0
        stop_reason = "not_started"
        failure_reason: str | None = None
        retryable = False
        complete = False

        for page_number in range(1, _MAX_PAGES + 1):
            request_url = _inventory_url(api_key, page_number)
            body = _inventory_body(
                page_number,
                api_key,
                career_portal_id,
                search_title,
            )
            try:
                response = fetcher.fetch(
                    request_url,
                    data=body,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": f"multipart/form-data; boundary={_MULTIPART_BOUNDARY}",
                        "Origin": "https://jobsapi.ceipal.com",
                        "Referer": _IFRAME_REFERER,
                    },
                )
            except (FetchError, OSError, TimeoutError) as error:
                failure_reason = _fetch_reason(error)
                retryable = reason_spec(failure_reason).retryable
                stop_reason = "inventory_fetch_failed"
                break

            pages_fetched += 1
            final_url = response.final_url or response.url
            if not _valid_inventory_response_url(final_url, api_key, page_number):
                failure_reason = "PROVIDER_VARIANT_UNSUPPORTED"
                stop_reason = "unsafe_inventory_response_url"
                break
            if _is_bot_response(response.html):
                failure_reason = "BOT_PROTECTION"
                stop_reason = "bot_protection"
                break
            try:
                payload = json.loads(response.html)
            except (json.JSONDecodeError, TypeError, ValueError):
                failure_reason = "INVALID_STRUCTURED_DATA"
                stop_reason = "invalid_inventory_json"
                break

            parsed = _parse_inventory_page(
                payload,
                requested_page=page_number,
                api_key=api_key,
                expected_count=expected_count,
                expected_limit=expected_limit,
                expected_pages=expected_pages,
                seen_ids=seen_ids,
                seen_next_pages=seen_next_pages,
            )
            if isinstance(parsed, str):
                failure_reason = "INVALID_STRUCTURED_DATA"
                stop_reason = parsed
                break

            if expected_count is None:
                expected_count = parsed.count
                expected_limit = parsed.limit
                expected_pages = parsed.page_count

            for record in parsed.records:
                job_id = record["id"]
                seen_ids.add(job_id)
                records_seen += 1
                candidates.append(_candidate(board, record))

            if parsed.next_page is None:
                if records_seen == parsed.count and parsed.terminal:
                    complete = True
                    stop_reason = "complete"
                else:
                    failure_reason = "INVALID_STRUCTURED_DATA"
                    stop_reason = "premature_terminal_page"
                break
            seen_next_pages.add(parsed.next_page)
        else:
            failure_reason = "FETCH_BUDGET_EXHAUSTED"
            retryable = True
            stop_reason = "page_cap_reached"

        if not complete and failure_reason is None:
            failure_reason = "FETCH_BUDGET_EXHAUSTED"
            retryable = True
            stop_reason = "page_cap_reached"

        reason_code = failure_reason
        if complete:
            reason_code = None if candidates else "EMPTY_PROVIDER_RESPONSE"
            retryable = False

        trace = {
            "adapter": self.name,
            "variant": "public_inventory_v1",
            "endpoint": _CAREER_API_URL,
            "inventory_endpoint_label": _INVENTORY_ENDPOINT_LABEL,
            "candidate_count": len(candidates),
            "records_seen": records_seen,
            "total": expected_count,
            "page_count": pages_fetched,
            "expected_page_count": expected_pages,
            "stop_reason": stop_reason,
            "inventory_scope": scope,
            "inventory_complete": complete,
        }
        if failure_reason is not None:
            trace["error_classification"] = failure_reason
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=reason_code,
            retryable=retryable,
            inventory_scope=scope,
            inventory_complete=complete,
            trace=trace,
        )


class _WidgetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tenants: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        values = {name.casefold(): value for name, value in attrs}
        if values.get("src") != _WIDGET_SCRIPT_URL:
            return
        api_key = (values.get("data-ceipal-api-key") or "").strip()
        career_portal_id = (values.get("data-ceipal-career-portal-id") or "").strip()
        if api_key and career_portal_id:
            self.tenants.append((api_key, career_portal_id))


class _IframeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "iframe":
            return
        values = {name.casefold(): value for name, value in attrs}
        source = values.get("src")
        if isinstance(source, str) and source.strip():
            self.sources.append(source.strip())


class _InventoryStructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.official_script = False
        self.listing_structure = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): value for name, value in attrs}
        if tag.casefold() == "script":
            source = values.get("src") or ""
            try:
                parsed = urlparse(urljoin(_IFRAME_REFERER, source))
                port = parsed.port
            except (TypeError, ValueError):
                parsed = None
                port = None
            if (
                parsed is not None
                and parsed.scheme == "https"
                and parsed.username is None
                and parsed.password is None
                and port in {None, 443}
                and (parsed.hostname or "").casefold() == _IFRAME_HOST
                and parsed.path == "/APISource/v1/js/app.min.js"
                and not parsed.query
                and not parsed.fragment
            ):
                self.official_script = True
        marker = " ".join(
            str(values.get(name) or "").casefold() for name in ("id", "class", "name")
        )
        if "careerportaljobpostings" in marker or "job-list" in marker:
            self.listing_structure = True


@dataclass(frozen=True)
class _InventoryPage:
    records: list[dict[str, str]]
    count: int
    limit: int
    page_count: int
    next_page: int | None
    terminal: bool


def _widget_tenant(html: str) -> tuple[str, str] | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    parser = _WidgetParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    unique = list(dict.fromkeys(parser.tenants))
    return unique[0] if len(unique) == 1 else None


def _single_iframe_url(html: str) -> str | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    parser = _IframeParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    return parser.sources[0] if len(parser.sources) == 1 else None


def _has_inventory_structure(html: str) -> bool:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return False
    parser = _InventoryStructureParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return False
    return parser.official_script or parser.listing_structure


def _safe_first_party_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    return parsed


def _origin(parsed) -> str:
    host = (parsed.hostname or "").casefold()
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}"


def _identifier(origin: str, api_key: str, career_portal_id: str) -> str:
    return json.dumps(
        {
            "api_key": api_key,
            "career_portal_id": career_portal_id,
            "origin": origin,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _board_identity(board: JobBoard) -> tuple[str, str] | None:
    if board.provider != "ceipal" or not board.identifier:
        return None
    parsed = _safe_first_party_url(board.url)
    if parsed is None or parsed.query or parsed.fragment:
        return None
    try:
        value = json.loads(board.identifier)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != {"api_key", "career_portal_id", "origin"}:
        return None
    api_key = value.get("api_key")
    career_portal_id = value.get("career_portal_id")
    if (
        not _safe_identity_value(api_key)
        or not _safe_identity_value(career_portal_id)
        or value.get("origin") != _origin(parsed)
    ):
        return None
    return api_key, career_portal_id


def _safe_identity_value(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 1000
        and value == value.strip()
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _career_api_url(api_key: str, career_portal_id: str) -> str:
    query = urlencode(
        [
            ("themeid", ""),
            ("bgcolor", ""),
            ("job_id", ""),
            ("apikey", api_key),
            ("cp_id", career_portal_id),
        ]
    )
    return f"{_CAREER_API_URL}?{query}"


def _valid_iframe_url(url: str, api_key: str, career_portal_id: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or (parsed.hostname or "").casefold() != _IFRAME_HOST
        or parsed.path != _IFRAME_PATH
        or parsed.params
        or parsed.fragment
        or len({name for name, _value in pairs}) != len(pairs)
    ):
        return False
    values = dict(pairs)
    if set(values) != {"api_key", "cp_id", "job_id", "bgcolor"}:
        return False
    return (
        values["api_key"] == api_key
        and values["cp_id"] == career_portal_id
        and values["job_id"] == ""
        and (values["bgcolor"] == "" or _HEX_COLOR.fullmatch(values["bgcolor"]) is not None)
    )


def _inventory_url(api_key: str, page_number: int) -> str:
    encoded_key = quote(api_key, safe="")
    return f"https://{_INVENTORY_HOST}/{encoded_key}/{_INVENTORY_METHOD}/?page={page_number}"


def _valid_inventory_response_url(url: str, api_key: str, page_number: int) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and (parsed.hostname or "").casefold() == _INVENTORY_HOST
        and parsed.path == f"/{quote(api_key, safe='')}/{_INVENTORY_METHOD}/"
        and not parsed.params
        and not parsed.fragment
        and pairs == [("page", str(page_number))]
    )


def _inventory_body(
    page_number: int,
    api_key: str,
    career_portal_id: str,
    title: str | None,
) -> bytes:
    fields = [
        ("page", str(page_number)),
        ("api_key", api_key),
        ("method", _INVENTORY_METHOD),
        ("cp_id", career_portal_id),
        ("from_career_portal", "1"),
    ]
    if title is not None:
        fields.append(("searchkey", title))
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.extend(
            [
                f"--{_MULTIPART_BOUNDARY}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.append(f"--{_MULTIPART_BOUNDARY}--\r\n".encode("ascii"))
    return b"".join(chunks)


def _parse_inventory_page(
    payload: object,
    *,
    requested_page: int,
    api_key: str,
    expected_count: int | None,
    expected_limit: int | None,
    expected_pages: int | None,
    seen_ids: set[str],
    seen_next_pages: set[int],
) -> _InventoryPage | str:
    if not isinstance(payload, dict):
        return "invalid_inventory_schema"
    if "next" not in payload or "previous" not in payload:
        return "invalid_inventory_schema"
    results = payload.get("results")
    count = _strict_int(payload.get("count"), minimum=0)
    limit = _strict_int(payload.get("limit"), minimum=1)
    page_number = _strict_int(payload.get("page_number"), minimum=1)
    num_pages = _strict_int(payload.get("num_pages"), minimum=0)
    page_count = _strict_int(payload.get("page_count"), minimum=0)
    if (
        not isinstance(results, list)
        or count is None
        or limit is None
        or page_number != requested_page
        or num_pages is None
        or page_count is None
        or page_count != len(results)
    ):
        return "invalid_inventory_schema"
    total_pages = num_pages
    mathematically_expected_pages = (count + limit - 1) // limit if count else 0
    if total_pages != mathematically_expected_pages or len(results) > limit:
        return "inconsistent_pagination_metadata"
    if (
        expected_count is not None
        and (count != expected_count or limit != expected_limit or total_pages != expected_pages)
    ):
        return "unstable_pagination_metadata"
    if count == 0 and (requested_page != 1 or results):
        return "invalid_empty_inventory"
    if count > 0 and (requested_page > total_pages or not results):
        return "invalid_page_records"

    previous = payload.get("previous")
    next_value = payload.get("next")
    if previous is not None and not isinstance(previous, str):
        return "invalid_previous_page"
    if next_value is not None and not isinstance(next_value, str):
        return "invalid_next_page"
    if requested_page == 1:
        if previous not in {None, ""}:
            return "unexpected_previous_page"
    elif not previous or _pagination_target(previous, api_key) != requested_page - 1:
        return "previous_page_mismatch"

    terminal = requested_page == total_pages if total_pages else requested_page == 1
    next_page: int | None = None
    if terminal:
        if next_value not in {None, ""}:
            return "unexpected_next_page"
    else:
        if not next_value:
            return "missing_next_page"
        next_page = _pagination_target(next_value, api_key)
        if next_page in seen_next_pages:
            return "pagination_cycle"
        if next_page != requested_page + 1:
            return "pagination_mismatch"

    records: list[dict[str, str]] = []
    page_ids: set[str] = set()
    for value in results:
        record = _parse_record(value)
        if record is None:
            return "invalid_inventory_record"
        job_id = record["id"]
        if job_id in seen_ids or job_id in page_ids:
            return "duplicate_job_id"
        page_ids.add(job_id)
        records.append(record)

    return _InventoryPage(records, count, limit, total_pages, next_page, terminal)


def _parse_record(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    job_id = value.get("id")
    title = value.get("public_job_title")
    if not _bounded_public_text(job_id, _SAFE_JOB_ID) or not _bounded_public_text(title):
        return None
    record = {"id": job_id, "public_job_title": title}
    for name in (
        "state",
        "country",
        "multpile_job_location",
        "remote_opportunities",
        "updated",
    ):
        field = value.get(name)
        if field is None:
            continue
        if not isinstance(field, (str, int, float, bool)):
            return None
        text = str(field).strip()
        if len(text) > _MAX_FIELD_CHARS:
            return None
        if text:
            record[name] = text
    return record


def _bounded_public_text(value: object, pattern=None) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= _MAX_FIELD_CHARS
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        and (pattern is None or pattern.fullmatch(value) is not None)
    )


def _pagination_target(value: str, api_key: str) -> int | None:
    try:
        absolute = urljoin(_inventory_url(api_key, 1), value)
        parsed = urlparse(absolute)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return None
    expected_paths = {
        f"/{quote(api_key, safe='')}/{_INVENTORY_METHOD}/",
        f"/{quote('[REDACTED]', safe='')}/{_INVENTORY_METHOD}/",
        f"/[REDACTED]/{_INVENTORY_METHOD}/",
    }
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or (parsed.hostname or "").casefold() != _INVENTORY_HOST
        or parsed.path not in expected_paths
        or parsed.params
        or parsed.fragment
        or len(pairs) != 1
        or pairs[0][0] != "page"
    ):
        return None
    value = pairs[0][1]
    if not value.isascii() or not value.isdecimal():
        return None
    number = int(value)
    return number if number >= 1 else None


def _strict_int(value: object, *, minimum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


def _candidate(board: JobBoard, record: dict[str, str]) -> JobCandidate:
    job_id = record["id"]
    query = urlencode({"job_id": job_id}, quote_via=quote)
    raw = {"job_id": job_id}
    if "updated" in record:
        raw["updated"] = record["updated"]
    return JobCandidate(
        title=record["public_job_title"],
        url=urlunparse(urlparse(board.url)._replace(query=query, fragment="")),
        provider="ceipal",
        location=_record_location(record),
        raw=raw,
    )


def _record_location(record: dict[str, str]) -> str | None:
    remote = (record.get("remote_opportunities") or "").strip().casefold()
    if remote in {"1", "true", "remote", "remote job"}:
        return "Remote Job"

    multiple = _meaningful_location(record.get("multpile_job_location"))
    if multiple is not None:
        return multiple

    values: list[str] = []
    for name in ("state", "country"):
        value = _meaningful_location(record.get(name))
        if value is not None and value.casefold() not in {
            item.casefold() for item in values
        }:
            values.append(value)
    return ", ".join(values) or None


def _meaningful_location(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.casefold() in {"0", "false", "n/a"}:
        return None
    return normalized or None


def _response_url_matches_request(response_url: str, request_url: str) -> bool:
    try:
        response = urlparse(response_url)
        request = urlparse(request_url)
        response_port = response.port
        request_port = request.port
        response_query = Counter(
            parse_qsl(response.query, keep_blank_values=True, strict_parsing=True)
        )
        request_query = Counter(
            parse_qsl(request.query, keep_blank_values=True, strict_parsing=True)
        )
    except (TypeError, ValueError):
        return False

    if (
        "#" in response_url
        or response.scheme != "https"
        or response.username is not None
        or response.password is not None
        or response.hostname is None
        or response.hostname.casefold() != (request.hostname or "").casefold()
        or (response_port or 443) != (request_port or 443)
        or response.path != request.path
        or response.params != request.params
    ):
        return False

    for pair, count in request_query.items():
        name, value = pair
        response_count = response_query.pop(pair, 0)
        if name in _OMITTABLE_EMPTY_RESPONSE_PARAMS and value == "":
            if response_count not in {0, count}:
                return False
        elif response_count != count:
            return False
    return not response_query


def _is_bot_response(value: object) -> bool:
    if isinstance(value, dict):
        message = value.get("message")
        return isinstance(message, str) and message.strip().casefold() == "bot access is not allowed"
    if isinstance(value, str):
        text = value.strip()
        if text.casefold() == "bot access is not allowed":
            return True
        try:
            return _is_bot_response(json.loads(text))
        except (json.JSONDecodeError, TypeError, ValueError):
            return "bot access is not allowed" in text.casefold()
    return False


def _unsupported(board: JobBoard, error: str, *, scope: str) -> AdapterResult:
    return AdapterResult(
        provider="ceipal",
        board=board,
        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
        retryable=False,
        inventory_scope=scope,
        inventory_complete=False,
        trace={
            "adapter": "ceipal",
            "variant": "public_inventory_v1",
            "endpoint": _CAREER_API_URL,
            "error": error,
            "inventory_scope": scope,
            "inventory_complete": False,
        },
    )


def _bot_protection(board: JobBoard, *, scope: str, phase: str) -> AdapterResult:
    return AdapterResult(
        provider="ceipal",
        board=board,
        reason_code="BOT_PROTECTION",
        retryable=False,
        inventory_scope=scope,
        inventory_complete=False,
        trace={
            "adapter": "ceipal",
            "variant": "public_inventory_v1",
            "endpoint": _CAREER_API_URL,
            "phase": phase,
            "inventory_scope": scope,
            "inventory_complete": False,
        },
    )


def _fetch_reason(error: Exception) -> str:
    typed = getattr(error, "reason_code", None)
    if isinstance(typed, str) and typed in REASON_SPECS:
        return typed
    reason_code = classify_fetch_error(str(error))
    return "PROVIDER_FETCH_FAILED" if reason_code == "FETCH_FAILED" else reason_code


def _fetch_failure(
    board: JobBoard,
    error: Exception,
    *,
    scope: str,
    phase: str,
) -> AdapterResult:
    reason_code = _fetch_reason(error)
    return AdapterResult(
        provider="ceipal",
        board=board,
        reason_code=reason_code,
        retryable=reason_spec(reason_code).retryable,
        inventory_scope=scope,
        inventory_complete=False,
        trace={
            "adapter": "ceipal",
            "variant": "public_inventory_v1",
            "endpoint": _CAREER_API_URL,
            "phase": phase,
            "error": f"CEIPAL request failed: {reason_code}",
            "inventory_scope": scope,
            "inventory_complete": False,
        },
    )


ADAPTER = CeipalAdapter()
