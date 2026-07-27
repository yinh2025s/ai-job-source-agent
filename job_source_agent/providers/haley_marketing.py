from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import ipaddress
import json
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from ..reasons import reason_spec
from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery, provider_fetch_reason


_CUSTOM_PREFIX = "custom:"
_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$",
    re.IGNORECASE,
)
_TOKEN_HASH = re.compile(r"^[a-f0-9]{32}$", re.IGNORECASE)
_TOKEN_TIME = re.compile(r"^[1-9][0-9]{8,12}$")
_SLUG = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,598}[A-Za-z0-9])?$")
_INVENTORY_DECLARATION = re.compile(
    r"""\bvar\s+jsonp_url\s*=\s*(['"])/json/index\.smpl\1\s*;"""
)
_MAX_HTML_CHARS = 5_000_000
_MAX_RESPONSE_CHARS = 8_000_000
_MAX_FIELD_CHARS = 20_000
_MAX_RECORDS = 2_000
_MAX_PAGES = 10
_PAGE_SIZE = 5


class HaleyMarketingAdapter:
    name = "haley_marketing"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        # Customer-owned domains require page evidence before provider identity
        # can be established.
        return False

    def identify_board(self, url: str) -> JobBoard | None:
        return None

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        origin = _safe_origin(page_url)
        if origin is None:
            return None
        parser = _parse_page(page.html)
        if parser is None:
            return None
        if (
            _inventory_config(parser, page.html, page_url) is None
            and _search_entry(parser, page_url) is None
        ):
            return None
        host = urlparse(origin).hostname or ""
        return JobBoard(
            url=f"{origin}/",
            provider=self.name,
            identifier=f"{_CUSTOM_PREFIX}{host.casefold()}",
            replay_safe=True,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identity = _board_identity(board)
        if identity is None:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                stop_reason="invalid_board_identity",
            )
        origin, expected_host = identity
        board_url = f"{origin}/"
        board_urls = [board_url]
        try:
            landing = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, error, board_urls=board_urls)
        if _safe_origin(landing.final_url or landing.url) != origin:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                board_urls=board_urls,
                rejected_final_url=landing.final_url or landing.url,
                stop_reason="board_final_url_drift",
            )

        parser = _parse_page(landing.html)
        config = (
            _inventory_config(parser, landing.html, landing.final_url or landing.url)
            if parser is not None
            else None
        )
        search_requests: list[str] = []
        if config is None and parser is not None:
            entry = _search_entry(parser, landing.final_url or landing.url)
            if entry is not None:
                search_url, search_fields = entry
                search_fields["keywords"] = (query.title or "").strip()
                data = urlencode(search_fields).encode("utf-8")
                search_requests.append(search_url)
                try:
                    landing = fetcher.fetch(
                        search_url,
                        data=data,
                        headers={
                            "Content-Type": "application/x-www-form-urlencoded",
                            "Referer": board_url,
                        },
                    )
                except (FetchError, OSError, TimeoutError) as error:
                    return _fetch_failure(
                        board,
                        error,
                        board_urls=board_urls,
                        search_requests=search_requests,
                    )
                if _safe_origin(landing.final_url or landing.url) != origin:
                    return _result(
                        board,
                        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                        inventory_complete=False,
                        board_urls=board_urls,
                        search_requests=search_requests,
                        rejected_final_url=landing.final_url or landing.url,
                        stop_reason="search_final_url_drift",
                    )
                parser = _parse_page(landing.html)
                config = (
                    _inventory_config(
                        parser,
                        landing.html,
                        landing.final_url or landing.url,
                    )
                    if parser is not None
                    else None
                )

        if config is None:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                board_urls=board_urls,
                search_requests=search_requests,
                stop_reason="inventory_contract_missing",
            )

        h_token, t_token = config
        target_title = _normalized_title(query.title)
        candidates: list[JobCandidate] = []
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        seen_offsets: set[int] = set()
        api_urls: list[str] = []
        expected_total: int | None = None
        next_offset = 0
        inventory_complete = False
        exact_title_found = False
        exact_target_found = False
        repaired_responses = 0
        archived_records = 0
        records_seen = 0
        stop_reason = "page_cap_reached"

        for _page_number in range(1, _MAX_PAGES + 1):
            if next_offset in seen_offsets:
                return _invalid(
                    board,
                    candidates,
                    board_urls,
                    search_requests,
                    api_urls,
                    repaired_responses,
                    "pagination_loop",
                )
            seen_offsets.add(next_offset)
            fields = {
                "arg": "list_posts",
                "pp": str(_PAGE_SIZE),
                "pid": "gwt",
                "h": h_token,
                "t": t_token,
                "first": str(next_offset),
                "action": "1",
                "keywords": (query.title or "").strip(),
            }
            api_url = f"{origin}/json/index.smpl?{urlencode(fields)}"
            api_urls.append(_trace_inventory_url(api_url))
            try:
                page = fetcher.fetch(
                    api_url,
                    headers={
                        "Accept": "application/json",
                        "Referer": landing.final_url or landing.url,
                    },
                )
            except (FetchError, OSError, TimeoutError) as error:
                return _fetch_failure(
                    board,
                    error,
                    candidates=candidates,
                    board_urls=board_urls,
                    search_requests=search_requests,
                    api_urls=api_urls,
                    repaired_responses=repaired_responses,
                )
            if not _same_inventory_endpoint(page.final_url or page.url, origin):
                return _result(
                    board,
                    candidates=[],
                    reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                    inventory_complete=False,
                    board_urls=board_urls,
                    search_requests=search_requests,
                    api_urls=api_urls,
                    repaired_responses=repaired_responses,
                    stop_reason="inventory_final_url_drift",
                    rejected_final_url=page.final_url or page.url,
                )

            payload, repaired = _inventory_payload(page.html)
            if payload is None:
                return _invalid(
                    board,
                    candidates,
                    board_urls,
                    search_requests,
                    api_urls,
                    repaired_responses,
                    "invalid_inventory_json",
                    response_source=page.source,
                )
            repaired_responses += int(repaired)
            parsed = _inventory_page(payload)
            if parsed is None:
                return _invalid(
                    board,
                    candidates,
                    board_urls,
                    search_requests,
                    api_urls,
                    repaired_responses,
                    "invalid_inventory_schema",
                    response_source=page.source,
                )
            records, total, response_next, response_pp, ticket = parsed
            if response_pp != _PAGE_SIZE or len(records) > _PAGE_SIZE:
                return _invalid(
                    board,
                    candidates,
                    board_urls,
                    search_requests,
                    api_urls,
                    repaired_responses,
                    "page_size_drift",
                    response_source=page.source,
                )
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                return _invalid(
                    board,
                    candidates,
                    board_urls,
                    search_requests,
                    api_urls,
                    repaired_responses,
                    "total_count_drift",
                    response_source=page.source,
                )

            for record in records:
                parsed_candidate = _candidate(record, origin, expected_host)
                if parsed_candidate is None:
                    return _invalid(
                        board,
                        candidates,
                        board_urls,
                        search_requests,
                        api_urls,
                        repaired_responses,
                        "invalid_job_record",
                        response_source=page.source,
                    )
                post_id, candidate = parsed_candidate
                if post_id in seen_ids or candidate.url in seen_urls:
                    return _invalid(
                        board,
                        candidates,
                        board_urls,
                        search_requests,
                        api_urls,
                        repaired_responses,
                        "duplicate_job",
                        response_source=page.source,
                    )
                seen_ids.add(post_id)
                seen_urls.add(candidate.url)
                records_seen += 1
                archived = _archived_state(record.get("POST_ARCHIVED"))
                if archived is None:
                    return _invalid(
                        board,
                        candidates,
                        board_urls,
                        search_requests,
                        api_urls,
                        repaired_responses,
                        "invalid_archived_state",
                        response_source=page.source,
                    )
                if archived:
                    archived_records += 1
                    continue
                candidates.append(candidate)
                if records_seen > _MAX_RECORDS:
                    return _result(
                        board,
                        reason_code="FETCH_BUDGET_EXHAUSTED",
                        retryable=True,
                        inventory_complete=False,
                        candidates=[],
                        board_urls=board_urls,
                        search_requests=search_requests,
                        api_urls=api_urls,
                        repaired_responses=repaired_responses,
                        stop_reason="record_cap_exceeded",
                    )
                if (
                    target_title
                    and _normalized_title(candidate.title) == target_title
                ):
                    exact_title_found = True
                    if _early_stop_location_matches(
                        candidate.location,
                        query.location,
                    ):
                        exact_target_found = True

            if ticket is not None:
                h_token, t_token = ticket
            if response_next == -1:
                if records_seen != total:
                    return _invalid(
                        board,
                        candidates,
                        board_urls,
                        search_requests,
                        api_urls,
                        repaired_responses,
                        "premature_inventory_end",
                        response_source=page.source,
                    )
                inventory_complete = True
                stop_reason = "complete"
                break
            if records_seen >= total:
                inventory_complete = True
                stop_reason = "complete"
                break
            if exact_target_found:
                stop_reason = "exact_title_and_location_found"
                break
            if response_next <= next_offset or not records:
                return _invalid(
                    board,
                    candidates,
                    board_urls,
                    search_requests,
                    api_urls,
                    repaired_responses,
                    "invalid_pagination_progress",
                    response_source=page.source,
                )
            next_offset = response_next

        exposed_candidates = (
            candidates if inventory_complete or exact_target_found else []
        )
        reason_code = None if exposed_candidates else (
            "EMPTY_PROVIDER_RESPONSE" if inventory_complete else "FETCH_BUDGET_EXHAUSTED"
        )
        return _result(
            board,
            candidates=exposed_candidates,
            reason_code=reason_code,
            retryable=reason_code == "FETCH_BUDGET_EXHAUSTED",
            inventory_complete=inventory_complete,
            board_urls=board_urls,
            search_requests=search_requests,
            api_urls=api_urls,
            repaired_responses=repaired_responses,
            records_seen=records_seen,
            candidate_count=len(candidates),
            archived_records=archived_records,
            expected_total=expected_total,
            exact_title_found=exact_title_found,
            exact_target_found=exact_target_found,
            stop_reason=stop_reason,
        )


@dataclass(frozen=True)
class _Form:
    action: str
    method: str
    fields: dict[str, str]
    field_names: frozenset[str]


class _HmgParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: dict[str, _Form] = {}
        self.scripts: list[str] = []
        self.stylesheets: list[str] = []
        self._form_id: str | None = None
        self._form_action = ""
        self._form_method = "GET"
        self._form_fields: dict[str, str] = {}
        self._form_field_names: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): value or "" for name, value in attrs}
        lowered = tag.casefold()
        if lowered == "script" and values.get("src"):
            self.scripts.append(values["src"])
        elif (
            lowered == "link"
            and "stylesheet" in values.get("rel", "").casefold().split()
            and values.get("href")
        ):
            self.stylesheets.append(values["href"])
        elif lowered == "form" and self._form_id is None:
            form_id = values.get("id")
            if form_id in {"JBSearchList_form", "jb_search"}:
                self._form_id = form_id
                self._form_action = values.get("action", "")
                self._form_method = values.get("method", "GET").upper()
                self._form_fields = {}
                self._form_field_names = set()
        elif lowered == "input" and self._form_id is not None:
            name = values.get("name")
            if name:
                self._form_field_names.add(name)
                self._form_fields[name] = values.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "form" or self._form_id is None:
            return
        self.forms[self._form_id] = _Form(
            action=self._form_action,
            method=self._form_method,
            fields=dict(self._form_fields),
            field_names=frozenset(self._form_field_names),
        )
        self._form_id = None
        self._form_action = ""
        self._form_method = "GET"
        self._form_fields = {}
        self._form_field_names = set()


def _parse_page(html: str) -> _HmgParser | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    parser = _HmgParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    return parser


def _inventory_config(
    parser: _HmgParser,
    html: str,
    page_url: str,
) -> tuple[str, str] | None:
    form = parser.forms.get("JBSearchList_form")
    if form is None or form.method not in {"GET", ""}:
        return None
    if not _same_origin_path(form.action or "/index.smpl", page_url, "/index.smpl"):
        return None
    fields = form.fields
    if (
        fields.get("arg") != "list_posts"
        or fields.get("pid") != "gwt"
        or not _TOKEN_HASH.fullmatch(fields.get("h", ""))
        or not _TOKEN_TIME.fullmatch(fields.get("t", ""))
        or "keywords" not in form.field_names
    ):
        return None
    if not _has_hmg_assets(parser, page_url):
        return None
    required_markers = (
        "ResultSet.list",
        "ResultSet.list_meta",
        "SEO_PERMALINK",
        "POST_ID",
        "'/jb/'",
    )
    if (
        any(marker not in html for marker in required_markers)
        or _INVENTORY_DECLARATION.search(html) is None
    ):
        return None
    return fields["h"], fields["t"]


def _search_entry(
    parser: _HmgParser,
    page_url: str,
) -> tuple[str, dict[str, str]] | None:
    form = parser.forms.get("jb_search")
    if form is None or form.method != "POST":
        return None
    if not _same_origin_path(form.action or "/index.smpl", page_url, "/index.smpl"):
        return None
    if (
        form.fields.get("arg") != "jb_search_results"
        or not _TOKEN_TIME.fullmatch(form.fields.get("t", ""))
        or "keywords" not in form.field_names
        or not _has_hmg_assets(parser, page_url)
    ):
        return None
    allowed = {
        key: value
        for key, value in form.fields.items()
        if key
        in {
            "SAVED_SEARCH_ID",
            "action",
            "arg",
            "proximity",
            "t",
            "view",
        }
    }
    allowed["arg"] = "jb_search_results"
    allowed["action"] = "1"
    origin = _safe_origin(page_url)
    if origin is None:
        return None
    return f"{origin}/index.smpl", allowed


def _has_hmg_assets(parser: _HmgParser, page_url: str) -> bool:
    return any(
        _same_origin_path(source, page_url, "/js/combobo.js")
        for source in parser.scripts
    ) and any(
        _same_origin_path(source, page_url, "/css/hmg-jb.css")
        for source in parser.stylesheets
    )


def _inventory_payload(raw: str) -> tuple[dict, bool] | tuple[None, bool]:
    if not isinstance(raw, str) or len(raw) > _MAX_RESPONSE_CHARS:
        return None, False
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        stripped = raw.strip()
        if not stripped.startswith('{"ResultSet"') or not stripped.endswith("]"):
            return None, False
        try:
            payload = json.loads(f"{stripped}}}}}")
        except json.JSONDecodeError:
            return None, False
        result_set = payload.get("ResultSet") if isinstance(payload, dict) else None
        if not isinstance(result_set, dict) or not result_set.get("list"):
            return None, False
        repaired = True
    else:
        repaired = False
    return (payload, repaired) if isinstance(payload, dict) else (None, repaired)


def _inventory_page(
    payload: dict,
) -> tuple[list[dict], int, int, int, tuple[str, str] | None] | None:
    result_set = payload.get("ResultSet")
    if not isinstance(result_set, dict):
        return None
    records = result_set.get("list")
    meta = result_set.get("list_meta")
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        return None
    if not isinstance(meta, dict):
        return None
    total = _bounded_nonnegative_int(meta.get("total"), _MAX_RECORDS)
    next_offset = _bounded_int(meta.get("first"), -1, _MAX_RECORDS)
    page_size = _bounded_int(meta.get("pp"), 1, 100)
    if total is None or next_offset is None or page_size is None:
        return None
    if total == 0 and (records or next_offset != -1):
        return None
    ticket = result_set.get("ticket")
    next_ticket = None
    if isinstance(ticket, dict):
        h_token = ticket.get("h")
        t_token = ticket.get("t")
        if (
            isinstance(h_token, str)
            and isinstance(t_token, str)
            and _TOKEN_HASH.fullmatch(h_token)
            and _TOKEN_TIME.fullmatch(t_token)
        ):
            next_ticket = (h_token, t_token)
    return records, total, next_offset, page_size, next_ticket


def _candidate(
    record: dict,
    origin: str,
    expected_host: str,
) -> tuple[str, JobCandidate] | None:
    post_id = _positive_id(record.get("POST_ID"))
    title = _bounded_text(record.get("POST_TITLE"), required=True)
    location = _bounded_text(record.get("POST_LOCATION"), required=False)
    slug = record.get("SEO_PERMALINK")
    canonical_url = record.get("POST_SEO_URL")
    if (
        post_id is None
        or title is None
        or not isinstance(slug, str)
        or not _SLUG.fullmatch(slug)
        or not isinstance(canonical_url, str)
    ):
        return None
    parsed = urlparse(canonical_url)
    if (
        _safe_origin(canonical_url) != origin
        or (parsed.hostname or "").casefold() != expected_host
        or parsed.path != f"/jb/{slug}/{post_id}"
        or parsed.query
        or parsed.fragment
    ):
        return None
    job_number = _bounded_text(record.get("POST_JOB_NUMBER"), required=False)
    return post_id, JobCandidate(
        title=title,
        url=canonical_url,
        provider="haley_marketing",
        location=location,
        raw={
            "post_id": post_id,
            "job_number": job_number,
            "expiration_date": _bounded_text(
                record.get("POST_EXPIRATION_DATE"),
                required=False,
            ),
        },
    )


def _board_identity(board: JobBoard) -> tuple[str, str] | None:
    if (
        board.provider != "haley_marketing"
        or not isinstance(board.identifier, str)
        or not board.identifier.startswith(_CUSTOM_PREFIX)
    ):
        return None
    expected_host = board.identifier.removeprefix(_CUSTOM_PREFIX).casefold()
    origin = _safe_origin(board.url)
    if (
        origin is None
        or not expected_host
        or (urlparse(origin).hostname or "").casefold() != expected_host
    ):
        return None
    return origin, expected_host


def _safe_origin(url: str) -> str | None:
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
        or not _HOSTNAME.fullmatch(host)
    ):
        return None
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        if not address.is_global:
            return None
    return f"https://{host}"


def _same_origin_path(value: str, base_url: str, expected_path: str) -> bool:
    origin = _safe_origin(base_url)
    if origin is None:
        return False
    try:
        parsed = urlparse(urljoin(base_url, value))
    except (TypeError, ValueError):
        return False
    return (
        _safe_origin(parsed.geturl()) == origin
        and parsed.path == expected_path
        and not parsed.fragment
    )


def _same_inventory_endpoint(url: str, origin: str) -> bool:
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return False
    return (
        _safe_origin(url) == origin
        and parsed.path == "/json/index.smpl"
        and not parsed.fragment
    )


def _trace_inventory_url(url: str) -> str:
    parsed = urlparse(url)
    safe_query = urlencode(
        [
            (key, "[redacted]" if key in {"h", "t"} else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return parsed._replace(query=safe_query).geturl()


def _archived_state(value) -> bool | None:
    if value is None or value is False or value == "" or value == 0 or value == "0":
        return False
    if value is True or value == 1 or value == "1":
        return True
    return None


def _early_stop_location_matches(
    candidate_location: str | None,
    target_location: str | None,
) -> bool:
    if not target_location:
        return True
    if not candidate_location:
        return False
    return _normalized_location(candidate_location) == _normalized_location(
        target_location
    )


def _normalized_location(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _bounded_text(value, *, required: bool) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    if len(normalized) > _MAX_FIELD_CHARS:
        return None
    return normalized


def _positive_id(value) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if 0 < value <= 10**20 - 1 else None
    if isinstance(value, str) and value.isdigit() and value[0] != "0":
        number = int(value)
        return value if number <= 10**20 - 1 else None
    return None


def _bounded_nonnegative_int(value, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if str(value).strip() != str(parsed) or not 0 <= parsed <= maximum:
        return None
    return parsed


def _bounded_int(value, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if str(value).strip() != str(parsed) or not minimum <= parsed <= maximum:
        return None
    return parsed


def _normalized_title(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _fetch_failure(
    board: JobBoard,
    error: Exception,
    *,
    candidates: list[JobCandidate] | None = None,
    **trace,
) -> AdapterResult:
    reason_code = provider_fetch_reason(error)
    return _result(
        board,
        candidates=[],
        reason_code=reason_code,
        retryable=reason_spec(reason_code).retryable,
        inventory_complete=False,
        error_class=type(error).__name__,
        **trace,
    )


def _invalid(
    board: JobBoard,
    candidates: list[JobCandidate],
    board_urls: list[str],
    search_requests: list[str],
    api_urls: list[str],
    repaired_responses: int,
    stop_reason: str,
    **trace,
) -> AdapterResult:
    return _result(
        board,
        candidates=[],
        reason_code="INVALID_STRUCTURED_DATA",
        inventory_complete=False,
        board_urls=board_urls,
        search_requests=search_requests,
        api_urls=api_urls,
        repaired_responses=repaired_responses,
        parsed_candidate_count=len(candidates),
        stop_reason=stop_reason,
        **trace,
    )


def _result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None = None,
    retryable: bool = False,
    inventory_complete: bool = True,
    **trace,
) -> AdapterResult:
    payload = {
        "adapter": "haley_marketing",
        "inventory_scope": "title_filtered",
        "inventory_complete": inventory_complete,
        **trace,
    }
    if payload.get("repaired_responses"):
        payload["structured_data_repair"] = "missing_two_final_braces"
    return AdapterResult(
        provider="haley_marketing",
        board=board,
        candidates=list(candidates or []),
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope="title_filtered",
        inventory_complete=inventory_complete,
        trace=payload,
    )


ADAPTER = HaleyMarketingAdapter()
