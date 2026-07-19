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
MAX_INLINE_SETTINGS = 3
MAX_SETTINGS_CHARS = 500_000
MAX_RESPONSE_CHARS = 5_000_000
MAX_CANDIDATES = 5_000
MAX_PAGE_SIZE = 5_000
MAX_URL_CHARS = 8_192
MAX_DRUPAL_PAGES = 50
MAX_JTABLE_PAGES = 50

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
_TITLE_KEYS = (
    "title", "Title", "jobTitle", "job_title", "positionTitle", "position",
)
_LOCATION_KEYS = (
    "location", "Location", "jobLocation", "job_location", "locationName",
)
_URL_KEYS = (
    "url", "Url", "jobUrl", "job_url", "detailUrl", "detail_url", "applyUrl",
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
_LANGUAGE_PREFIX = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.I)
_JOB_SEARCH_ENDPOINT = re.compile(
    r"^(?=[a-z0-9_-]*job)(?=[a-z0-9_-]*search)[a-z0-9_-]+$", re.I
)
_SAME_ORIGIN_GET_JOBS_PATH = re.compile(
    r"(?P<quote>['\"])(?P<path>/wp-json/[a-z0-9_.-]{1,80}/jobs/?)(?P=quote)",
    re.I,
)
_GET_KEYWORD_PARAMETER = re.compile(
    r"['\"](?:[?&])keyword=['\"]\s*\+\s*encodeURIComponent\(", re.I
)
_GET_LIMIT_PARAMETER = re.compile(r"['\"](?:[?&])limit=['\"]\s*\+", re.I)
_GET_JOB_RECORDS = re.compile(
    r"\b(?:response\.data|tmpjobs)\b.*?\.map\(\s*function\s*\(\s*job\s*\)"
    r".*?\bjob\.title\b",
    re.I | re.S,
)
_SOLR_SELECT_ENDPOINT = re.compile(
    r"(?P<quote>['\"])(?P<path>/bin/[a-z0-9_./-]{1,120})"
    r"\?searchType=select&searchTerm=(?P=quote)",
    re.I,
)
_SOLR_SELECT_CONTROLS = re.compile(
    r"&start=['\"]?\s*\+[^;]{0,300}&rows=['\"]?\s*\+[^;]{0,300}"
    r"&wt=json",
    re.I | re.S,
)
_SOLR_SELECT_RESPONSE = re.compile(
    r"\.response\.docs\b.{0,5000}\.response\.numFound\b|"
    r"\.response\.numFound\b.{0,5000}\.response\.docs\b",
    re.I | re.S,
)
_SOLR_RECORDS_BINDING = re.compile(
    r"recordsList\s*=\s*[^;]{0,200}\.response\.docs\b",
    re.I,
)
_SOLR_RECORD_URL = re.compile(
    r"recordsList\s*\[[^\]]+\]\.url\b",
    re.I,
)
_SOLR_RECORD_TITLE = re.compile(
    r"recordsList\s*\[[^\]]+\]\.title\b",
    re.I,
)
_CAREER_SEARCH_BUILDER = re.compile(
    r"\bbuildCareersSearchQuery\s*=\s*function\s*\([^)]{0,200}\)\s*\{",
    re.I,
)
_QUERY_LITERAL = re.compile(
    r"[?&](?P<key>[A-Za-z][A-Za-z0-9_-]{0,39})=(?P<value>[^&'\"]{0,200})"
)
_FETCH_BUILT_URL = re.compile(
    rf"\b(?P<name>{_IDENTIFIER})\s*=\s*[^;]{{1,500}}"
    rf"\.buildCareersSearchQuery\([^;]{{1,500}}\)\s*;\s*"
    rf"fetch\(\s*(?P=name)\s*\)",
    re.I,
)
_FETCH_CAREER_RESPONSE = re.compile(
    r"\.Total\b.{0,500}\.OpenPositions\b.{0,500}\.OpenPositions\b",
    re.S,
)
_FETCH_CAREER_CONTROLS = {
    "pageApp",
    "count",
    "display",
    "careerLocation",
    "careerPosition",
    "isLink",
}
_INLINE_HTML_POST = re.compile(
    r"\$\.ajax\s*\(\s*\{(?P<body>.{0,5000}?)\}\s*\)", re.I | re.S
)
_HTML_POST_ENDPOINT = re.compile(
    r"\burl\s*:\s*(?P<quote>['\"])(?P<url>/[^'\"]{1,500})(?P=quote)", re.I
)
_HTML_POST_METHOD = re.compile(r"\bmethod\s*:\s*['\"]POST['\"]", re.I)
_HTML_SEARCH_INPUT = re.compile(
    r"<input\b[^>]*\bname\s*=\s*['\"]freetext['\"][^>]*>", re.I
)
_HTML_JOB_PATH = re.compile(
    r"^/job/[a-z0-9_-]{1,160}/[a-z0-9_-]{1,160}/[1-9][0-9]{0,18}/?$",
    re.I,
)
_JTABLE_PAGE_SIZE = re.compile(
    r"\bPAGE_SIZE\s*:\s*(?P<size>[1-9][0-9]{0,3})\b", re.I
)
_JTABLE_PAGING = re.compile(
    r"\[\s*['\"]jtStartIndex['\"]\s*,\s*['\"]jtPageSize['\"]\s*,\s*"
    r"['\"]jtSorting['\"]\s*\]\.forEach\(\s*\(\s*param\s*\)\s*=>\s*\{"
    r".{0,500}?params\.set\(\s*param\s*,\s*jTableParams\[param\]\s*\)",
    re.I | re.S,
)
_JTABLE_ENDPOINT = re.compile(
    r"\burl\s*:\s*(?P<quote>['\"])(?P<path>/[^?'\"]{1,300}"
    r"search[^?'\"]*results[^?'\"]*)\?(?P=quote)\s*\+\s*params\.toString\(\)",
    re.I,
)
_JTABLE_JSON_GET = re.compile(
    r"\btype\s*:\s*['\"]GET['\"].{0,300}\bdataType\s*:\s*['\"]json['\"]|"
    r"\bdataType\s*:\s*['\"]json['\"].{0,300}\btype\s*:\s*['\"]GET['\"]",
    re.I | re.S,
)
_JTABLE_DETAIL_TEMPLATE = re.compile(
    r"(?P<quote>['\"`])(?P<prefix>/[^'\"`$]{1,240}/)\$\{title\}/\$\{rowId\}"
    r"(?P=quote)",
    re.I,
)
_JTABLE_RECORD_DETAIL = re.compile(
    r"\b(?:[A-Za-z_$][A-Za-z0-9_$]{0,79}\.)?getJobHref\("
    r"\s*data\.record\.ID\s*,\s*"
    r"data\.record\.TrackingObject\.TitleJson\s*\)",
    re.I | re.S,
)
_JTABLE_KEYWORD = re.compile(
    r"\bKeyword\s*:\s*model\.Keyword\s*\?\?\s*['\"]['\"]", re.I
)
_HANDLEBARS_SLUG_DETAIL = re.compile(
    r"href\s*=\s*(?P<quote>['\"])(?P<prefix>[^'\"{}]{1,500}/)"
    r"\{\{\s*slug\s*\}\}(?P=quote)",
    re.I,
)


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
    inventory_scope: str = "title_filtered"


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
    method: str = "POST"
    response_keys: tuple[str, str] | None = None
    transport: str = "generic"
    detail_path: str | None = None


class _HTMLJobFragmentParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.depth = 0
        self.li_depth: int | None = None
        self.h2_depth: int | None = None
        self.anchor_depth: int | None = None
        self.anchor_url: str | None = None
        self.anchor_text: list[str] = []
        self.job_title: str | None = None
        self.job_url: str | None = None
        self.location_depth: int | None = None
        self.location_text: list[str] = []
        self.candidates: list[JSListingCandidate] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.depth += 1
        values = {key.casefold(): value or "" for key, value in attrs}
        tag = tag.casefold()
        if tag == "li" and self.li_depth is None:
            self.li_depth = self.depth
        elif tag == "h2" and self.li_depth is not None and self.h2_depth is None:
            self.h2_depth = self.depth
        elif tag == "a" and self.h2_depth is not None and self.anchor_depth is None:
            raw_url = values.get("href")
            try:
                path = urlparse(raw_url).path
            except (TypeError, ValueError):
                path = ""
            if raw_url and _HTML_JOB_PATH.fullmatch(path):
                self.anchor_depth = self.depth
                self.anchor_url = raw_url
                self.anchor_text = []
        elif (
            tag == "h3"
            and self.li_depth is not None
            and "fa-map-marker" in values.get("class", "").casefold()
        ):
            self.location_depth = self.depth
            self.location_text = []
        elif (
            tag == "i"
            and self.li_depth is not None
            and "fa-map-marker" in values.get("class", "").casefold()
        ):
            # The marker is normally nested inside the location h3.
            self.location_depth = self.depth - 1
            self.location_text = []

    def handle_data(self, data: str) -> None:
        if self.anchor_depth is not None:
            self.anchor_text.append(data)
        if self.location_depth is not None and self.depth >= self.location_depth:
            self.location_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "a" and self.anchor_depth == self.depth:
            title = " ".join("".join(self.anchor_text).split())
            url = _candidate_url(self.anchor_url or "", self.page_url)
            if title and len(title) <= 300 and url is not None:
                self.job_title = title
                self.job_url = url
            self.anchor_depth = None
            self.anchor_url = None
            self.anchor_text = []
        if tag == "h2" and self.h2_depth == self.depth:
            self.h2_depth = None
        if tag == "h3" and self.location_depth == self.depth:
            self.location_depth = None
        if tag == "li" and self.li_depth == self.depth:
            if self.job_title is not None and self.job_url is not None:
                location = " ".join("".join(self.location_text).split()) or None
                self.candidates.append(
                    JSListingCandidate(
                        self.job_title, location, self.job_url, self.page_url
                    )
                )
            self.li_depth = None
            self.h2_depth = None
            self.anchor_depth = None
            self.job_title = None
            self.job_url = None
            self.location_depth = None
            self.location_text = []
        self.depth = max(0, self.depth - 1)


class _ScriptParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.urls: list[str] = []
        self.settings: list[str] = []
        self.settings_overflow = False
        self._in_drupal_settings = False
        self._settings_parts: list[str] = []
        self._settings_chars = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        values = {key.casefold(): value or "" for key, value in attrs}
        if (
            values.get("type", "").casefold() == "application/json"
            and values.get("data-drupal-selector", "").casefold()
            == "drupal-settings-json"
        ):
            self._in_drupal_settings = True
            self._settings_parts = []
            self._settings_chars = 0
        if values.get("src"):
            self.urls.append(urljoin(self.page_url, values["src"]))

    def handle_data(self, data: str) -> None:
        if self._in_drupal_settings:
            self._settings_chars += len(data)
            if self._settings_chars <= MAX_SETTINGS_CHARS:
                self._settings_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script" or not self._in_drupal_settings:
            return
        value = "".join(self._settings_parts)
        if self._settings_chars <= MAX_SETTINGS_CHARS:
            if len(self.settings) < MAX_INLINE_SETTINGS:
                self.settings.append(value)
            else:
                self.settings_overflow = True
        self._in_drupal_settings = False
        self._settings_parts = []
        self._settings_chars = 0


class _CareerSettingsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []
        self.overflow = False
        self._in_json = False
        self._parts: list[str] = []
        self._chars = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        values = {key.casefold(): value or "" for key, value in attrs}
        if values.get("type", "").casefold() == "application/json":
            self._in_json = True
            self._parts = []
            self._chars = 0

    def handle_data(self, data: str) -> None:
        if not self._in_json:
            return
        self._chars += len(data)
        if self._chars <= MAX_SETTINGS_CHARS:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script" or not self._in_json:
            return
        if self._chars <= MAX_SETTINGS_CHARS:
            if len(self.values) < MAX_INLINE_SETTINGS:
                self.values.append("".join(self._parts))
            else:
                self.overflow = True
        else:
            self.overflow = True
        self._in_json = False
        self._parts = []
        self._chars = 0


class _DeclaredAjaxFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        self._endpoint: str | None = None
        self._fields: list[tuple[str, str]] = []
        self._invalid = False
        self._select_name: str | None = None
        self._select_options: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "form" and self._endpoint is None:
            if values.get("method", "get").casefold() != "post":
                return
            endpoint = values.get("data-ajax", "").strip()
            if endpoint:
                self._endpoint = endpoint
                self._fields = []
                self._invalid = False
            return
        if self._endpoint is None:
            return
        if tag.casefold() == "option" and self._select_name is not None:
            self._select_options.append(
                (values.get("value", ""), "selected" in values)
            )
            return
        if tag.casefold() not in {"input", "select"}:
            return
        name = values.get("name", "").strip()
        if not name or len(name) > 160:
            return
        if _SENSITIVE_KEY.search(name) or len(self._fields) >= 32:
            self._invalid = True
            return
        if tag.casefold() == "select":
            self._select_name = name
            self._select_options = []
            return
        self._fields.append((name, values.get("value", "")))

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "select" and self._select_name is not None:
            selected = [value for value, is_selected in self._select_options if is_selected]
            if not selected and self._select_options:
                selected = [self._select_options[0][0]]
            if selected != [""] or len(self._fields) >= 32:
                self._invalid = True
            else:
                self._fields.append((self._select_name, ""))
            self._select_name = None
            self._select_options = []
            return
        if tag.casefold() != "form" or self._endpoint is None:
            return
        if not self._invalid:
            self.forms.append((self._endpoint, tuple(self._fields)))
        self._endpoint = None
        self._fields = []
        self._invalid = False


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

    html_form_declarations = _declared_html_data_ajax_inventory(
        page.html or "", page_url
    )
    if len(html_form_declarations) > 1:
        return _result("ambiguous_transport")
    if html_form_declarations:
        return _fetch_html_data_ajax_inventory(
            fetcher,
            page_url,
            html_form_declarations[0],
            max_candidates,
        )

    drupal_endpoints, settings_overflow = _drupal_job_search_endpoints(
        page.html or "", page_url
    )
    if settings_overflow or len(drupal_endpoints) > 1:
        return _result("ambiguous_transport")
    if drupal_endpoints:
        return _fetch_drupal_job_search(
            fetcher,
            page_url,
            drupal_endpoints[0],
            title.strip(),
            max_candidates,
        )

    inline_declaration = _declared_inline_html_post(page.html or "", page_url)
    if inline_declaration is not None:
        return _fetch_inline_html_post(
            fetcher, page_url, inline_declaration, title.strip(), max_candidates
        )

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
        asset_source = asset_page.html or ""
        if len(asset_source) > MAX_ASSET_CHARS:
            continue
        declaration = _declared_transport(
            asset_source,
            asset_url,
            page_url,
            page.html or "",
        )
        if declaration is not None:
            declarations.append(declaration)
        declarations.extend(
            _declared_same_origin_get_inventory(
                asset_source,
                asset_url,
                page_url,
                page.html or "",
            )
        )
        declarations.extend(
            _declared_jtable_get_inventory(asset_source, asset_url, page_url)
        )

    unique = {
        (
            item.method,
            item.endpoint_url,
            item.fields,
            item.response_keys,
            item.transport,
            item.detail_path,
        ): item
        for item in declarations
    }
    if len(unique) != 1:
        status = "transport_not_declared" if not unique else "ambiguous_transport"
        return _result(
            status,
            assets_considered=considered,
            assets_fetched=tuple(fetched),
        )
    declaration = next(iter(unique.values()))
    if declaration.transport == "jtable":
        return _fetch_jtable_inventory(
            fetcher,
            page_url,
            declaration,
            title.strip(),
            max_candidates,
            considered,
            tuple(fetched),
        )
    fields = dict(declaration.fields)
    if declaration.method == "GET":
        for key in ("keyword", "careerPosition", "searchTerm"):
            if key in fields:
                fields[key] = title.strip()
        for key in ("limit", "rows"):
            if key in fields:
                fields[key] = str(max_candidates)
        request_url = declaration.endpoint_url + "?" + urlencode(fields)
        body = None
        headers = {"Accept": "application/json"}
    else:
        fields["searchTerm"] = title.strip()
        request_url = declaration.endpoint_url
        body = urlencode(fields).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
    try:
        response = fetcher.fetch(request_url, data=body, headers=headers)
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
    if response_url != request_url:
        return _result(
            "transport_redirect_rejected",
            assets_considered=considered,
            assets_fetched=tuple(fetched),
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(fields),
        )

    response_body = response.html or ""
    if len(response_body) > MAX_RESPONSE_CHARS:
        candidates, valid_payload, truncated = [], False, False
    else:
        candidates, valid_payload, truncated = _parse_candidates(
            response_body,
            page_url,
            request_url,
            max_candidates,
            response_keys=declaration.response_keys,
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


def inspect_js_declared_inventory_transport(
    fetcher: FetchClient,
    page: Page,
    *,
    max_assets: int = MAX_ASSETS,
) -> JSInventoryTrace:
    """Verify that a first-party listing page declares one anonymous job transport."""

    _validate_limit("max_assets", max_assets, MAX_ASSETS)
    page_url = _public_https_url(page.final_url or page.url)
    if page_url is None:
        return _result(
            "unsafe_listing_url", detail="listing URL is not public HTTPS"
        ).trace

    html_form_declarations = _declared_html_data_ajax_inventory(
        page.html or "", page_url
    )
    if len(html_form_declarations) > 1:
        return _result("ambiguous_transport").trace
    if html_form_declarations:
        declaration = html_form_declarations[0]
        return JSInventoryTrace(
            status="declared",
            retryable=False,
            blocked=False,
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(dict(declaration.fields)),
            inventory_scope="full",
        )

    drupal_endpoints, settings_overflow = _drupal_job_search_endpoints(
        page.html or "", page_url
    )
    if settings_overflow or len(drupal_endpoints) > 1:
        return _result("ambiguous_transport").trace
    if drupal_endpoints:
        return JSInventoryTrace(
            status="declared",
            retryable=False,
            blocked=False,
            endpoint_url=drupal_endpoints[0],
            request_fields=("q", "area", "location", "from"),
        )


    inline_declaration = _declared_inline_html_post(page.html or "", page_url)
    if inline_declaration is not None:
        return JSInventoryTrace(
            status="declared",
            retryable=False,
            blocked=False,
            endpoint_url=inline_declaration.endpoint_url,
            request_fields=tuple(dict(inline_declaration.fields)),
        )

    asset_urls = _script_urls(page.html or "", page_url)
    considered = tuple(asset_urls[:max_assets])
    fetched: list[str] = []
    declarations: list[_Declaration] = []
    for asset_url in considered:
        try:
            asset_page = fetcher.fetch(asset_url)
        except (FetchError, OSError, TimeoutError) as exc:
            return _fetch_failure(
                exc,
                "asset_fetch_failed",
                considered,
                tuple(fetched),
                detail=asset_url,
            ).trace
        response_url = _public_https_url(asset_page.final_url or asset_page.url)
        if response_url != asset_url:
            return _result(
                "asset_redirect_rejected",
                assets_considered=considered,
                assets_fetched=tuple(fetched),
                detail=asset_url,
            ).trace
        fetched.append(asset_url)
        source = asset_page.html or ""
        if len(source) > MAX_ASSET_CHARS:
            continue
        declaration = _declared_transport(
            source, asset_url, page_url, page.html or ""
        )
        if declaration is not None:
            declarations.append(declaration)
        declarations.extend(
            _declared_same_origin_get_inventory(
                source, asset_url, page_url, page.html or ""
            )
        )
        declarations.extend(
            _declared_jtable_get_inventory(source, asset_url, page_url)
        )

    unique = {
        (
            item.method,
            item.endpoint_url,
            item.fields,
            item.response_keys,
            item.transport,
            item.detail_path,
        ): item
        for item in declarations
    }
    if len(unique) != 1:
        return _result(
            "transport_not_declared" if not unique else "ambiguous_transport",
            assets_considered=considered,
            assets_fetched=tuple(fetched),
        ).trace
    declaration = next(iter(unique.values()))
    return JSInventoryTrace(
        status="declared",
        retryable=False,
        blocked=False,
        assets_considered=considered,
        assets_fetched=tuple(fetched),
        endpoint_url=declaration.endpoint_url,
        request_fields=tuple(dict(declaration.fields)),
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


def _declared_html_data_ajax_inventory(
    html: str,
    page_url: str,
) -> list[_Declaration]:
    if not isinstance(html, str) or len(html) > MAX_ASSET_CHARS:
        return []
    detail_match = _HANDLEBARS_SLUG_DETAIL.search(html)
    if detail_match is None:
        return []
    detail_probe = _declared_endpoint(
        urljoin(page_url, detail_match.group("prefix") + "inventory-probe"),
        page_url,
    )
    if detail_probe is None:
        return []
    detail_path = detail_probe.rsplit("/", 1)[0] + "/"

    parser = _DeclaredAjaxFormParser()
    try:
        parser.feed(html[:MAX_ASSET_CHARS])
        parser.close()
    except (TypeError, ValueError):
        return []

    declarations: list[_Declaration] = []
    for raw_endpoint, fields in parser.forms:
        endpoint = _declared_endpoint(raw_endpoint, page_url)
        if endpoint is None or not fields:
            continue
        names = [name.casefold() for name, _value in fields]
        if len(names) != len(set(names)):
            continue
        declarations.append(
            _Declaration(
                asset_url=page_url,
                endpoint_url=endpoint,
                fields=fields,
                method="POST",
                response_keys=("", "results"),
                transport="html_data_ajax",
                detail_path=detail_path,
            )
        )
    return declarations


def _fetch_html_data_ajax_inventory(
    fetcher: FetchClient,
    page_url: str,
    declaration: _Declaration,
    max_candidates: int,
) -> JSDeclaredInventoryResult:
    fields = dict(declaration.fields)
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
            (),
            (),
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(fields),
        )
    response_url = _public_https_url(response.final_url or response.url)
    if response_url != declaration.endpoint_url:
        return _result(
            "transport_redirect_rejected",
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(fields),
        )
    candidates, valid_payload, truncated = _parse_slug_inventory_candidates(
        response.html or "",
        page_url,
        declaration.endpoint_url,
        declaration.detail_path or "",
        max_candidates,
    )
    if not valid_payload:
        return _result(
            "invalid_job_postings_payload",
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
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(fields),
            candidate_count=len(candidates),
            inventory_scope="full",
        ),
    )


def _declared_inline_html_post(html: str, page_url: str) -> _Declaration | None:
    if (
        not isinstance(html, str)
        or len(html) > MAX_ASSET_CHARS
        or not _HTML_SEARCH_INPUT.search(html)
        or "X-Total-Count" not in html
        or ".jobresults" not in html
        or ".find('li')" not in html
        or "limit_page" not in html
        or "page_start" not in html
    ):
        return None
    declarations: list[_Declaration] = []
    for match in _INLINE_HTML_POST.finditer(html):
        body = match.group("body")
        endpoint_match = _HTML_POST_ENDPOINT.search(body)
        if endpoint_match is None or not _HTML_POST_METHOD.search(body):
            continue
        endpoint = _declared_endpoint(endpoint_match.group("url"), page_url)
        if endpoint is None or not endpoint.endswith("/ajax.php"):
            continue
        limit_match = re.search(r"\b(?:var|let|const)\s+limit\s*=\s*(\d{1,4})", html)
        if limit_match is None:
            continue
        limit = int(limit_match.group(1))
        if not 1 <= limit <= MAX_PAGE_SIZE:
            continue
        declarations.append(
            _Declaration(
                page_url,
                endpoint,
                (("freetext", ""), ("limit_page", str(limit)), ("page_start", "0")),
                method="POST_HTML",
            )
        )
    unique = {(item.endpoint_url, item.fields): item for item in declarations}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _fetch_inline_html_post(
    fetcher: FetchClient,
    page_url: str,
    declaration: _Declaration,
    title: str,
    max_candidates: int,
) -> JSDeclaredInventoryResult:
    landing_url = page_url + ("&" if urlparse(page_url).query else "?") + urlencode(
        {"freetext": title}
    )
    try:
        landing = fetcher.fetch(landing_url)
    except (FetchError, OSError, TimeoutError) as exc:
        return _fetch_failure(
            exc,
            "transport_fetch_failed",
            (),
            (),
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(dict(declaration.fields)),
        )
    if _public_https_url(landing.final_url or landing.url) != landing_url:
        return _result(
            "transport_redirect_rejected",
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(dict(declaration.fields)),
        )
    fields = dict(declaration.fields)
    fields["freetext"] = title
    body = urlencode(fields).encode("utf-8")
    try:
        response = fetcher.fetch(
            declaration.endpoint_url,
            data=body,
            headers={
                "Accept": "text/html,*/*;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
    except (FetchError, OSError, TimeoutError) as exc:
        return _fetch_failure(
            exc,
            "transport_fetch_failed",
            (),
            (),
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(fields),
        )
    if _public_https_url(response.final_url or response.url) != declaration.endpoint_url:
        return _result(
            "transport_redirect_rejected",
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(fields),
        )
    parser = _HTMLJobFragmentParser(page_url)
    try:
        parser.feed((response.html or "")[:MAX_RESPONSE_CHARS])
        parser.close()
    except (TypeError, ValueError):
        return _result("invalid_job_postings_payload")
    deduped = list({(item.url, item.title.casefold()): item for item in parser.candidates}.values())
    if not deduped:
        return _result(
            "invalid_job_postings_payload",
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(fields),
        )
    truncated = len(deduped) > max_candidates
    candidates = deduped[:max_candidates]
    return JSDeclaredInventoryResult(
        candidates=tuple(candidates),
        inventory_complete=False,
        trace=JSInventoryTrace(
            status="candidate_cap_reached" if truncated else "verified",
            retryable=False,
            blocked=False,
            endpoint_url=declaration.endpoint_url,
            request_fields=tuple(fields),
            candidate_count=len(candidates),
            detail="filtered HTML inventory; completeness is not attested",
        ),
    )


def _drupal_job_search_endpoints(
    html: str, page_url: str
) -> tuple[list[str], bool]:
    parser = _ScriptParser(page_url)
    try:
        parser.feed(html[:2_000_000])
        parser.close()
    except (TypeError, ValueError):
        return [], False
    output: list[str] = []
    for source in parser.settings:
        try:
            settings = json.loads(source)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(settings, dict):
            continue
        job_search = settings.get("dd_job_search")
        if not isinstance(job_search, dict):
            continue
        declared = job_search.get("hero_input_search_path")
        endpoint = _drupal_job_search_endpoint(declared, page_url)
        if endpoint is not None and endpoint not in output:
            output.append(endpoint)
    return output, parser.settings_overflow


def _career_search_endpoints(html: str, page_url: str) -> tuple[list[str], bool]:
    parser = _CareerSettingsParser()
    try:
        parser.feed(html[:2_000_000])
        parser.close()
    except (TypeError, ValueError):
        return [], True
    output: list[str] = []
    invalid = parser.overflow
    for source in parser.values:
        if "careerSearchPath" not in source:
            continue
        try:
            settings = json.loads(source)
        except (json.JSONDecodeError, TypeError, ValueError):
            invalid = True
            continue
        if not isinstance(settings, dict):
            invalid = True
            continue
        endpoint = _declared_endpoint(settings.get("careerSearchPath"), page_url)
        if endpoint is None:
            invalid = True
        elif endpoint not in output:
            output.append(endpoint)
    return output, invalid


def _drupal_job_search_endpoint(value: object, page_url: str) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_URL_CHARS:
        return None
    try:
        declared = urlparse(value)
    except (TypeError, ValueError):
        return None
    path = declared.path
    basename = path.rstrip("/").rsplit("/", 1)[-1]
    if not _JOB_SEARCH_ENDPOINT.fullmatch(basename):
        return None
    if value.startswith("/") and not value.startswith("//"):
        page_parts = [part for part in urlparse(page_url).path.split("/") if part]
        endpoint_parts = [part for part in path.split("/") if part]
        if (
            page_parts
            and _LANGUAGE_PREFIX.fullmatch(page_parts[0])
            and (
                not endpoint_parts
                or endpoint_parts[0].casefold() != page_parts[0].casefold()
            )
        ):
            path = "/" + "/".join((page_parts[0], *endpoint_parts))
            value = urlunparse(("", "", path, "", declared.query, ""))
    return _declared_endpoint(value, page_url)


def _fetch_drupal_job_search(
    fetcher: FetchClient,
    page_url: str,
    endpoint_url: str,
    title: str,
    max_candidates: int,
) -> JSDeclaredInventoryResult:
    request_fields = ("q", "area", "location", "from")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    candidates: list[JSListingCandidate] = []
    seen_candidates: set[tuple[str, str]] = set()
    seen_pages: set[tuple[tuple[str, str], ...]] = set()
    offset = 0
    expected_total: int | None = None
    complete = False

    for _page_number in range(MAX_DRUPAL_PAGES):
        request = {"q": title, "area": "all", "location": [], "from": offset}
        try:
            response = fetcher.fetch(
                endpoint_url,
                data=json.dumps(request, separators=(",", ":")).encode("utf-8"),
                headers=headers,
            )
        except (FetchError, OSError, TimeoutError) as exc:
            return _fetch_failure(
                exc,
                "transport_fetch_failed",
                (),
                (),
                endpoint_url=endpoint_url,
                request_fields=request_fields,
            )
        response_url = _public_https_url(response.final_url or response.url)
        if response_url != endpoint_url:
            return _result(
                "transport_redirect_rejected",
                endpoint_url=endpoint_url,
                request_fields=request_fields,
            )
        body = response.html or ""
        if len(body) > MAX_RESPONSE_CHARS:
            return _result(
                "invalid_job_postings_payload",
                endpoint_url=endpoint_url,
                request_fields=request_fields,
            )
        page_candidates, valid_payload, total, hit_count, fingerprint = (
            _parse_elasticsearch_page(body, page_url, endpoint_url)
        )
        if not valid_payload or total is None:
            return _result(
                "invalid_job_postings_payload",
                endpoint_url=endpoint_url,
                request_fields=request_fields,
            )
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            break
        if hit_count and fingerprint in seen_pages:
            break
        seen_pages.add(fingerprint)

        for candidate in page_candidates:
            key = (candidate.url.rstrip("/"), candidate.title.casefold())
            if key in seen_candidates:
                continue
            if len(candidates) >= max_candidates:
                break
            seen_candidates.add(key)
            candidates.append(candidate)

        offset += hit_count
        if offset >= total:
            complete = True
            break
        if not hit_count or len(candidates) >= max_candidates:
            break

    status = "verified" if complete else "candidate_cap_reached"
    return JSDeclaredInventoryResult(
        candidates=tuple(candidates),
        inventory_complete=complete,
        trace=JSInventoryTrace(
            status=status,
            retryable=False,
            blocked=False,
            endpoint_url=endpoint_url,
            request_fields=request_fields,
            candidate_count=len(candidates),
        ),
    )


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


def _declared_same_origin_get_inventory(
    source: str,
    asset_url: str,
    page_url: str,
    page_html: str,
) -> tuple[_Declaration, ...]:
    """Accept a narrow anonymous GET inventory contract declared by first-party JS."""

    declarations: list[_Declaration] = []
    if not (
        _CREDENTIALS.search(source)
        or not re.search(
            r"\baxios[\w$]*(?:\.[\w$]+){0,4}\.get\(\s*url\s*\)",
            source,
            re.I,
        )
        or not _GET_KEYWORD_PARAMETER.search(source)
        or not _GET_LIMIT_PARAMETER.search(source)
        or not _GET_JOB_RECORDS.search(source)
    ):
        endpoints = {
            endpoint
            for match in _SAME_ORIGIN_GET_JOBS_PATH.finditer(source)
            if (endpoint := _declared_endpoint(match.group("path"), page_url))
            is not None
        }
        declarations.extend(
            _Declaration(
                asset_url,
                endpoint,
                (("keyword", ""), ("limit", "")),
                method="GET",
            )
            for endpoint in sorted(endpoints)
        )
    declarations.extend(
        _declared_literal_fetch_get_inventory(
            source, asset_url, page_url, page_html
        )
    )
    declarations.extend(
        _declared_solr_select_inventory(source, asset_url, page_url)
    )
    return tuple(declarations)


def _declared_jtable_get_inventory(
    source: str,
    asset_url: str,
    page_url: str,
) -> tuple[_Declaration, ...]:
    """Recognize a bounded anonymous jTable JSON inventory declared by JS."""

    if (
        _CREDENTIALS.search(source)
        or not _JTABLE_PAGING.search(source)
        or not _JTABLE_JSON_GET.search(source)
        or not _JTABLE_RECORD_DETAIL.search(source)
        or not _JTABLE_KEYWORD.search(source)
    ):
        return ()
    page_sizes = {
        int(match.group("size")) for match in _JTABLE_PAGE_SIZE.finditer(source)
    }
    detail_paths = {
        match.group("prefix") for match in _JTABLE_DETAIL_TEMPLATE.finditer(source)
    }
    endpoints = {
        endpoint
        for match in _JTABLE_ENDPOINT.finditer(source)
        if (endpoint := _declared_endpoint(match.group("path"), page_url)) is not None
    }
    if (
        len(page_sizes) != 1
        or not 1 <= next(iter(page_sizes)) <= MAX_PAGE_SIZE
        or len(detail_paths) != 1
        or len(endpoints) != 1
    ):
        return ()
    detail_path = next(iter(detail_paths))
    if _declared_endpoint(detail_path + "title/id", page_url) is None:
        return ()
    page_size = next(iter(page_sizes))
    return (
        _Declaration(
            asset_url,
            next(iter(endpoints)),
            (
                ("Keyword", ""),
                ("jtStartIndex", "0"),
                ("jtPageSize", str(page_size)),
            ),
            method="GET",
            response_keys=("TotalRecordCount", "Records"),
            transport="jtable",
            detail_path=detail_path,
        ),
    )


def _fetch_jtable_inventory(
    fetcher: FetchClient,
    page_url: str,
    declaration: _Declaration,
    title: str,
    max_candidates: int,
    assets_considered: tuple[str, ...],
    assets_fetched: tuple[str, ...],
) -> JSDeclaredInventoryResult:
    fields = dict(declaration.fields)
    request_fields = tuple(fields)
    page_size = min(int(fields["jtPageSize"]), max_candidates)
    candidates: list[JSListingCandidate] = []
    seen_candidates: set[tuple[str, str]] = set()
    seen_pages: set[tuple[tuple[str, str], ...]] = set()
    offset = 0
    expected_total: int | None = None
    complete = False

    for _page_number in range(MAX_JTABLE_PAGES):
        request = {
            "Keyword": title,
            "jtStartIndex": str(offset),
            "jtPageSize": str(min(page_size, max_candidates - len(candidates))),
        }
        request_url = declaration.endpoint_url + "?" + urlencode(request)
        try:
            response = fetcher.fetch(
                request_url, data=None, headers={"Accept": "application/json"}
            )
        except (FetchError, OSError, TimeoutError) as exc:
            return _fetch_failure(
                exc,
                "transport_fetch_failed",
                assets_considered,
                assets_fetched,
                endpoint_url=declaration.endpoint_url,
                request_fields=request_fields,
            )
        response_url = _public_https_url(response.final_url or response.url)
        if response_url != request_url:
            return _result(
                "transport_redirect_rejected",
                assets_considered=assets_considered,
                assets_fetched=assets_fetched,
                endpoint_url=declaration.endpoint_url,
                request_fields=request_fields,
            )
        body = response.html or ""
        if len(body) > MAX_RESPONSE_CHARS:
            return _result(
                "invalid_job_postings_payload",
                assets_considered=assets_considered,
                assets_fetched=assets_fetched,
                endpoint_url=declaration.endpoint_url,
                request_fields=request_fields,
            )
        page_candidates, valid, total, record_count, fingerprint = _parse_jtable_page(
            body,
            page_url,
            request_url,
            declaration.detail_path or "",
        )
        if not valid or total is None:
            return _result(
                "invalid_job_postings_payload",
                assets_considered=assets_considered,
                assets_fetched=assets_fetched,
                endpoint_url=declaration.endpoint_url,
                request_fields=request_fields,
            )
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            break
        if record_count and fingerprint in seen_pages:
            break
        seen_pages.add(fingerprint)
        for candidate in page_candidates:
            key = (candidate.url.rstrip("/"), candidate.title.casefold())
            if key in seen_candidates:
                continue
            if len(candidates) >= max_candidates:
                break
            seen_candidates.add(key)
            candidates.append(candidate)
        offset += record_count
        if offset >= total:
            complete = True
            break
        if not record_count or len(candidates) >= max_candidates:
            break

    return JSDeclaredInventoryResult(
        candidates=tuple(candidates),
        inventory_complete=complete,
        trace=JSInventoryTrace(
            status="verified" if complete else "candidate_cap_reached",
            retryable=False,
            blocked=False,
            assets_considered=assets_considered,
            assets_fetched=assets_fetched,
            endpoint_url=declaration.endpoint_url,
            request_fields=request_fields,
            candidate_count=len(candidates),
        ),
    )


def _parse_jtable_page(
    body: str,
    page_url: str,
    endpoint_url: str,
    detail_path: str,
) -> tuple[
    list[JSListingCandidate],
    bool,
    int | None,
    int,
    tuple[tuple[str, str], ...],
]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return [], False, None, 0, ()
    if isinstance(payload, str):
        if len(payload) > MAX_RESPONSE_CHARS:
            return [], False, None, 0, ()
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError, ValueError):
            return [], False, None, 0, ()
    if not isinstance(payload, dict) or payload.get("Result") != "OK":
        return [], False, None, 0, ()
    records = payload.get("Records")
    total = payload.get("TotalRecordCount")
    if (
        not isinstance(records, list)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total < len(records)
    ):
        return [], False, None, 0, ()

    output: list[JSListingCandidate] = []
    fingerprint: list[tuple[str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            return [], False, None, 0, ()
        row_id = record.get("ID")
        tracking = record.get("TrackingObject")
        title = tracking.get("TitleJson") if isinstance(tracking, dict) else None
        if (
            not isinstance(row_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", row_id)
            or not isinstance(title, str)
            or not title.strip()
            or len(title) > 300
        ):
            return [], False, None, 0, ()
        title = " ".join(title.split())
        fingerprint.append((row_id, title))
        slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
        if not slug:
            return [], False, None, 0, ()
        candidate_url = _candidate_url(f"{detail_path}{slug}/{row_id}", page_url)
        if candidate_url is None:
            continue
        location: str | None = None
        locations = (
            tracking.get("LocationNamesJson")
            if isinstance(tracking, dict)
            else None
        )
        if isinstance(locations, list):
            clean_locations = [
                " ".join(value.split())
                for value in locations
                if isinstance(value, str) and value.strip() and len(value) <= 300
            ]
            location = ", ".join(clean_locations) or None
        output.append(
            JSListingCandidate(title, location, candidate_url, endpoint_url)
        )
    return output, True, total, len(records), tuple(fingerprint)


def _declared_solr_select_inventory(
    source: str,
    asset_url: str,
    page_url: str,
) -> tuple[_Declaration, ...]:
    """Recognize a bounded anonymous Solr-style title search declared by JS."""

    if (
        _CREDENTIALS.search(source)
        or not _SOLR_SELECT_CONTROLS.search(source)
        or not _SOLR_SELECT_RESPONSE.search(source)
        or not _SOLR_RECORDS_BINDING.search(source)
        or not _SOLR_RECORD_URL.search(source)
        or not _SOLR_RECORD_TITLE.search(source)
        or "encodeURIComponent" not in source
    ):
        return ()
    endpoints = {
        endpoint
        for match in _SOLR_SELECT_ENDPOINT.finditer(source)
        if (endpoint := _declared_endpoint(match.group("path"), page_url))
        is not None
    }
    return tuple(
        _Declaration(
            asset_url,
            endpoint,
            (
                ("searchType", "select"),
                ("searchTerm", ""),
                ("start", "0"),
                ("rows", ""),
                ("wt", "json"),
            ),
            method="GET",
            response_keys=("response.numFound", "response.docs"),
        )
        for endpoint in sorted(endpoints)
    )


def _declared_literal_fetch_get_inventory(
    source: str,
    asset_url: str,
    page_url: str,
    page_html: str,
) -> tuple[_Declaration, ...]:
    builder_match = _CAREER_SEARCH_BUILDER.search(source)
    fetch_match = _FETCH_BUILT_URL.search(source)
    if builder_match is None or fetch_match is None:
        return ()
    fetch_tail = source[fetch_match.end() : fetch_match.end() + 2_000]
    if not _FETCH_CAREER_RESPONSE.search(fetch_tail):
        return ()
    bounds = _object_from_open_brace(source, builder_match.end() - 1)
    if bounds is None or bounds[1] - bounds[0] > 5_000:
        return ()
    builder = source[bounds[0] : bounds[1] + 1]
    controls: dict[str, str] = {}
    for match in _QUERY_LITERAL.finditer(builder):
        key = match.group("key")
        if key in controls:
            return ()
        controls[key] = match.group("value")
    if set(controls) != _FETCH_CAREER_CONTROLS:
        return ()
    if (
        controls["pageApp"] != "getCareers"
        or controls["isLink"].casefold() != "false"
        or controls["careerLocation"]
        or controls["careerPosition"]
    ):
        return ()
    try:
        count = int(controls["count"])
        display = int(controls["display"])
    except (TypeError, ValueError):
        return ()
    if count != display or count < 1 or count > MAX_PAGE_SIZE:
        return ()

    endpoints, invalid_settings = _career_search_endpoints(page_html, page_url)
    if invalid_settings:
        return ()
    fields = (
        ("pageApp", "getCareers"),
        ("count", str(count)),
        ("display", str(display)),
        ("careerPosition", ""),
        ("isLink", "false"),
    )
    return tuple(
        _Declaration(
            asset_url,
            endpoint,
            fields,
            method="GET",
            response_keys=("Total", "OpenPositions"),
        )
        for endpoint in sorted(endpoints)
    )


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
    *,
    response_keys: tuple[str, str] | None = None,
) -> tuple[list[JSListingCandidate], bool, bool]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return [], False, False
    declared_total: int | None = None
    if response_keys is not None:
        total_key, postings_key = response_keys
        if not isinstance(payload, dict):
            return [], False, False
        declared_total = _nested_payload_value(payload, total_key)
        postings = _nested_payload_value(payload, postings_key)
        if (
            isinstance(declared_total, bool)
            or not isinstance(declared_total, int)
            or not isinstance(postings, list)
            or declared_total < len(postings)
        ):
            return [], False, False
    else:
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
        raw_url = _url_field(record, _URL_KEYS)
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
                location=_record_location(record),
                url=candidate_url,
                source_url=endpoint_url,
            )
        )
    if declared_total is not None and declared_total > len(postings):
        truncated = True
    return output, True, truncated


def _parse_slug_inventory_candidates(
    body: str,
    page_url: str,
    endpoint_url: str,
    detail_path: str,
    limit: int,
) -> tuple[list[JSListingCandidate], bool, bool]:
    if len(body) > MAX_RESPONSE_CHARS or not detail_path:
        return [], False, False
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return [], False, False
    if (
        not isinstance(payload, dict)
        or payload.get("status") not in {"success", "ok"}
        or not isinstance(payload.get("results"), list)
    ):
        return [], False, False

    records = payload["results"]
    output: list[JSListingCandidate] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        if not isinstance(record, dict):
            return [], False, False
        title = _text_field(record, _TITLE_KEYS)
        slug = record.get("slug")
        if (
            not title
            or not isinstance(slug, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", slug)
        ):
            return [], False, False
        url = _candidate_url(urljoin(detail_path, slug), page_url)
        if url is None:
            return [], False, False
        key = (url.rstrip("/"), title.casefold())
        if key in seen:
            continue
        seen.add(key)
        if len(output) == limit:
            return output, True, True
        output.append(
            JSListingCandidate(
                title=title,
                location=_record_location(record),
                url=url,
                source_url=endpoint_url,
            )
        )
    return output, True, False


def _nested_payload_value(payload: dict, path: str):
    value: object = payload
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _parse_elasticsearch_candidates(
    body: str,
    page_url: str,
    endpoint_url: str,
    limit: int,
) -> tuple[list[JSListingCandidate], bool, bool]:
    output, valid, total, hit_count, _fingerprint = _parse_elasticsearch_page(
        body, page_url, endpoint_url
    )
    if not valid or total is None:
        return [], False, False
    return output[:limit], True, total > hit_count or len(output) > limit


def _parse_elasticsearch_page(
    body: str,
    page_url: str,
    endpoint_url: str,
) -> tuple[
    list[JSListingCandidate],
    bool,
    int | None,
    int,
    tuple[tuple[str, str], ...],
]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return [], False, None, 0, ()
    if not isinstance(payload, dict):
        return [], False, None, 0, ()
    hits_container = payload.get("hits")
    if not isinstance(hits_container, dict):
        return [], False, None, 0, ()
    hits = hits_container.get("hits")
    total = hits_container.get("total")
    if isinstance(total, dict):
        total = total.get("value")
    if (
        not isinstance(hits, list)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total < len(hits)
    ):
        return [], False, None, 0, ()

    output: list[JSListingCandidate] = []
    seen: set[tuple[str, str]] = set()
    fingerprint: list[tuple[str, str]] = []
    for hit in hits:
        if not isinstance(hit, dict) or not isinstance(hit.get("_source"), dict):
            continue
        source = hit["_source"]
        title = _text_field(source, ("title",))
        raw_url = _url_field(source, ("url",))
        fingerprint.append((title or "", raw_url or ""))
        if not title or not raw_url:
            continue
        candidate_url = _candidate_url(raw_url, page_url)
        if candidate_url is None:
            continue
        key = (candidate_url.rstrip("/"), title.casefold())
        if key in seen:
            continue
        seen.add(key)
        city = _text_field(source, ("city",))
        country = _text_field(source, ("country",))
        locations: list[str] = []
        for value in (city, country):
            if value and value.casefold() not in {
                existing.casefold() for existing in locations
            }:
                locations.append(value)
        location = ", ".join(locations) or None
        output.append(
            JSListingCandidate(
                title=title,
                location=location,
                url=candidate_url,
                source_url=endpoint_url,
            )
        )
    return output, True, total, len(hits), tuple(fingerprint)


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
        if any(
            isinstance(child, dict)
            and _text_field(child, _TITLE_KEYS)
            and _text_field(child, _URL_KEYS)
            for child in value[:100]
        ):
            return value
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


def _url_field(record: dict, names: tuple[str, ...]) -> str | None:
    for name in names:
        value = record.get(name)
        if (
            isinstance(value, str)
            and value
            and len(value) <= MAX_URL_CHARS
            and not any(character.isspace() for character in value)
        ):
            return value
    return None


def _record_location(record: dict) -> str | None:
    direct = _text_field(record, _LOCATION_KEYS)
    if direct:
        return direct
    parts: list[str] = []
    for value in (
        _text_field(record, ("city",)),
        _text_field(record, ("state", "region")),
        _text_field(record, ("country",)),
    ):
        if value and value.casefold() not in {item.casefold() for item in parts}:
            parts.append(value)
    return ", ".join(parts) or None


def _candidate_url(value: str, page_url: str) -> str | None:
    if len(value) > MAX_URL_CHARS:
        return None
    try:
        joined = urljoin(page_url, value)
    except (TypeError, ValueError):
        return None
    candidate = _public_https_url(joined)
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
