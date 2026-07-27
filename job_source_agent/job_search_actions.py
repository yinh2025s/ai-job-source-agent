from __future__ import annotations

from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .browser_interaction import JobSearchInteraction
from .contracts import FetchClient
from .generic_opening_inventory import generic_opening_inventory_fingerprint
from .web import FetchError, Page, safe_normalize_url


MAX_FORMS = 12
MAX_FIELDS = 32
MAX_TRACE_ITEMS = 24
MAX_HELPER_SOURCE_CHARS = 1_000_000
MAX_HELPER_RESPONSE_CHARS = 262_144
MAX_HELPER_URL_CHARS = 2_048
MAX_HELPER_ASSETS = 3
MAX_TITLE_SEARCH_QUERIES = 3
MAX_SUBMISSION_BYTES = 16_384
MAX_SUBMISSION_RESPONSE_CHARS = 5_000_000

_QUERY_NAMES = {
    "k",
    "keyword",
    "keywords",
    "q",
    "query",
    "search",
    "searchfield",
    "searchkeyword",
    "term",
}
_SENSITIVE_NAMES = {
    "access_token",
    "auth",
    "authorization",
    "code",
    "key",
    "password",
    "session",
    "state",
    "token",
}
_JOB_CONTEXT = re.compile(r"\b(?:career|job|opening|position|role|vacanc)", re.I)
_QUERY_CONTEXT = re.compile(r"\b(?:job\s+title|keyword|search\s+jobs?|role)\b", re.I)
_LOCATION_CONTEXT = re.compile(
    r"\b(?:location|city|state|province|postal|zip|country)\b",
    re.I,
)
_INTERACTIVE_QUERY_NAMES = {
    "jobtitle",
    "job_title",
    "searchfield",
    "searchkeyword",
}
_INTERACTIVE_CLASS_TOKENS = {"action", "btn", "button", "search"}
_SUBMIT_TEXT = re.compile(
    r"^(?:search(?:\s+(?:jobs?|openings?|roles?))?|find\s+jobs?)$",
    re.I,
)
_GET_HELPER_CALL = re.compile(
    r"(?P<caller>fetch|(?:[A-Za-z_$][A-Za-z0-9_$]*\.)*get|\$\.get)"
    r"\s*\(\s*(?P<quote>['\"`])"
    r"(?P<url>[^'\"`\r\n]{1,2048})(?P=quote)",
    re.I,
)
_EXPLICIT_GET = re.compile(r"\bmethod\s*:\s*['\"]GET['\"]", re.I)
_CONCAT_GET_HELPER = re.compile(
    r"(?:\.[A-Za-z_$][A-Za-z0-9_$]*\.)?get\(\s*"
    r"(?P<quote>['\"])(?P<path>/[^'\"\r\n]{1,500}get-search-results\?)"
    r"(?P=quote)\.concat\((?P<context>.{1,1600})",
    re.I | re.S,
)
_CONCAT_TEXT_QUERY = re.compile(
    r"['\"]&text=['\"]\.concat\([^)]{0,200}\.query\b",
    re.I | re.S,
)
_CREDENTIAL_MARKER = re.compile(
    r"\b(?:authorization|credentials|password|secret|token|withCredentials)\b",
    re.I,
)
_CANCEL_TOKEN_OPTION = re.compile(
    r"\bcancelToken\s*:\s*[A-Za-z_$][A-Za-z0-9_$.]{0,120}",
    re.I,
)
_CORE_TITLE_WITH_SUFFIX = re.compile(
    r"^(?P<core>.+?)\s*\((?P<qualifier>[^()]{1,120})\)\s*-\s*"
    r"(?P<suffix>.+)$"
)
_ROLE_WITH_PRODUCT_OR_TEAM = re.compile(
    r"^(?P<role>.+?)\s*-\s*(?P<suffix>.+)$"
)
_SENIORITY_OR_LEVEL = re.compile(
    r"\b(?:intern|junior|senior|lead|principal|staff|director|manager|"
    r"vp|vice\s+president|level\s*[0-9ivx]+|[ivx]{1,4}|[0-9]{1,2})\b",
    re.I,
)
_GENERIC_PRODUCT_OR_TEAM_WORDS = {
    "business", "department", "group", "organization", "platform", "product",
    "products", "team",
}


@dataclass(frozen=True)
class TitleSearchQuery:
    value: str
    source: str


def title_search_queries(target_title: str) -> tuple[TitleSearchQuery, ...]:
    """Return a small, deterministic portfolio for first-party title search."""

    full_title = _normalize_title_query(target_title)
    if not full_title:
        return ()

    queries = [TitleSearchQuery(full_title, "full_title")]
    core_match = _CORE_TITLE_WITH_SUFFIX.fullmatch(full_title)
    if core_match is not None:
        core = _normalize_title_query(core_match.group("core"))
        removed = " ".join((core_match.group("qualifier"), core_match.group("suffix")))
        if _is_safe_title_variant(core, removed=removed):
            queries.append(TitleSearchQuery(core, "core_title"))

    product_match = _ROLE_WITH_PRODUCT_OR_TEAM.fullmatch(full_title)
    if product_match is not None:
        product_or_team = _normalize_title_query(product_match.group("suffix"))
        if _is_informative_product_or_team(product_or_team):
            queries.append(TitleSearchQuery(product_or_team, "product_or_team"))

    deduped: list[TitleSearchQuery] = []
    seen: set[str] = set()
    for query in queries:
        identity = _title_query_identity(query.value)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        deduped.append(query)
        if len(deduped) == MAX_TITLE_SEARCH_QUERIES:
            break
    return tuple(deduped)


def _normalize_title_query(value: str) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _title_query_identity(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _is_safe_title_variant(value: str, *, removed: str = "") -> bool:
    tokens = _title_query_identity(value).split()
    return (
        len(tokens) >= 2
        and sum(len(token) for token in tokens) >= 5
        and not _SENIORITY_OR_LEVEL.search(removed)
    )


def _is_informative_product_or_team(value: str) -> bool:
    tokens = _title_query_identity(value).split()
    return (
        _is_safe_title_variant(value)
        and not _SENIORITY_OR_LEVEL.search(value)
        and any(token not in _GENERIC_PRODUCT_OR_TEAM_WORDS for token in tokens)
    )


@dataclass(frozen=True)
class JobSearchAction:
    method: str
    url: str
    query_field: str
    static_fields: tuple[tuple[str, str], ...]
    source: str = "declared_get_form"

    def __post_init__(self) -> None:
        method = self.method.upper()
        if method not in {"GET", "POST"}:
            raise ValueError("job search method must be GET or POST")
        if not self.query_field or self.query_field.casefold() in _SENSITIVE_NAMES:
            raise ValueError("job search query field is unsafe")
        if any(
            not key or key.casefold() in _SENSITIVE_NAMES
            for key, _value in self.static_fields
        ):
            raise ValueError("job search static field is unsafe")

    def request_url(self, target_title: str) -> str:
        if self.method.upper() == "POST":
            return urlunparse(urlparse(self.url)._replace(fragment=""))
        parsed = urlparse(self.url)
        items = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() != self.query_field.casefold()
        ]
        items.extend(
            (key, value)
            for key, value in self.static_fields
            if key.casefold() != self.query_field.casefold()
        )
        items.append((self.query_field, target_title))
        return urlunparse(parsed._replace(query=urlencode(items)))

    def request_data(self, target_title: str) -> bytes | None:
        if self.method.upper() != "POST":
            return None
        fields = [
            (key, value)
            for key, value in self.static_fields
            if key.casefold() != self.query_field.casefold()
        ]
        fields.append((self.query_field, target_title))
        data = urlencode(fields).encode("utf-8")
        if len(data) > MAX_SUBMISSION_BYTES:
            raise ValueError("job search submission exceeds the body limit")
        return data

    def request_headers(self) -> dict[str, str] | None:
        if self.method.upper() != "POST":
            return None
        return {
            "Accept": "application/json, text/html",
            "Content-Type": "application/x-www-form-urlencoded",
        }


@dataclass(frozen=True)
class JobSearchActionDiscovery:
    actions: tuple[JobSearchAction, ...]
    trace: tuple[dict[str, str], ...]
    interactive_actions: tuple[JobSearchInteraction, ...] = ()


@dataclass(frozen=True)
class JobSearchRouteResult:
    """A followable search route, never evidence of a matching opening."""

    route_url: str | None
    status: str
    helper_url: str | None = None


@dataclass(frozen=True)
class JobSearchSubmissionResult:
    page: Page | None
    status: str
    request_url: str
    change_kind: str | None = None


def submit_job_search_action(
    fetcher: FetchClient,
    page: Page,
    action: JobSearchAction,
    target_title: str,
) -> JobSearchSubmissionResult:
    """Execute one bounded action and require semantic response progress."""

    page_url = safe_normalize_url(page.final_url or page.url)
    request_url = safe_normalize_url(action.request_url(target_title), page_url)
    if (
        not page_url
        or not request_url
        or not _safe_same_origin(request_url, page_url)
    ):
        return JobSearchSubmissionResult(None, "transport_unsafe", request_url or "")
    try:
        response = fetcher.fetch(
            request_url,
            data=action.request_data(target_title),
            headers=action.request_headers(),
        )
    except (FetchError, OSError, TimeoutError, ValueError):
        return JobSearchSubmissionResult(None, "transport_failed", request_url)

    return verify_job_search_submission(
        page,
        response,
        request_url=request_url,
        allow_route=action.source != "declared_post_api",
    )


def verify_job_search_submission(
    before: Page,
    response: Page,
    *,
    request_url: str,
    allow_route: bool = True,
) -> JobSearchSubmissionResult:
    """Type a completed form/browser/API submission from semantic progress."""

    page_url = safe_normalize_url(before.final_url or before.url)
    response_url = safe_normalize_url(response.final_url or response.url)
    if (
        not page_url
        or not response_url
        or not _safe_same_origin(response_url, page_url)
    ):
        return JobSearchSubmissionResult(None, "transport_unsafe_response", request_url)
    change_kind = _submission_change_kind(
        before,
        response,
        allow_route=allow_route,
    )
    if change_kind is None:
        return JobSearchSubmissionResult(
            None,
            "transport_unchanged",
            request_url,
        )
    return JobSearchSubmissionResult(
        _embedded_listing_page(before, response) or response,
        "submitted",
        request_url,
        change_kind,
    )


def _submission_change_kind(
    before: Page,
    after: Page,
    *,
    allow_route: bool,
) -> str | None:
    before_url = safe_normalize_url(before.final_url or before.url)
    after_url = safe_normalize_url(after.final_url or after.url)
    if allow_route and before_url and after_url and before_url != after_url:
        return "route"

    before_listing = generic_opening_inventory_fingerprint(before)
    after_listing = generic_opening_inventory_fingerprint(after)
    if after_listing is not None and after_listing != before_listing:
        return "listing_fingerprint"

    before_payload = _json_payload_fingerprint(before.html)
    after_payload = _json_payload_fingerprint(after.html)
    if after_payload is not None and after_payload != before_payload:
        return "payload_fingerprint"
    return None


def _json_payload_fingerprint(body: str) -> str | None:
    if not isinstance(body, str) or len(body) > MAX_SUBMISSION_RESPONSE_CHARS:
        return None
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, (dict, list)):
        return None
    if not _is_search_payload(payload):
        return None
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _is_search_payload(payload: dict | list) -> bool:
    if isinstance(payload, dict):
        if payload.get("success") is False or payload.get("error"):
            return False
        semantic_keys = {
            "html", "items", "jobs", "postings", "records", "results",
            "searchurl", "total",
        }
        if any(key.casefold() in semantic_keys for key in payload):
            return True
        return any(
            _is_search_payload(value)
            for value in payload.values()
            if isinstance(value, (dict, list))
        )
    return bool(payload) and any(
        isinstance(value, dict) and _is_search_payload(value)
        for value in payload
    )


def _embedded_listing_page(before: Page, response: Page) -> Page | None:
    try:
        payload = json.loads(response.html)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    html_values: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.casefold() == "html" and isinstance(item, str):
                    html_values.append(item)
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    if len(html_values) != 1 or len(html_values[0]) > MAX_SUBMISSION_RESPONSE_CHARS:
        return None
    listing_url = before.final_url or before.url
    listing_page = Page(
        listing_url,
        html_values[0],
        final_url=listing_url,
        source=f"{response.source}|declared_post_api_html",
    )
    return (
        listing_page
        if generic_opening_inventory_fingerprint(listing_page) is not None
        else None
    )


@dataclass
class _Field:
    name: str
    field_id: str
    placeholder: str
    value: str
    input_type: str
    semantic_text: str


@dataclass
class _Button:
    submit_tag: str
    button_type: str
    href: str
    text: list[str]


@dataclass
class _Form:
    action: str
    method: str
    marker: str
    fields: list[_Field]
    buttons: list[_Button]


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[_Form] = []
        self.current: _Form | None = None
        self._active_button: _Button | None = None
        self._active_button_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        values = {name.casefold(): (value or "") for name, value in attrs}
        if tag == "form" and self.current is None and len(self.forms) < MAX_FORMS:
            self.current = _Form(
                action=values.get("action", ""),
                method=values.get("method", "get").casefold(),
                marker=" ".join(
                    values.get(name, "")
                    for name in ("id", "class", "aria-label", "data-testid")
                ),
                fields=[],
                buttons=[],
            )
            return
        if self.current is None:
            return
        if self._active_button is not None:
            if tag == self._active_button.submit_tag:
                self._active_button_depth += 1
            return
        if tag == "input" and len(self.current.fields) < MAX_FIELDS:
            name = values.get("name", "")
            input_type = values.get("type", "text").casefold()
            if input_type in {"button", "submit"}:
                self.current.buttons.append(
                    _Button(
                        "input",
                        input_type,
                        "",
                        [values.get("value", "")],
                    )
                )
            else:
                self.current.fields.append(
                    _Field(
                        name=name,
                        field_id=values.get("id", ""),
                        placeholder=values.get("placeholder", ""),
                        value=values.get("value", ""),
                        input_type=input_type,
                        semantic_text=" ".join(
                            values.get(key, "")
                            for key in ("name", "id", "placeholder", "aria-label")
                        ),
                    )
                )
        elif tag == "button":
            button = _Button(
                "button",
                values.get("type", "submit").casefold(),
                "",
                [values.get("aria-label", "")],
            )
            self.current.buttons.append(button)
            self._active_button = button
            self._active_button_depth = 1
        elif (
            tag in {"a", "span"}
            and self._active_button is None
            and _is_button_like(values)
        ):
            button = _Button(
                tag,
                "button",
                values.get("href", "") if tag == "a" else "",
                [values.get("aria-label", "")],
            )
            self.current.buttons.append(button)
            self._active_button = button
            self._active_button_depth = 1

    def handle_data(self, data: str) -> None:
        if self.current is not None and self._active_button is not None and data.strip():
            self._active_button.text.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self._active_button is not None and tag == self._active_button.submit_tag:
            self._active_button_depth -= 1
            if self._active_button_depth == 0:
                self._active_button = None
        if tag == "form" and self.current is not None:
            self.forms.append(self.current)
            self.current = None
            self._active_button = None
            self._active_button_depth = 0


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sources: list[str] = []
        self.assets: list[tuple[str, str]] = []
        self._parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script" or self._parts is not None:
            return
        values = {name.casefold(): (value or "") for name, value in attrs}
        if values.get("src"):
            self.assets.append((values["src"], values.get("data-chunk", "")))
            return
        script_type = values.get("type", "").casefold()
        if not values.get("src") and script_type not in {"application/json", "application/ld+json"}:
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._parts is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._parts is not None:
            self.sources.append("".join(self._parts))
            self._parts = None


def discover_job_search_actions(
    page: Page,
    target_title: str | None = None,
) -> JobSearchActionDiscovery:
    """Discover bounded, same-origin search transports declared by a job page."""

    page_url = page.final_url or page.url
    parser = _FormParser()
    try:
        parser.feed(page.html or "")
        parser.close()
    except (TypeError, ValueError):
        return JobSearchActionDiscovery((), ({"disposition": "parse_failed"},), ())

    actions: list[JobSearchAction] = []
    trace: list[dict[str, str]] = []
    trace_forms: list[_Form] = []
    seen: set[tuple[str, str]] = set()
    for form in parser.forms:
        query_field = _query_field(form)
        disposition = "eligible"
        normalized = safe_normalize_url(form.action or page_url, page_url)
        if query_field is None or not query_field.name:
            disposition = "no_job_query_field"
        elif form.method not in {"get", "post"}:
            disposition = "unsupported_method"
        elif form.method == "post" and not (
            form.action
            and normalized
            and _safe_same_origin(normalized, page_url)
            and not _has_sensitive_fields(form)
            and _has_job_context(form, query_field, page_url, normalized)
        ):
            disposition = "unsupported_method"
        elif _is_interactive_only(form):
            disposition = "interactive_only"
        elif not normalized or not _safe_same_origin(normalized, page_url):
            disposition = "unsafe_action"
        elif not _has_job_context(form, query_field, page_url, normalized):
            disposition = "non_job_search"
        elif any(
            field.name.casefold() in _SENSITIVE_NAMES
            for field in form.fields
        ):
            disposition = "sensitive_fields"

        trace_item = {
            "disposition": disposition,
            "method": form.method,
            "action": normalized or "[invalid]",
            "query_field": query_field.name if query_field else "",
        }
        if query_field is not None and query_field.placeholder:
            trace_item["query_placeholder"] = query_field.placeholder
        if len(trace) < MAX_TRACE_ITEMS:
            trace.append(trace_item)
            trace_forms.append(form)
        if disposition != "eligible" or query_field is None or normalized is None:
            continue

        page_query = {
            key.casefold(): value
            for key, value in parse_qsl(
                urlparse(page_url).query,
                keep_blank_values=True,
            )
        }
        static_fields = tuple(
            (
                field.name,
                field.value or page_query.get(field.name.casefold(), ""),
            )
            for field in form.fields
            if field.name.casefold() != query_field.name.casefold()
            and field.input_type in {"hidden"}
        )
        key = (normalized, query_field.name.casefold())
        if key in seen:
            continue
        seen.add(key)
        actions.append(
            JobSearchAction(
                method=form.method.upper(),
                url=normalized,
                query_field=query_field.name,
                static_fields=static_fields,
                source=(
                    "declared_post_form"
                    if form.method == "post"
                    else "declared_get_form"
                ),
            )
        )

    interactive_actions: list[JobSearchInteraction] = []
    if not actions and target_title:
        eligible: list[tuple[int, JobSearchInteraction]] = []
        for ordinal, (form, trace_item) in enumerate(zip(trace_forms, trace)):
            interaction, disposition = _interactive_action(
                form,
                ordinal,
                page_url,
                target_title,
            )
            if interaction is not None:
                eligible.append((ordinal, interaction))
                trace_item["disposition"] = "interactive_eligible"
                trace_item["submit_text"] = interaction.submit_text
            elif trace_item["disposition"] in {
                "interactive_only",
                "unsupported_method",
                "no_job_query_field",
                "non_job_search",
            }:
                trace_item["disposition"] = disposition
        if len(eligible) == 1:
            interactive_actions.append(eligible[0][1])
        elif len(eligible) > 1:
            for ordinal, _interaction in eligible:
                trace[ordinal]["disposition"] = "ambiguous_interactive_forms"

    return JobSearchActionDiscovery(
        tuple(actions),
        tuple(trace),
        tuple(interactive_actions),
    )


def resolve_declared_search_route(
    fetcher: FetchClient,
    page: Page,
    target_title: str,
) -> JobSearchRouteResult:
    """Execute one explicitly declared anonymous GET search-route helper."""

    if not isinstance(target_title, str) or not target_title.strip():
        raise ValueError("target_title must be a non-empty string")
    page_url = safe_normalize_url(page.final_url or page.url)
    if not page_url or not _safe_same_origin(page_url, page_url):
        return JobSearchRouteResult(None, "unsafe_page_url")

    source = page.html or ""
    if len(source) > MAX_HELPER_SOURCE_CHARS:
        return JobSearchRouteResult(None, "source_oversize")
    declarations = list(_declared_get_helpers(source, page_url))
    if not declarations:
        declarations.extend(
            _declared_get_helpers_from_assets(fetcher, source, page_url)
        )
    if not declarations:
        return JobSearchRouteResult(None, "helper_undeclared")
    if len(declarations) != 1:
        return JobSearchRouteResult(None, "helper_ambiguous")

    helper_url = _helper_request_url(declarations[0], target_title.strip())
    if helper_url is None or len(helper_url) > MAX_HELPER_URL_CHARS:
        return JobSearchRouteResult(None, "helper_malformed")
    try:
        response = fetcher.fetch(helper_url, headers={"Accept": "application/json"})
    except (FetchError, OSError, TimeoutError):
        return JobSearchRouteResult(None, "helper_fetch_failed", helper_url)
    response_url = safe_normalize_url(response.final_url or response.url)
    if response_url != helper_url:
        return JobSearchRouteResult(None, "helper_redirect_rejected", helper_url)

    body = response.html or ""
    if len(body) > MAX_HELPER_RESPONSE_CHARS:
        return JobSearchRouteResult(None, "response_oversize", helper_url)
    payload = _unique_json_object(body)
    if payload is None:
        return JobSearchRouteResult(None, "response_malformed", helper_url)
    raw_route = _single_returned_url(payload)
    if raw_route is None or len(raw_route) > MAX_HELPER_URL_CHARS:
        return JobSearchRouteResult(None, "response_malformed", helper_url)
    route_url = safe_normalize_url(raw_route, page_url)
    if not route_url or not _safe_same_origin(route_url, page_url):
        return JobSearchRouteResult(None, "route_unsafe", helper_url)
    return JobSearchRouteResult(route_url, "resolved", helper_url)


def _declared_get_helpers(source: str, page_url: str) -> tuple[str, ...]:
    declarations: list[str] = []
    seen: set[str] = set()
    parser = _ScriptParser()
    try:
        parser.feed(source)
        parser.close()
    except (TypeError, ValueError):
        return ()
    for script in parser.sources:
        for declaration in _declared_get_helpers_from_script(script, page_url):
            if declaration not in seen:
                declarations.append(declaration)
                seen.add(declaration)
    return tuple(declarations)


def _declared_get_helpers_from_script(
    script: str,
    page_url: str,
) -> tuple[str, ...]:
    declarations: list[str] = []
    seen: set[str] = set()
    for match in _GET_HELPER_CALL.finditer(script):
            caller = match.group("caller").casefold()
            context = script[match.start():min(len(script), match.end() + 500)]
            if caller == "fetch" and not _EXPLICIT_GET.search(context):
                continue
            if _CREDENTIAL_MARKER.search(context):
                continue
            raw_url = match.group("url")
            raw_fields = parse_qsl(urlparse(raw_url).query, keep_blank_values=True)
            dynamic_keys = {
                key.casefold()
                for key, value in raw_fields
                if value.startswith("${") and value.endswith("}")
            }
            if not {"query", "text"}.issubset(dynamic_keys):
                continue
            normalized = safe_normalize_url(raw_url, page_url)
            if not normalized or not _safe_same_origin(normalized, page_url):
                continue
            keys = [key.casefold() for key, _value in parse_qsl(
                urlparse(normalized).query, keep_blank_values=True
            )]
            if keys.count("query") != 1 or keys.count("text") != 1:
                continue
            if normalized not in seen:
                declarations.append(normalized)
                seen.add(normalized)
    for match in _CONCAT_GET_HELPER.finditer(script):
        context = match.group("context")
        credential_context = _CANCEL_TOKEN_OPTION.sub("", context)
        if (
            _CREDENTIAL_MARKER.search(credential_context)
            or not _CONCAT_TEXT_QUERY.search(context)
            or not re.search(r"\.query\b", context)
        ):
            continue
        raw_url = match.group("path") + "query=${title}&text=${title}"
        normalized = safe_normalize_url(raw_url, page_url)
        if normalized and _safe_same_origin(normalized, page_url) and normalized not in seen:
            declarations.append(normalized)
            seen.add(normalized)
    return tuple(declarations)


def _declared_get_helpers_from_assets(
    fetcher: FetchClient,
    source: str,
    page_url: str,
) -> tuple[str, ...]:
    parser = _ScriptParser()
    try:
        parser.feed(source)
        parser.close()
    except (TypeError, ValueError):
        return ()
    ranked: list[tuple[int, str]] = []
    seen_assets: set[str] = set()
    for raw_url, chunk in parser.assets:
        asset_url = safe_normalize_url(raw_url, page_url)
        semantic = " ".join((raw_url, chunk)).casefold()
        if (
            not asset_url
            or asset_url in seen_assets
            or not _safe_same_origin(asset_url, page_url)
            or not urlparse(asset_url).path.casefold().endswith(".js")
            or not re.search(r"(?:job|search)", semantic)
        ):
            continue
        seen_assets.add(asset_url)
        asset_name = urlparse(asset_url).path.rsplit("/", 1)[-1]
        path_semantic = bool(re.search(r"(?:job|search)", asset_name, re.I))
        chunk_semantic = bool(re.search(r"(?:job|search)", chunk, re.I))
        ranked.append((4 if path_semantic else (2 if chunk_semantic else 1), asset_url))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    declarations: list[str] = []
    seen: set[str] = set()
    for _rank, asset_url in ranked[:MAX_HELPER_ASSETS]:
        try:
            page = fetcher.fetch(asset_url)
        except (FetchError, OSError, TimeoutError):
            continue
        final_url = safe_normalize_url(page.final_url or page.url)
        body = page.html or ""
        if final_url != asset_url or len(body) > MAX_HELPER_SOURCE_CHARS:
            continue
        for declaration in _declared_get_helpers_from_script(body, page_url):
            if declaration not in seen:
                declarations.append(declaration)
                seen.add(declaration)
        if declarations:
            break
    return tuple(declarations)


def _helper_request_url(declaration: str, target_title: str) -> str | None:
    try:
        parsed = urlparse(declaration)
        fields = parse_qsl(parsed.query, keep_blank_values=True)
    except (TypeError, ValueError):
        return None
    if any(key.casefold() in _SENSITIVE_NAMES for key, _value in fields):
        return None
    replaced = [
        (key, target_title if key.casefold() in {"query", "text"} else value)
        for key, value in fields
    ]
    request_url = urlunparse(parsed._replace(query=urlencode(replaced), fragment=""))
    return request_url if _safe_same_origin(request_url, declaration) else None


def _unique_json_object(body: str) -> dict[str, object] | None:
    duplicate = False

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        nonlocal duplicate
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                duplicate = True
            value[key] = item
        return value

    try:
        payload = json.loads(body, object_pairs_hook=object_pairs)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) and not duplicate else None


def _single_returned_url(payload: dict[str, object]) -> str | None:
    returned: list[tuple[str, str]] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and "url" in key.casefold() and item.strip():
                    returned.append((key, item))
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    if len(returned) != 1 or returned[0][0] != "searchUrl":
        return None
    return returned[0][1]


def _query_field(form: _Form) -> _Field | None:
    candidates = [
        field
        for field in form.fields
        if (
            (
                field.input_type in {"", "search", "text"}
                and (
                    field.name.casefold() in _QUERY_NAMES
                    or field.input_type == "search"
                    or _QUERY_CONTEXT.search(field.semantic_text)
                )
            )
            # Some server-rendered job boards use a visible, unnamed autocomplete
            # and mirror its value into a declared GET form before navigation.
            # The exact query-name allowlist keeps this from promoting arbitrary
            # hidden state into a search transport.
            or (
                field.input_type == "hidden"
                and field.name.casefold() in _QUERY_NAMES
            )
        )
    ]
    candidates.sort(
        key=lambda field: (
            field.input_type != "hidden",
            field.input_type == "search",
            field.name.casefold() in _QUERY_NAMES,
            bool(_QUERY_CONTEXT.search(field.semantic_text)),
        ),
        reverse=True,
    )
    return candidates[0] if candidates else None


def _is_interactive_only(form: _Form) -> bool:
    return bool(
        not form.action
        and any(button.button_type == "button" for button in form.buttons)
        and not any(button.button_type == "submit" for button in form.buttons)
    )


def _interactive_action(
    form: _Form,
    form_ordinal: int,
    page_url: str,
    target_title: str,
) -> tuple[JobSearchInteraction | None, str]:
    normalized_action = safe_normalize_url(form.action or page_url, page_url)
    if not normalized_action or not _safe_same_origin(normalized_action, page_url):
        return None, "interactive_unsafe_action"
    if _has_sensitive_fields(form):
        return None, "interactive_sensitive_fields"

    editable_fields = [
        field
        for field in form.fields
        if field.input_type in {"", "search", "text"}
    ]
    query_fields = [field for field in editable_fields if _is_interactive_query_field(field)]
    if len(query_fields) != 1:
        return None, "interactive_ambiguous_fields"
    query_field = query_fields[0]
    if any(
        field is not query_field and not _is_location_scope_field(field)
        for field in editable_fields
    ):
        return None, "interactive_ambiguous_fields"
    placeholder_identity = " ".join(query_field.placeholder.casefold().split())
    if (
        query_field.input_type not in {"", "search", "text"}
        or not any((query_field.name, query_field.field_id, query_field.placeholder))
        or not (
            _QUERY_CONTEXT.search(query_field.semantic_text)
            or query_field.name.casefold() in _INTERACTIVE_QUERY_NAMES
            or placeholder_identity == "job title"
        )
    ):
        return None, "interactive_low_confidence_query"

    search_buttons = [
        button
        for button in form.buttons
        if button.button_type in {"button", "submit"}
        and _SUBMIT_TEXT.fullmatch(_semantic_submit_text(button))
    ]
    if len(search_buttons) != 1:
        return None, "interactive_ambiguous_buttons"
    button = search_buttons[0]
    submit_text = _semantic_submit_text(button)
    if (
        button.button_type not in {"button", "submit"}
        or not _SUBMIT_TEXT.fullmatch(submit_text)
    ):
        return None, "interactive_non_search_button"
    if button.href:
        submit_url = safe_normalize_url(button.href, page_url)
        if not submit_url or not _safe_same_origin(submit_url, page_url):
            return None, "interactive_unsafe_submit"
    if not _has_job_context(
        form,
        query_field,
        page_url,
        normalized_action,
    ):
        return None, "interactive_non_job_search"

    try:
        return (
            JobSearchInteraction(
                form_ordinal=form_ordinal,
                query_name=query_field.name or None,
                query_id=query_field.field_id or None,
                query_placeholder=(
                    query_field.placeholder
                    if not query_field.name and not query_field.field_id
                    else None
                ),
                target_title=target_title,
                submit_text=submit_text,
                submit_tag=button.submit_tag,
            ),
            "interactive_eligible",
        )
    except ValueError:
        return None, "interactive_invalid_descriptor"


def _is_interactive_query_field(field: _Field) -> bool:
    placeholder_identity = " ".join(field.placeholder.casefold().split())
    return bool(
        field.input_type in {"", "search", "text"}
        and any((field.name, field.field_id, field.placeholder))
        and (
            _QUERY_CONTEXT.search(field.semantic_text)
            or field.name.casefold() in _INTERACTIVE_QUERY_NAMES
            or placeholder_identity == "job title"
        )
    )


def _is_location_scope_field(field: _Field) -> bool:
    return bool(
        field.input_type in {"", "search", "text"}
        and _LOCATION_CONTEXT.search(field.semantic_text)
        and not _QUERY_CONTEXT.search(field.semantic_text)
    )


def _has_sensitive_fields(form: _Form) -> bool:
    return any(
        field.input_type == "password"
        or field.name.casefold() in _SENSITIVE_NAMES
        or field.field_id.casefold() in _SENSITIVE_NAMES
        for field in form.fields
    )


def _is_button_like(values: dict[str, str]) -> bool:
    if values.get("role", "").casefold() == "button":
        return True
    class_tokens = {
        token.casefold()
        for token in values.get("class", "").split()
        if token
    }
    return bool(class_tokens & _INTERACTIVE_CLASS_TOKENS)


def _semantic_submit_text(button: _Button) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for value in button.text:
        normalized = " ".join(value.split())
        identity = normalized.casefold()
        if normalized and identity not in seen:
            parts.append(normalized)
            seen.add(identity)
    return " ".join(parts)


def _has_job_context(
    form: _Form,
    query_field: _Field,
    page_url: str,
    action_url: str,
) -> bool:
    action_path = urlparse(action_url).path
    button_text = " ".join(
        part
        for button in form.buttons
        for part in button.text
        if part
    )
    return bool(
        _JOB_CONTEXT.search(action_path)
        or _JOB_CONTEXT.search(form.marker)
        or _JOB_CONTEXT.search(button_text)
        or (
            _QUERY_CONTEXT.search(query_field.semantic_text)
            and _JOB_CONTEXT.search(urlparse(page_url).path)
        )
    )


def _safe_same_origin(url: str, page_url: str) -> bool:
    try:
        parsed = urlparse(url)
        page = urlparse(page_url)
        return bool(
            parsed.scheme == page.scheme == "https"
            and parsed.hostname
            and parsed.hostname.casefold() == (page.hostname or "").casefold()
            and parsed.username is None
            and parsed.password is None
            and parsed.port in {None, 443}
            and page.port in {None, 443}
            and not any(key.casefold() in _SENSITIVE_NAMES for key, _ in parse_qsl(parsed.query))
        )
    except (TypeError, ValueError):
        return False
