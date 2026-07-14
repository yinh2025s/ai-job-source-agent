from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import ipaddress
import json
import math
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_MAX_HTML_CHARS = 2_000_000
_MAX_PAGES = 10
_MAX_RECORDS_PER_PAGE = 100
_MAX_ROWS = _MAX_PAGES * _MAX_RECORDS_PER_PAGE
_ID = re.compile(r"^[1-9][0-9]{0,11}$")
_LOCALE = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$")
_DETAIL_PATH = re.compile(
    r"^/(?P<locale>[a-z]{2}(?:-[a-z]{2})?)/job/[^/?#]+(?:/[^/?#]+)*/"
    r"(?P<tenant>[0-9]{1,20})/(?P<job_id>[0-9]{1,20})/?$"
)
_SEARCH_META = (
    "site-tenant-id",
    "site-organization-id",
    "site-id",
    "gtm_tenantid",
    "gtm_companysiteid",
    "site-current-language",
    "site-url-modified-language-code",
)


class TalentBrewAdapter:
    name = "talentbrew"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        # TalentBrew sites use customer-owned origins and require page evidence.
        return False

    def identify_board(self, url: str) -> JobBoard | None:
        return None

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        parsed = _safe_public_https_url(page.final_url or page.url)
        if parsed is None:
            return None
        fingerprint = _fingerprint(page.html, page.final_url or page.url)
        if fingerprint is None:
            return None
        tenant_id, site_id, locale = fingerprint
        host = (parsed.hostname or "").casefold()
        board_url = f"https://{_url_host(host)}/{locale}/search-jobs"
        return JobBoard(
            url=board_url,
            provider=self.name,
            identifier=_identifier(host, locale, tenant_id, site_id),
            replay_safe=True,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        scope = "title_filtered" if _clean(query.title) else "full"
        identity = _board_identity(board)
        if identity is None:
            return _result_failure(
                board,
                scope,
                "PROVIDER_VARIANT_UNSUPPORTED",
                "invalid TalentBrew board identity",
            )
        host, locale, tenant_id, site_id = identity
        title = _clean(query.title)
        location = _clean(query.location)
        if (
            (title is not None and (len(title) > 200 or _has_controls(title)))
            or (location is not None and (len(location) > 300 or _has_controls(location)))
        ):
            return _result_failure(
                board,
                scope,
                "PROVIDER_VARIANT_UNSUPPORTED",
                "query exceeds bounded public search contract",
            )
        candidates: list[JobCandidate] = []
        seen_ids: set[str] = set()
        expected_total: int | None = None
        expected_pages: int | None = None
        expected_page_size: int | None = None
        pages_fetched = 0
        records_seen = 0
        inventory_complete = False
        stopped_on_exact_title = False
        failure_reason: str | None = None
        retryable = False
        stop_reason = "not_started"
        response_source: str | None = None

        for page_number in range(1, _MAX_PAGES + 1):
            request_url = _search_url(board.url, title, location, tenant_id, page_number)
            try:
                response = fetcher.fetch(request_url)
            except (FetchError, OSError, TimeoutError) as error:
                failure_reason = _fetch_reason(error)
                retryable = reason_spec(failure_reason).retryable
                stop_reason = "fetch_failed"
                break

            final_url = response.final_url or response.url
            if not _same_search_response(final_url, request_url, host, locale):
                failure_reason = "PROVIDER_VARIANT_UNSUPPORTED"
                stop_reason = "unsafe_response_url"
                break
            response_source = response_source or response.source
            parsed_page = _inventory_page(
                response.html,
                host=host,
                locale=locale,
                tenant_id=tenant_id,
                requested_page=page_number,
            )
            if isinstance(parsed_page, str):
                failure_reason = "INVALID_STRUCTURED_DATA"
                stop_reason = parsed_page
                break

            if expected_total is None:
                expected_total = parsed_page.total
                expected_pages = parsed_page.total_pages
                expected_page_size = parsed_page.records_per_page
            elif (
                parsed_page.total != expected_total
                or parsed_page.total_pages != expected_pages
                or parsed_page.records_per_page != expected_page_size
            ):
                failure_reason = "INVALID_STRUCTURED_DATA"
                stop_reason = "contradictory_pagination_metadata"
                break

            pages_fetched += 1
            for card in parsed_page.cards:
                if card.job_id in seen_ids:
                    failure_reason = "INVALID_STRUCTURED_DATA"
                    stop_reason = "duplicate_job_id"
                    break
                seen_ids.add(card.job_id)
                candidates.append(
                    JobCandidate(
                        title=card.title,
                        url=card.url,
                        provider=self.name,
                        location=card.location,
                        raw={"job_id": card.job_id, "tenant_id": tenant_id},
                    )
                )
            if failure_reason is not None:
                break
            records_seen += len(parsed_page.cards)
            if records_seen > _MAX_ROWS:
                failure_reason = "INVALID_STRUCTURED_DATA"
                stop_reason = "row_cap_exceeded"
                break
            if title and any(_same_title(item.title, title) for item in candidates):
                stopped_on_exact_title = page_number < parsed_page.total_pages
                if stopped_on_exact_title:
                    stop_reason = "exact_title_found"
                    break
            if page_number == parsed_page.total_pages or parsed_page.total == 0:
                inventory_complete = records_seen == parsed_page.total
                stop_reason = "complete" if inventory_complete else "count_mismatch"
                if not inventory_complete:
                    failure_reason = "INVALID_STRUCTURED_DATA"
                break
        else:
            failure_reason = "FETCH_BUDGET_EXHAUSTED"
            retryable = True
            stop_reason = "page_cap_reached"

        if (
            failure_reason is None
            and not inventory_complete
            and not stopped_on_exact_title
        ):
            failure_reason = "FETCH_BUDGET_EXHAUSTED"
            retryable = True
            stop_reason = "page_cap_reached"

        if failure_reason is not None:
            reason_code = failure_reason
        elif not candidates and inventory_complete:
            reason_code = "EMPTY_PROVIDER_RESPONSE"
        else:
            reason_code = None

        trace = {
            "adapter": self.name,
            "variant": "ssr_search_jobs",
            "board_url": board.url,
            "host": host,
            "locale": locale,
            "tenant_id": tenant_id,
            "site_id": site_id,
            "response_source": response_source,
            "page_count": pages_fetched,
            "records_seen": records_seen,
            "total": expected_total,
            "total_pages": expected_pages,
            "records_per_page": expected_page_size,
            "candidate_count": len(candidates),
            "stopped_on_exact_title": stopped_on_exact_title,
            "stop_reason": stop_reason,
            "inventory_scope": scope,
            "inventory_complete": inventory_complete,
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
            inventory_complete=inventory_complete,
            trace=trace,
        )


@dataclass(frozen=True)
class _Form:
    action: str
    method: str
    inputs: tuple[tuple[str, str, str], ...]


class _FingerprintParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {name: [] for name in _SEARCH_META}
        self.assets: list[str] = []
        self.forms: list[_Form] = []
        self._form_action: str | None = None
        self._form_method = "get"
        self._form_inputs: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        tag = tag.casefold()
        if tag == "meta":
            name = values.get("name", "").casefold()
            if name in self.meta:
                self.meta[name].append(values.get("content", "").strip())
        for key in ("src", "href", "srcset", "data-src"):
            if values.get(key):
                self.assets.extend(values[key].split())
        if tag == "form":
            self._form_action = values.get("action", "").strip()
            self._form_method = values.get("method", "get").strip().casefold()
            self._form_inputs = []
        elif tag == "input" and self._form_action is not None:
            self._form_inputs.append(
                (
                    values.get("name", ""),
                    values.get("type", "text").casefold(),
                    values.get("value", "").strip(),
                )
            )

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "form" or self._form_action is None:
            return
        self.forms.append(
            _Form(self._form_action, self._form_method, tuple(self._form_inputs))
        )
        self._form_action = None
        self._form_inputs = []


@dataclass(frozen=True)
class _Card:
    job_id: str
    title: str
    location: str | None
    url: str


class _InventoryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: list[dict[str, str]] = []
        self.cards: list[tuple[list[tuple[str, str]], list[str], list[str]]] = []
        self._card_depth = 0
        self._links: list[tuple[str, str]] = []
        self._titles: list[str] = []
        self._locations: list[str] = []
        self._capture: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        tag = tag.casefold()
        if tag == "section" and values.get("id") == "search-results":
            self.metadata.append(values)
        if tag == "li" and "section29__search-results-li" in classes:
            if self._card_depth:
                self._card_depth += 1
                return
            self._card_depth = 1
            self._links = []
            self._titles = []
            self._locations = []
            return
        if not self._card_depth:
            return
        self._card_depth += 1
        if tag == "a" and "section29__search-results-link" in classes:
            self._links.append((values.get("href", ""), values.get("data-job-id", "")))
        if tag == "h2" and "section29__search-results-job-title" in classes:
            self._capture = "title"
            self._parts = []
        elif tag == "span" and "section29__result-location" in classes:
            self._capture = "location"
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self._capture == "title" and tag == "h2":
            self._titles.append(_clean("".join(self._parts)) or "")
            self._capture = None
        elif self._capture == "location" and tag == "span":
            self._locations.append(_clean("".join(self._parts)) or "")
            self._capture = None
        if not self._card_depth:
            return
        self._card_depth -= 1
        if self._card_depth == 0:
            self.cards.append((self._links, self._titles, self._locations))


@dataclass(frozen=True)
class _InventoryPage:
    total: int
    total_pages: int
    current_page: int
    records_per_page: int
    cards: tuple[_Card, ...]


def _fingerprint(html: str, page_url: str) -> tuple[str, str, str] | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    page = _safe_public_https_url(page_url)
    if page is None:
        return None
    parser = _FingerprintParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    values = {name: _single(parser.meta[name]) for name in _SEARCH_META}
    if any(values[name] is None for name in _SEARCH_META):
        return None
    tenant_id = values["site-tenant-id"] or ""
    site_id = values["site-id"] or ""
    locale = (values["site-current-language"] or "").casefold()
    if (
        not _ID.fullmatch(tenant_id)
        or not _ID.fullmatch(site_id)
        or values["site-organization-id"] != tenant_id
        or values["gtm_tenantid"] != tenant_id
        or values["gtm_companysiteid"] != site_id
        or (values["site-url-modified-language-code"] or "").casefold() != locale
        or not _LOCALE.fullmatch(locale)
    ):
        return None
    expected_path = f"/{locale}/search-jobs"
    matching_forms = []
    for form in parser.forms:
        action = _safe_public_https_url(urljoin(page_url, form.action))
        if action is None or action.path != expected_path:
            continue
        matching_forms.append(form)
        inputs = {name: (kind, value) for name, kind, value in form.inputs}
        if (
            form.method != "get"
            or action.hostname != page.hostname
            or action.query
            or action.fragment
            or "k" not in inputs
            or "l" not in inputs
            or inputs.get("orgIds") != ("hidden", tenant_id)
        ):
            return None
    if not matching_forms or not any(
        _is_tenant_asset(asset, tenant_id) for asset in parser.assets
    ):
        return None
    return tenant_id, site_id, locale


def _inventory_page(
    html: str,
    *,
    host: str,
    locale: str,
    tenant_id: str,
    requested_page: int,
) -> _InventoryPage | str:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return "invalid_html_size"
    parser = _InventoryParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return "malformed_html"
    if len(parser.metadata) != 1:
        return "missing_or_duplicate_inventory_metadata"
    meta = parser.metadata[0]
    numbers = []
    for key in (
        "data-total-job-results",
        "data-total-pages",
        "data-current-page",
        "data-records-per-page",
    ):
        raw = meta.get(key, "")
        if not raw.isdigit():
            return "invalid_pagination_metadata"
        numbers.append(int(raw))
    total, total_pages, current_page, records_per_page = numbers
    calculated_pages = math.ceil(total / records_per_page) if records_per_page else -1
    if (
        not 1 <= records_per_page <= _MAX_RECORDS_PER_PAGE
        or total > 1_000_000
        or total_pages != calculated_pages
        or current_page != requested_page
        or (total_pages == 0 and current_page != 1)
        or (total_pages > 0 and current_page > total_pages)
    ):
        return "inconsistent_pagination_metadata"
    expected_rows = 0
    if total:
        expected_rows = min(records_per_page, total - (current_page - 1) * records_per_page)
    if expected_rows < 0 or len(parser.cards) != expected_rows:
        return "inconsistent_page_row_count"

    cards: list[_Card] = []
    for links, titles, locations in parser.cards:
        if len(links) != 1 or len(titles) != 1 or len(locations) > 1:
            return "invalid_typed_job_card"
        href, job_id = links[0]
        title = titles[0]
        if not title or not _ID.fullmatch(job_id):
            return "invalid_typed_job_card"
        detail = _safe_public_https_url(urljoin(f"https://{_url_host(host)}/", href))
        match = _DETAIL_PATH.fullmatch(detail.path) if detail is not None else None
        if (
            detail is None
            or detail.hostname != host
            or detail.query
            or detail.fragment
            or match is None
            or match.group("locale") != locale
            or match.group("tenant") != tenant_id
            or match.group("job_id") != job_id
        ):
            return "cross_tenant_or_invalid_job_card"
        cards.append(
            _Card(job_id, title, locations[0] or None if locations else None, detail.geturl())
        )
    return _InventoryPage(total, total_pages, current_page, records_per_page, tuple(cards))


def _single(values: list[str]) -> str | None:
    unique = list(dict.fromkeys(values))
    return unique[0] if len(unique) == 1 and unique[0] else None


def _is_tenant_asset(raw_url: str, tenant_id: str) -> bool:
    try:
        parsed = urlparse(raw_url if not raw_url.startswith("//") else "https:" + raw_url)
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == "tbcdn.talentbrew.com"
        and parsed.path.startswith(f"/company/{tenant_id}/")
    )


def _safe_public_https_url(url: str):
    try:
        parsed = urlparse(url)
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


def _identifier(host: str, locale: str, tenant_id: str, site_id: str) -> str:
    return json.dumps(
        {
            "host": host,
            "locale": locale,
            "site_id": site_id,
            "tenant_id": tenant_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _board_identity(board: JobBoard) -> tuple[str, str, str, str] | None:
    if board.provider != "talentbrew" or not board.replay_safe or not board.identifier:
        return None
    parsed = _safe_public_https_url(board.url)
    try:
        value = json.loads(board.identifier)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if parsed is None or not isinstance(value, dict) or set(value) != {
        "host", "locale", "site_id", "tenant_id"
    }:
        return None
    host = (parsed.hostname or "").casefold()
    locale = value.get("locale")
    tenant_id = value.get("tenant_id")
    site_id = value.get("site_id")
    if (
        value.get("host") != host
        or not isinstance(locale, str)
        or not _LOCALE.fullmatch(locale)
        or not isinstance(tenant_id, str)
        or not _ID.fullmatch(tenant_id)
        or not isinstance(site_id, str)
        or not _ID.fullmatch(site_id)
        or board.url != f"https://{_url_host(host)}/{locale}/search-jobs"
        or board.identifier != _identifier(host, locale, tenant_id, site_id)
    ):
        return None
    return host, locale, tenant_id, site_id


def _search_url(
    board_url: str,
    title: str | None,
    location: str | None,
    tenant_id: str,
    page: int,
) -> str:
    return f"{board_url}?" + urlencode(
        (("k", title or ""), ("l", location or ""), ("orgIds", tenant_id), ("p", page))
    )


def _same_search_response(url: str, request_url: str, host: str, locale: str) -> bool:
    actual = _safe_public_https_url(url)
    expected = urlparse(request_url)
    return bool(
        actual is not None
        and (actual.hostname or "").casefold() == host
        and actual.path == f"/{locale}/search-jobs"
        and parse_qsl(actual.query, keep_blank_values=True)
        == parse_qsl(expected.query, keep_blank_values=True)
        and not actual.fragment
    )


def _clean(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _same_title(left: str, right: str) -> bool:
    return (_clean(left) or "").casefold() == (_clean(right) or "").casefold()


def _has_controls(value: str) -> bool:
    return any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)


def _fetch_reason(error: Exception) -> str:
    typed = getattr(error, "reason_code", None)
    if isinstance(typed, str) and typed:
        return typed
    classified = classify_fetch_error(str(error))
    return "PROVIDER_FETCH_FAILED" if classified == "FETCH_FAILED" else classified


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _result_failure(
    board: JobBoard,
    scope: str,
    reason_code: str,
    stop_reason: str,
) -> AdapterResult:
    return AdapterResult(
        provider="talentbrew",
        board=board,
        reason_code=reason_code,
        retryable=reason_spec(reason_code).retryable,
        inventory_scope=scope,
        inventory_complete=False,
        trace={
            "adapter": "talentbrew",
            "variant": "ssr_search_jobs",
            "stop_reason": stop_reason,
            "error_classification": reason_code,
            "inventory_scope": scope,
            "inventory_complete": False,
        },
    )


ADAPTER = TalentBrewAdapter()
