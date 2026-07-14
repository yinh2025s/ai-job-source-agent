from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re
import threading
from urllib.parse import quote, urlencode, urlparse, urlunparse

from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_MAX_HTML_CHARS = 2_000_000
_MAX_CONFIG_CHARS = 20_000
_MAX_RESPONSE_CHARS = 2_000_000
_MAX_HANDOFFS = 32
_MAX_COUNTRY_IDS = 64
_MAX_PAGES = 10
_MAX_ROWS = 1_000
_PAGE_SIZE = 50
_API_HOST_SUFFIX = "-caas-api.e-spirit.cloud"
_ASSIGNMENT = re.compile(r"\bwindow\s*\.\s*EXTERNAL_CONFIG\s*=")
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_COUNTRY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_CREDENTIAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~+/=-]{7,511}$")
_JOB_ROUTE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,299}$")
_JOB_ID = re.compile(r"^[^\x00-\x1f\x7f]{1,300}$")
_REDACTED_MARKERS = (
    "redacted",
    "placeholder",
    "api_key",
    "apikey",
    "your-key",
    "your_key",
)


@dataclass(frozen=True)
class _Identity:
    api_origin: str
    tenant: str
    project: str
    collection: str
    detail_prefix: str
    career_origin: str
    country_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Config:
    identity: _Identity
    credential: str


_HANDOFF_LOCK = threading.Lock()
_CREDENTIAL_HANDOFFS: OrderedDict[str, str] = OrderedDict()


class ESpiritCaaSAdapter:
    name = "e_spirit_caas"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        # Customer career domains require public page configuration evidence.
        return False

    def identify_board(self, url: str) -> JobBoard | None:
        return None

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        parsed_page = _safe_https_url(page_url)
        if parsed_page is None:
            return None
        config = _jobs_config(page.html, parsed_page)
        if config is None:
            return None
        board = JobBoard(
            url=urlunparse(parsed_page._replace(query="", fragment="")),
            provider=self.name,
            identifier=_encode_identity(config.identity),
            replay_safe=False,
        )
        _put_credential(_handoff_key(board), config.credential)
        return board

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        # Consumption is deliberately first: invalid or failed calls cannot replay it.
        credential = _take_credential(_handoff_key(board))
        title = query.title.strip() if isinstance(query.title, str) else ""
        scope = "title_filtered" if title else "full"
        identity = _board_identity(board)
        if identity is None:
            return _failure(
                board,
                scope,
                "PROVIDER_VARIANT_UNSUPPORTED",
                "invalid_board_identity",
            )
        if credential is None:
            return _failure(
                board,
                scope,
                "PROVIDER_VARIANT_UNSUPPORTED",
                "runtime_credential_unavailable",
            )
        if not title or len(title) > 200 or _has_controls(title):
            return _failure(
                board,
                scope,
                "PROVIDER_VARIANT_UNSUPPORTED",
                "bounded_title_required",
            )

        endpoint = _aggregation_endpoint(identity)
        candidates: list[JobCandidate] = []
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        expected_count: int | None = None
        pages_fetched = 0
        records_seen = 0

        for page_number in range(1, _MAX_PAGES + 1):
            request_url = _request_url(identity, title, page_number)
            try:
                response = fetcher.fetch(
                    request_url,
                    headers={
                        "Accept": "application/hal+json, application/json",
                        "Authorization": f"Bearer {credential}",
                    },
                )
            except (FetchError, OSError, TimeoutError) as error:
                reason = _fetch_reason(error)
                return _result(
                    board,
                    candidates,
                    scope,
                    reason,
                    reason_spec(reason).retryable,
                    False,
                    pages_fetched=pages_fetched,
                    records_seen=records_seen,
                    total=expected_count,
                    stop_reason="inventory_fetch_failed",
                )

            pages_fetched += 1
            if not _expected_response_url(response.final_url or response.url, request_url):
                return _result(
                    board,
                    candidates,
                    scope,
                    "PROVIDER_VARIANT_UNSUPPORTED",
                    False,
                    False,
                    pages_fetched=pages_fetched,
                    records_seen=records_seen,
                    total=expected_count,
                    stop_reason="unsafe_inventory_response_url",
                )
            if not isinstance(response.html, str) or len(response.html) > _MAX_RESPONSE_CHARS:
                return _invalid(
                    board,
                    candidates,
                    scope,
                    pages_fetched,
                    records_seen,
                    expected_count,
                    "response_size_or_type",
                )
            try:
                payload = json.loads(response.html)
            except (json.JSONDecodeError, TypeError, ValueError):
                return _invalid(
                    board,
                    candidates,
                    scope,
                    pages_fetched,
                    records_seen,
                    expected_count,
                    "invalid_json",
                )

            parsed = _hal_page(payload)
            if parsed is None:
                return _invalid(
                    board,
                    candidates,
                    scope,
                    pages_fetched,
                    records_seen,
                    expected_count,
                    "invalid_hal_schema",
                )
            rows, count = parsed
            if expected_count is None:
                expected_count = count
                if count > _MAX_ROWS:
                    return _result(
                        board,
                        candidates,
                        scope,
                        "FETCH_BUDGET_EXHAUSTED",
                        True,
                        False,
                        pages_fetched=pages_fetched,
                        records_seen=records_seen,
                        total=count,
                        stop_reason="row_cap_exceeded",
                    )
            elif count != expected_count:
                return _invalid(
                    board,
                    candidates,
                    scope,
                    pages_fetched,
                    records_seen,
                    expected_count,
                    "contradictory_count",
                )

            remaining = expected_count - records_seen
            expected_rows = min(_PAGE_SIZE, remaining)
            if len(rows) != expected_rows:
                return _invalid(
                    board,
                    candidates,
                    scope,
                    pages_fetched,
                    records_seen,
                    expected_count,
                    "pagination_count_mismatch",
                )

            for record in rows:
                candidate = _candidate(board, identity, record)
                if candidate is None:
                    return _invalid(
                        board,
                        candidates,
                        scope,
                        pages_fetched,
                        records_seen,
                        expected_count,
                        "invalid_job_record",
                    )
                record_id = candidate.raw["job_id"].casefold()
                candidate_url = candidate.url.casefold()
                if record_id in seen_ids or candidate_url in seen_urls:
                    return _invalid(
                        board,
                        candidates,
                        scope,
                        pages_fetched,
                        records_seen,
                        expected_count,
                        "duplicate_job_record",
                    )
                seen_ids.add(record_id)
                seen_urls.add(candidate_url)
                candidates.append(candidate)
                records_seen += 1

            if records_seen == expected_count:
                reason = None if candidates else "EMPTY_PROVIDER_RESPONSE"
                return _result(
                    board,
                    candidates,
                    scope,
                    reason,
                    False,
                    True,
                    pages_fetched=pages_fetched,
                    records_seen=records_seen,
                    total=expected_count,
                    stop_reason="complete",
                )

        return _result(
            board,
            candidates,
            scope,
            "FETCH_BUDGET_EXHAUSTED",
            True,
            False,
            pages_fetched=pages_fetched,
            records_seen=records_seen,
            total=expected_count,
            stop_reason="page_cap_reached",
        )


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.bodies: list[str] = []
        self.country_filter_bodies: list[str] = []
        self._in_script = False
        self._is_country_filter = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "script":
            self._in_script = True
            attributes = {name.casefold(): value or "" for name, value in attrs}
            self._is_country_filter = (
                attributes.get("data-prop-name", "").casefold()
                == "countryfilteroptions"
                and attributes.get("type", "").casefold() in {"text/json", "application/json"}
            )
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._in_script:
            body = "".join(self._parts)
            self.bodies.append(body)
            if self._is_country_filter:
                self.country_filter_bodies.append(body)
            self._in_script = False
            self._is_country_filter = False
            self._parts = []


class _LiteralParser:
    def __init__(self, text: str, start: int) -> None:
        self.text = text
        self.index = start

    def parse(self):
        value = self._value()
        self._space()
        if self.index < len(self.text) and self.text[self.index] == ";":
            self.index += 1
            self._space()
        if self.index != len(self.text):
            raise ValueError("trailing script content")
        return value

    def _value(self):
        self._space()
        if self.index >= len(self.text):
            raise ValueError("missing value")
        char = self.text[self.index]
        if char == "{":
            return self._object()
        if char == "[":
            return self._array()
        if char in "\"'":
            return self._string()
        for literal, value in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(literal, self.index):
                self.index += len(literal)
                return value
        raise ValueError("unsupported literal")

    def _object(self) -> dict:
        result = {}
        self.index += 1
        self._space()
        if self._take("}"):
            return result
        while True:
            self._space()
            key = self._string() if self._peek() in "\"'" else self._identifier()
            if key in result:
                raise ValueError("duplicate key")
            self._space()
            if not self._take(":"):
                raise ValueError("missing colon")
            result[key] = self._value()
            self._space()
            if self._take("}"):
                return result
            if not self._take(","):
                raise ValueError("missing comma")
            self._space()
            if self._take("}"):
                return result

    def _array(self) -> list:
        result = []
        self.index += 1
        self._space()
        if self._take("]"):
            return result
        while True:
            result.append(self._value())
            self._space()
            if self._take("]"):
                return result
            if not self._take(","):
                raise ValueError("missing comma")
            self._space()
            if self._take("]"):
                return result

    def _string(self) -> str:
        quote_char = self._peek()
        self.index += 1
        parts: list[str] = []
        escapes = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
        while self.index < len(self.text):
            char = self.text[self.index]
            self.index += 1
            if char == quote_char:
                return "".join(parts)
            if char == "\\":
                if self.index >= len(self.text):
                    break
                escaped = self.text[self.index]
                self.index += 1
                if escaped in "\\/\"'":
                    parts.append(escaped)
                elif escaped in escapes:
                    parts.append(escapes[escaped])
                elif escaped == "u" and self.index + 4 <= len(self.text):
                    code = self.text[self.index : self.index + 4]
                    if not re.fullmatch(r"[0-9A-Fa-f]{4}", code):
                        raise ValueError("invalid unicode escape")
                    parts.append(chr(int(code, 16)))
                    self.index += 4
                else:
                    raise ValueError("invalid escape")
            elif ord(char) < 0x20:
                raise ValueError("control in string")
            else:
                parts.append(char)
        raise ValueError("unterminated string")

    def _identifier(self) -> str:
        match = re.match(r"[A-Za-z_$][A-Za-z0-9_$]*", self.text[self.index :])
        if match is None:
            raise ValueError("invalid identifier")
        self.index += len(match.group(0))
        return match.group(0)

    def _space(self) -> None:
        while self.index < len(self.text) and self.text[self.index].isspace():
            self.index += 1

    def _peek(self) -> str:
        return self.text[self.index] if self.index < len(self.text) else ""

    def _take(self, char: str) -> bool:
        if self._peek() != char:
            return False
        self.index += 1
        return True


def _jobs_config(html: str, page_url) -> _Config | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    parser = _ScriptParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    assignments: list[dict] = []
    for body in parser.bodies:
        if len(body) > _MAX_CONFIG_CHARS:
            if _ASSIGNMENT.search(body):
                return None
            continue
        matches = list(_ASSIGNMENT.finditer(body))
        if not matches:
            continue
        if len(matches) != 1:
            return None
        try:
            value = _LiteralParser(body, matches[0].end()).parse()
        except (TypeError, ValueError):
            return None
        if not isinstance(value, dict):
            return None
        assignments.append(value)
    if len(assignments) != 1:
        return None
    if len(parser.country_filter_bodies) != 1:
        return None
    country_ids = _country_filter_ids(parser.country_filter_bodies[0])
    if country_ids is None:
        return None
    return _validated_config(assignments[0], page_url, country_ids)


def _validated_config(
    value: dict,
    page_url,
    country_ids: tuple[str, ...],
) -> _Config | None:
    if set(value) != {"jobsApi", "jobAdLinkPrefix"} or not {
        "jobsApi",
        "jobAdLinkPrefix",
    }.issubset(value):
        return None
    jobs = value.get("jobsApi")
    if not isinstance(jobs, dict) or set(jobs) != {
        "baseUrl",
        "tenant",
        "project",
        "collection",
        "apiKey",
    }:
        return None
    required = {"baseUrl", "tenant", "project", "collection", "apiKey"}
    if not required.issubset(jobs):
        return None
    api_origin = _safe_api_origin(jobs.get("baseUrl"))
    tenant = jobs.get("tenant")
    project = jobs.get("project")
    collection = jobs.get("collection")
    credential = jobs.get("apiKey")
    if (
        api_origin is None
        or not isinstance(tenant, str)
        or _SEGMENT.fullmatch(tenant) is None
        or not isinstance(project, str)
        or _SEGMENT.fullmatch(project) is None
        or collection != "jobs"
        or not _valid_credential(credential)
    ):
        return None

    career_origin = _origin(page_url)
    detail_prefix = _safe_detail_prefix(value.get("jobAdLinkPrefix"), career_origin)
    if detail_prefix is None:
        return None
    return _Config(
        identity=_Identity(
            api_origin=api_origin,
            tenant=tenant,
            project=project,
            collection=collection,
            detail_prefix=detail_prefix,
            career_origin=career_origin,
            country_ids=country_ids,
        ),
        credential=credential,
    )


def _safe_https_url(value: str):
    try:
        parsed = urlparse(value)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or _has_controls(value)
    ):
        return None
    return parsed


def _safe_api_origin(value) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = _safe_https_url(value)
    if parsed is None or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    host = (parsed.hostname or "").casefold()
    prefix = host[: -len(_API_HOST_SUFFIX)] if host.endswith(_API_HOST_SUFFIX) else ""
    if (
        not prefix
        or len(host) > 253
        or any(_DNS_LABEL.fullmatch(label) is None for label in prefix.split("."))
    ):
        return None
    return f"https://{host}"


def _safe_detail_prefix(value, career_origin: str) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = _safe_https_url(value)
    if (
        parsed is None
        or _origin(parsed) != career_origin
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/job/")
        or "//" in parsed.path
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        return None
    return urlunparse(parsed._replace(query="", fragment=""))


def _valid_credential(value) -> bool:
    if value == "[REDACTED]":
        # Snapshot replay uses a non-secret sentinel. Authorization is excluded
        # from request identity, so the sanitized request still consumes the
        # original scoped outcome without persisting the public runtime key.
        return True
    if not isinstance(value, str) or _CREDENTIAL.fullmatch(value) is None:
        return False
    folded = value.casefold()
    return not any(marker in folded for marker in _REDACTED_MARKERS) and set(value) != {"*"}


def _bounded_country_ids(value) -> tuple[str, ...] | None:
    if not isinstance(value, list) or len(value) > _MAX_COUNTRY_IDS:
        return None
    normalized = tuple(item.strip() if isinstance(item, str) else item for item in value)
    if any(not isinstance(item, str) or _COUNTRY_ID.fullmatch(item) is None for item in normalized):
        return None
    if not normalized or len({item.casefold() for item in normalized}) != len(normalized):
        return None
    return normalized


def _country_filter_ids(body: str) -> tuple[str, ...] | None:
    if not isinstance(body, str) or len(body) > _MAX_CONFIG_CHARS:
        return None
    try:
        options = json.loads(body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(options, list) or not options or len(options) > _MAX_COUNTRY_IDS:
        return None
    ids: list[str] = []
    for option in options:
        if not isinstance(option, dict) or not set(option).issubset({"label", "ids", "index"}):
            return None
        raw_ids = option.get("ids")
        if not isinstance(option.get("label"), str) or not isinstance(raw_ids, list):
            return None
        ids.extend(raw_ids)
    return _bounded_country_ids(ids)


def _origin(parsed) -> str:
    return f"https://{(parsed.hostname or '').casefold()}"


def _encode_identity(identity: _Identity) -> str:
    return json.dumps(
        {
            "api_origin": identity.api_origin,
            "career_origin": identity.career_origin,
            "collection": identity.collection,
            "country_ids": list(identity.country_ids),
            "detail_prefix": identity.detail_prefix,
            "project": identity.project,
            "tenant": identity.tenant,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _board_identity(board: JobBoard) -> _Identity | None:
    if board.provider != "e_spirit_caas" or not board.identifier or board.replay_safe:
        return None
    page_url = _safe_https_url(board.url)
    if page_url is None or page_url.query or page_url.fragment:
        return None
    try:
        value = json.loads(board.identifier)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    expected = {
        "api_origin",
        "career_origin",
        "collection",
        "country_ids",
        "detail_prefix",
        "project",
        "tenant",
    }
    if not isinstance(value, dict) or set(value) != expected:
        return None
    country_ids = _bounded_country_ids(value.get("country_ids"))
    if country_ids is None:
        return None
    config = _validated_config(
        {
            "jobsApi": {
                "baseUrl": value.get("api_origin"),
                "tenant": value.get("tenant"),
                "project": value.get("project"),
                "collection": value.get("collection"),
                "apiKey": "identity-validation-key",
            },
            "jobAdLinkPrefix": value.get("detail_prefix"),
        },
        page_url,
        country_ids,
    )
    if config is None or value.get("career_origin") != _origin(page_url):
        return None
    return config.identity


def _handoff_key(board: JobBoard) -> str:
    return json.dumps(
        [board.provider, board.url, board.identifier, board.replay_safe],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _put_credential(key: str, credential: str) -> None:
    with _HANDOFF_LOCK:
        _CREDENTIAL_HANDOFFS.pop(key, None)
        _CREDENTIAL_HANDOFFS[key] = credential
        while len(_CREDENTIAL_HANDOFFS) > _MAX_HANDOFFS:
            _CREDENTIAL_HANDOFFS.popitem(last=False)


def _take_credential(key: str) -> str | None:
    with _HANDOFF_LOCK:
        return _CREDENTIAL_HANDOFFS.pop(key, None)


def _aggregation_endpoint(identity: _Identity) -> str:
    collection = (
        f"{quote(identity.project, safe='')}."
        f"{quote(identity.collection, safe='')}.content"
    )
    return (
        f"{identity.api_origin}/{quote(identity.tenant, safe='')}/"
        f"{collection}/_aggrs/get_jobs"
    )


def _request_url(identity: _Identity, title: str, page_number: int) -> str:
    variables = json.dumps(
        {
            "country": list(identity.country_ids),
            "search_term": title,
            "sort": {"releasedDate": -1},
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{_aggregation_endpoint(identity)}?{urlencode({'avars': variables, 'page': page_number, 'pagesize': _PAGE_SIZE})}"


def _expected_response_url(response_url: str, request_url: str) -> bool:
    parsed = _safe_https_url(response_url)
    expected = _safe_https_url(request_url)
    return parsed is not None and expected is not None and parsed == expected


def _hal_page(payload) -> tuple[list[dict], int] | None:
    if not isinstance(payload, dict) or payload.get("_returned") != 1:
        return None
    embedded = payload.get("_embedded")
    if not isinstance(embedded, dict) or set(embedded) != {"rh:result"}:
        return None
    results = embedded.get("rh:result")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        return None
    result = results[0]
    rows = result.get("data")
    meta = result.get("meta")
    if (
        not isinstance(rows, list)
        or len(rows) > _PAGE_SIZE
        or not isinstance(meta, list)
        or len(meta) != 1
        or not isinstance(meta[0], dict)
        or set(meta[0]) != {"count"}
    ):
        return None
    count = meta[0].get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    if any(not isinstance(row, dict) for row in rows):
        return None
    return rows, count


def _candidate(board: JobBoard, identity: _Identity, record: dict) -> JobCandidate | None:
    job_id = record.get("_id")
    title = record.get("name")
    route = record.get("jobUrl")
    if (
        not isinstance(job_id, str)
        or _JOB_ID.fullmatch(job_id) is None
        or job_id != job_id.strip()
        or not isinstance(title, str)
        or not title.strip()
        or len(title) > 500
        or _has_controls(title)
        or not isinstance(route, str)
        or _JOB_ROUTE.fullmatch(route) is None
    ):
        return None
    detail_url = identity.detail_prefix + quote(route, safe="-._~")
    if not _valid_detail_url(detail_url, identity):
        return None
    location = _location(record.get("location"))
    if location is False:
        return None
    raw = {"job_id": job_id}
    for source, target in (("refNumber", "reference"), ("releasedDate", "released")):
        value = record.get(source)
        if value is not None:
            if not isinstance(value, str) or len(value) > 200 or _has_controls(value):
                return None
            raw[target] = value
    return JobCandidate(
        title=title.strip(),
        url=detail_url,
        provider="e_spirit_caas",
        location=location,
        raw=raw,
    )


def _valid_detail_url(url: str, identity: _Identity) -> bool:
    parsed = _safe_https_url(url)
    prefix = _safe_https_url(identity.detail_prefix)
    return bool(
        parsed is not None
        and prefix is not None
        and _origin(parsed) == identity.career_origin
        and not parsed.query
        and not parsed.fragment
        and parsed.path.startswith(prefix.path)
        and parsed.path.count("/") == prefix.path.count("/")
    )


def _location(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        return False
    parts: list[str] = []
    for key in ("workLocation", "city", "country"):
        item = value.get(key)
        if item is None:
            continue
        if not isinstance(item, str) or len(item) > 300 or _has_controls(item):
            return False
        item = item.strip()
        if item and item.casefold() not in {part.casefold() for part in parts}:
            parts.append(item)
    return ", ".join(parts) if parts else None


def _has_controls(value: str) -> bool:
    return any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)


def _fetch_reason(error: Exception) -> str:
    if isinstance(error, FetchError) and error.reason_code:
        return error.reason_code
    reason = classify_fetch_error(str(error))
    return "PROVIDER_FETCH_FAILED" if reason == "FETCH_FAILED" else reason


def _invalid(
    board: JobBoard,
    candidates: list[JobCandidate],
    scope: str,
    pages_fetched: int,
    records_seen: int,
    total: int | None,
    stop_reason: str,
) -> AdapterResult:
    return _result(
        board,
        candidates,
        scope,
        "INVALID_STRUCTURED_DATA",
        False,
        False,
        pages_fetched=pages_fetched,
        records_seen=records_seen,
        total=total,
        stop_reason=stop_reason,
    )


def _failure(board: JobBoard, scope: str, reason: str, stop_reason: str) -> AdapterResult:
    return _result(
        board,
        [],
        scope,
        reason,
        reason_spec(reason).retryable,
        False,
        pages_fetched=0,
        records_seen=0,
        total=None,
        stop_reason=stop_reason,
    )


def _result(
    board: JobBoard,
    candidates: list[JobCandidate],
    scope: str,
    reason_code: str | None,
    retryable: bool,
    complete: bool,
    *,
    pages_fetched: int,
    records_seen: int,
    total: int | None,
    stop_reason: str,
) -> AdapterResult:
    return AdapterResult(
        provider="e_spirit_caas",
        board=board,
        candidates=list(candidates),
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope=scope,
        inventory_complete=complete,
        trace={
            "adapter": "e_spirit_caas",
            "variant": "public_jobs_aggregation_v1",
            "endpoint": "get_jobs",
            "candidate_count": len(candidates),
            "records_seen": records_seen,
            "total": total,
            "page_count": pages_fetched,
            "stop_reason": stop_reason,
            "inventory_scope": scope,
            "inventory_complete": complete,
        },
    )


ADAPTER = ESpiritCaaSAdapter()
