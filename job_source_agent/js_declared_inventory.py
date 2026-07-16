from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import ipaddress
import json
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from .contracts import FetchClient
from .scoring import is_ats_url, is_likely_job_detail, score_job_link
from .web import FetchError, Page, RawLink


MAX_ASSETS = 3
MAX_ASSET_CHARS = 2_000_000
MAX_CANDIDATES = 5_000
MAX_PAGE_SIZE = 5_000
MAX_URL_CHARS = 8_192

_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]{0,79}"
_URL_PROPERTY = re.compile(
    r"(?:\burl\b|['\"]url['\"])\s*:\s*"
    r"(?P<quote>['\"])(?P<url>[^'\"]{1,1000})(?P=quote)",
    re.I,
)
_POST_PROPERTY = re.compile(r"\b(?:method|type)\s*:\s*['\"]POST['\"]", re.I)
_XHR_POST = re.compile(r"\.open\(\s*['\"]POST['\"]\s*,", re.I)
_XHR_FORM_CONTENT = re.compile(
    r"setRequestHeader\(\s*['\"]Content-type['\"]\s*,\s*"
    r"['\"]application/x-www-form-urlencoded['\"]\s*\)",
    re.I,
)
_XHR_SEND = re.compile(r"\.send\([^;]{0,1000}\)", re.I | re.S)
_DATA_PROPERTY = re.compile(
    rf"\b(?:data|body|payload)\s*:\s*(?P<value>\{{|{_IDENTIFIER})", re.I
)
_JOB_POSTINGS = re.compile(r"\bjobPostings\b")
_ASSIGNMENT = re.compile(
    rf"\b(?:const|let|var)\s+(?P<name>{_IDENTIFIER})\s*=\s*"
    r"(?P<value>-?\d+|true|false|null|'[^'\r\n]*'|\"[^\"\r\n]*\")\s*;?",
    re.I,
)
_PROPERTY = re.compile(
    rf"(?P<key>{_IDENTIFIER}|['\"][^'\"]+['\"])\s*:\s*"
    rf"(?P<value>-?\d+|true|false|null|'[^'\r\n]*'|\"[^\"\r\n]*\"|{_IDENTIFIER})",
    re.I,
)
_PAGE_SIZE_KEY = re.compile(
    r"(?:page.?size|pagination.?limit|results?.?per.?page|records?.?per.?page|limit|rows)$",
    re.I,
)
_PAGE_OFFSET_KEY = re.compile(
    r"(?:page(?:no|number|index)?|pagination.?start|start(?:index)?|offset|skip)$",
    re.I,
)
_TITLE_KEYS = ("title", "jobTitle", "job_title", "positionTitle", "position")
_LOCATION_KEYS = ("location", "jobLocation", "job_location", "locationName")
_URL_KEYS = (
    "url", "jobUrl", "job_url", "detailUrl", "detail_url", "applyUrl",
    "externalUrl", "canonicalUrl", "link",
)
_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:access.?token|api.?key|auth(?:orization)?|cookie|csrf|jwt|"
    r"password|refresh.?token|secret|session|signature|token)(?:$|[_-])",
    re.I,
)
_CREDENTIALS = re.compile(
    r"\bcredentials\s*:\s*['\"](?:include|same-origin)['\"]|"
    r"\bwithCredentials\s*:\s*true|\bAuthorization\s*:",
    re.I,
)
_HOSTNAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$", re.I)


@dataclass(frozen=True)
class JSListingCandidate:
    title: str
    location: str | None
    url: str
    source_url: str


@dataclass(frozen=True)
class JSInventoryTrace:
    status: str
    retryable: bool
    blocked: bool
    assets_considered: tuple[str, ...] = ()
    assets_fetched: tuple[str, ...] = ()
    endpoint_url: str | None = None
    request_fields: tuple[str, ...] = ()
    candidate_count: int = 0
    detail: str | None = None


@dataclass(frozen=True)
class JSDeclaredInventoryResult:
    candidates: tuple[JSListingCandidate, ...]
    inventory_complete: bool
    trace: JSInventoryTrace


@dataclass(frozen=True)
class _Declaration:
    asset_url: str
    endpoint_url: str
    fields: tuple[tuple[str, str], ...]


class _ScriptParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        values = {key.casefold(): value or "" for key, value in attrs}
        if values.get("src"):
            self.urls.append(urljoin(self.page_url, values["src"]))


def discover_js_declared_inventory(
    fetcher: FetchClient,
    page: Page,
    title: str,
    *,
    max_assets: int = MAX_ASSETS,
    max_candidates: int = MAX_CANDIDATES,
) -> JSDeclaredInventoryResult:
    """Discover and execute one fully declared anonymous JS listing transport."""

    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    _validate_limit("max_assets", max_assets, MAX_ASSETS)
    _validate_limit("max_candidates", max_candidates, MAX_CANDIDATES)

    page_url = _public_https_url(page.final_url or page.url)
    if page_url is None:
        return _result("unsafe_listing_url", detail="listing URL is not public HTTPS")

    asset_urls = _script_urls(page.html or "", page_url)
    considered = tuple(asset_urls[:max_assets])
    fetched: list[str] = []
    declarations: list[_Declaration] = []
    for asset_url in considered:
        try:
            asset_page = fetcher.fetch(asset_url)
        except (FetchError, OSError, TimeoutError) as exc:
            return _fetch_failure(
                exc, "asset_fetch_failed", considered, tuple(fetched), detail=asset_url
            )
        response_url = _public_https_url(asset_page.final_url or asset_page.url)
        if response_url != asset_url:
            return _result(
                "asset_redirect_rejected",
                assets_considered=considered,
                assets_fetched=tuple(fetched),
                detail=asset_url,
            )
        fetched.append(asset_url)
        declaration = _declared_transport(
            (asset_page.html or "")[:MAX_ASSET_CHARS],
            asset_url,
            page_url,
            page.html or "",
        )
        if declaration is not None:
            declarations.append(declaration)

    unique = {(item.endpoint_url, item.fields): item for item in declarations}
    if len(unique) != 1:
        status = "transport_not_declared" if not unique else "ambiguous_transport"
        return _result(
            status,
            assets_considered=considered,
            assets_fetched=tuple(fetched),
        )
    declaration = next(iter(unique.values()))
    fields = dict(declaration.fields)
    fields["searchTerm"] = title.strip()
    body = urlencode(fields).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    try:
        response = fetcher.fetch(declaration.endpoint_url, data=body, headers=headers)
    except (FetchError, OSError, TimeoutError) as exc:
        return _fetch_failure(
            exc,
            "transport_fetch_failed",
            considered,
            tuple(fetched),
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(fields),
        )
    response_url = _public_https_url(response.final_url or response.url)
    if response_url != declaration.endpoint_url:
        return _result(
            "transport_redirect_rejected",
            assets_considered=considered,
            assets_fetched=tuple(fetched),
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(fields),
        )

    candidates, valid_payload, truncated = _parse_candidates(
        response.html or "", page_url, declaration.endpoint_url, max_candidates
    )
    if not valid_payload:
        return _result(
            "invalid_job_postings_payload",
            assets_considered=considered,
            assets_fetched=tuple(fetched),
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(fields),
        )
    status = "candidate_cap_reached" if truncated else "verified"
    return JSDeclaredInventoryResult(
        candidates=tuple(candidates),
        inventory_complete=not truncated,
        trace=JSInventoryTrace(
            status=status,
            retryable=False,
            blocked=False,
            assets_considered=considered,
            assets_fetched=tuple(fetched),
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(fields),
            candidate_count=len(candidates),
        ),
    )


def _script_urls(html: str, page_url: str) -> list[str]:
    parser = _ScriptParser(page_url)
    try:
        parser.feed(html[:500_000])
        parser.close()
    except (TypeError, ValueError):
        return []
    output: list[str] = []
    for value in parser.urls:
        candidate = _public_https_url(value)
        if (
            candidate is not None
            and _same_site(candidate, page_url)
            and urlparse(candidate).path.casefold().endswith(".js")
            and candidate not in output
        ):
            output.append(candidate)
    output.sort(key=_asset_priority)
    return output


def _asset_priority(url: str) -> tuple[int, str]:
    filename = urlparse(url).path.rsplit("/", 1)[-1].casefold()
    related = any(token in filename for token in ("career", "job", "search", "listing"))
    return (0 if related else 1, filename)


def _declared_transport(
    source: str,
    asset_url: str,
    page_url: str,
    page_html: str = "",
) -> _Declaration | None:
    constants = _literal_constants(source)
    matches: list[_Declaration] = []
    for url_match in _URL_PROPERTY.finditer(source):
        bounds = _enclosing_object(source, url_match.start())
        if bounds is None:
            continue
        request_object = source[bounds[0] : bounds[1] + 1]
        if not _POST_PROPERTY.search(request_object) or _CREDENTIALS.search(request_object):
            continue
        endpoint = _declared_endpoint(url_match.group("url"), page_url)
        if endpoint is None or not _JOB_POSTINGS.search(source):
            continue
        data_match = _DATA_PROPERTY.search(request_object)
        if data_match is None:
            continue
        if data_match.group("value") == "{":
            object_start = bounds[0] + data_match.end() - 1
            data_bounds = _object_from_open_brace(source, object_start)
            if data_bounds is None or data_bounds[1] > bounds[1]:
                continue
        else:
            data_bounds = _named_literal_object(
                source, data_match.group("value"), before=bounds[0]
            )
            if data_bounds is None:
                continue
        fields = _declared_fields(source[data_bounds[0] : data_bounds[1] + 1], constants)
        if fields is None:
            continue
        matches.append(_Declaration(asset_url, endpoint, tuple(fields.items())))
    unique = {(item.endpoint_url, item.fields): item for item in matches}
    if len(unique) == 1:
        return next(iter(unique.values()))
    if unique:
        return None
    return _declared_xhr_transport(source, asset_url, page_url, page_html)


def _declared_xhr_transport(
    source: str,
    asset_url: str,
    page_url: str,
    page_html: str,
) -> _Declaration | None:
    if not (
        _XHR_POST.search(source)
        and _XHR_FORM_CONTENT.search(source)
        and _XHR_SEND.search(source)
        and _JOB_POSTINGS.search(source)
        and re.search(r"\bsearchTerm\b", source)
        and re.search(r"\bsearchMode\b", source)
    ):
        return None
    endpoints = {
        endpoint
        for match in _URL_PROPERTY.finditer(source)
        if (endpoint := _declared_endpoint(match.group("url"), page_url)) is not None
    }
    if len(endpoints) != 1:
        return None
    fields = {"searchTerm": "", "searchMode": "search"}
    if re.search(r"\.send\([^;]{0,1000}\bjobFormat\b", source, re.I | re.S):
        format_match = re.search(
            r"\bdata-format\s*=\s*(['\"])(?P<value>[^'\"]{0,128})\1",
            page_html,
            re.I,
        )
        fields["jobFormat"] = (
            format_match.group("value") if format_match else "undefined"
        )
    return _Declaration(asset_url, next(iter(endpoints)), tuple(fields.items()))


def _literal_constants(source: str) -> dict[str, str]:
    output: dict[str, str] = {}
    for match in _ASSIGNMENT.finditer(source):
        parsed = _scalar(match.group("value"))
        if parsed is not None:
            output[match.group("name")] = parsed
    return output


def _declared_fields(data_object: str, constants: dict[str, str]) -> dict[str, str] | None:
    fields: dict[str, str] = {}
    for match in _PROPERTY.finditer(data_object):
        key = match.group("key").strip("'\"")
        raw_value = match.group("value")
        if _SENSITIVE_KEY.search(key):
            return None
        if key == "searchTerm":
            fields[key] = ""
            continue
        if _PAGE_OFFSET_KEY.fullmatch(key) and re.fullmatch(_IDENTIFIER, raw_value):
            fields[key] = "0"
            continue
        value = _scalar(raw_value)
        if value is None:
            value = constants.get(raw_value)
        if value is None:
            return None
        fields[key] = value
    if "searchTerm" not in fields or not fields.get("searchMode"):
        return None
    page_sizes = [
        int(value)
        for key, value in fields.items()
        if _PAGE_SIZE_KEY.fullmatch(key) and value.isdigit()
    ]
    if not page_sizes or any(value < 1 or value > MAX_PAGE_SIZE for value in page_sizes):
        return None
    for key, value in fields.items():
        if _PAGE_OFFSET_KEY.fullmatch(key) and (not value.isdigit() or int(value) < 0):
            return None
    return fields


def _named_literal_object(
    source: str,
    name: str,
    *,
    before: int,
) -> tuple[int, int] | None:
    assignment = re.compile(
        rf"\b(?:const|let|var)\s+{re.escape(name)}\s*=\s*\{{"
    )
    matches = list(assignment.finditer(source, 0, before))
    if len(matches) != 1:
        return None
    return _object_from_open_brace(source, matches[0].end() - 1)


def _scalar(value: str) -> str | None:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    if re.fullmatch(r"-?\d+|true|false|null", value, re.I):
        return value.casefold()
    return None


def _enclosing_object(source: str, position: int) -> tuple[int, int] | None:
    stack: list[int] = []
    objects: list[tuple[int, int]] = []
    quote: str | None = None
    escaped = False
    for index, character in enumerate(source):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in "'\"`":
            quote = character
        elif character == "{":
            stack.append(index)
        elif character == "}" and stack:
            start = stack.pop()
            if start < position < index:
                objects.append((start, index))
        if index > position and not stack:
            break
    return min(objects, key=lambda item: item[1] - item[0]) if objects else None


def _object_from_open_brace(source: str, start: int) -> tuple[int, int] | None:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(source)):
        character = source[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in "'\"`":
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return start, index
    return None


def _declared_endpoint(value: str, page_url: str) -> str | None:
    if any(token in value for token in ("${", "{{", "}}")):
        return None
    endpoint = _public_https_url(urljoin(page_url, value))
    if endpoint is None or not _same_origin(endpoint, page_url):
        return None
    return endpoint


def _parse_candidates(
    body: str,
    page_url: str,
    endpoint_url: str,
    limit: int,
) -> tuple[list[JSListingCandidate], bool, bool]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return [], False, False
    postings = _find_job_postings(payload)
    if not isinstance(postings, list):
        return [], False, False
    output: list[JSListingCandidate] = []
    seen: set[tuple[str, str]] = set()
    truncated = False
    for record in postings:
        if not isinstance(record, dict):
            continue
        title = _text_field(record, _TITLE_KEYS)
        raw_url = _text_field(record, _URL_KEYS)
        if not title or not raw_url:
            continue
        candidate_url = _candidate_url(raw_url, page_url)
        if candidate_url is None:
            continue
        key = (candidate_url.rstrip("/"), title.casefold())
        if key in seen:
            continue
        if len(output) >= limit:
            truncated = True
            break
        seen.add(key)
        output.append(
            JSListingCandidate(
                title=title,
                location=_text_field(record, _LOCATION_KEYS),
                url=candidate_url,
                source_url=endpoint_url,
            )
        )
    return output, True, truncated


def _find_job_postings(value: object, depth: int = 0) -> object | None:
    if depth > 12:
        return None
    if isinstance(value, dict):
        if "jobPostings" in value:
            return value["jobPostings"]
        for child in value.values():
            found = _find_job_postings(child, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value[:100]:
            found = _find_job_postings(child, depth + 1)
            if found is not None:
                return found
    return None


def _text_field(record: dict, names: tuple[str, ...]) -> str | None:
    for name in names:
        value = record.get(name)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:1000]
    return None


def _candidate_url(value: str, page_url: str) -> str | None:
    candidate = _public_https_url(urljoin(page_url, value))
    if candidate is None:
        return None
    if _same_site(candidate, page_url):
        return candidate
    link = RawLink(candidate, "", page_url, origin="js_declared_inventory")
    scored = score_job_link(link, page_url)
    return candidate if is_ats_url(candidate) and is_likely_job_detail(scored) else None


def _public_https_url(value: str) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_URL_CHARS:
        return None
    try:
        parsed = urlparse(value)
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "https"
        or not _public_host(host)
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
        or any(_SENSITIVE_KEY.search(key) for key, _value in query)
    ):
        return None
    return urlunparse(("https", host, parsed.path or "/", "", parsed.query, ""))


def _public_host(host: str) -> bool:
    if (
        not host
        or not _HOSTNAME.fullmatch(host)
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
    ):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return "." in host


def _same_origin(first: str, second: str) -> bool:
    left, right = urlparse(first), urlparse(second)
    return left.scheme == right.scheme and left.hostname == right.hostname and left.port == right.port


def _same_site(first: str, second: str) -> bool:
    left = (urlparse(first).hostname or "").casefold().rstrip(".")
    right = (urlparse(second).hostname or "").casefold().rstrip(".")
    return left == right or left.endswith("." + right) or right.endswith("." + left)


def _fetch_failure(
    exc: BaseException,
    status: str,
    considered: tuple[str, ...],
    fetched: tuple[str, ...],
    *,
    endpoint_url: str | None = None,
    request_fields: tuple[str, ...] = (),
    detail: str | None = None,
) -> JSDeclaredInventoryResult:
    http_status = exc.status if isinstance(exc, FetchError) else None
    blocked = http_status in {403, 429}
    retryable = (
        http_status == 429
        or (isinstance(exc, FetchError) and exc.retryable is True)
        or isinstance(exc, (OSError, TimeoutError))
    )
    typed_status = "rate_limited" if http_status == 429 else "blocked" if http_status == 403 else status
    return _result(
        typed_status,
        retryable=retryable,
        blocked=blocked,
        assets_considered=considered,
        assets_fetched=fetched,
        endpoint_url=endpoint_url,
        request_fields=request_fields,
        detail=detail or str(exc),
    )


def _result(
    status: str,
    *,
    retryable: bool = False,
    blocked: bool = False,
    assets_considered: tuple[str, ...] = (),
    assets_fetched: tuple[str, ...] = (),
    endpoint_url: str | None = None,
    request_fields: tuple[str, ...] = (),
    detail: str | None = None,
) -> JSDeclaredInventoryResult:
    return JSDeclaredInventoryResult(
        candidates=(),
        inventory_complete=False,
        trace=JSInventoryTrace(
            status=status,
            retryable=retryable,
            blocked=blocked,
            assets_considered=assets_considered,
            assets_fetched=assets_fetched,
            endpoint_url=endpoint_url,
            request_fields=request_fields,
            detail=detail,
        ),
    )


def _validate_limit(name: str, value: int, upper: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
        raise ValueError(f"{name} must be between 1 and {upper}")
