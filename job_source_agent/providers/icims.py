from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re
from typing import Any, Iterator
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from ..identity_continuity import ProviderOpeningRouteEvidence
from ..web import FetchError, Page, safe_normalize_url
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_ICIMS_HOST = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.icims\.com$",
    re.IGNORECASE,
)
_JOB_CONTAINER_KEYS = {
    "jobs",
    "jobpostings",
    "job_postings",
    "postings",
    "results",
    "items",
    "itemlistelement",
}
_ID_FIELDS = ("id", "jobId", "job_id", "jobNumber", "job_number")
_URL_FIELDS = ("url", "jobUrl", "job_url", "detailUrl", "detail_url", "link")
_NESTED_RECORD_KEYS = ("job", "fields", "data", "posting")
_PAGINATION_KEYS = {"next", "nextpage", "next_page", "nexturl", "next_url"}
_PAGINATION_QUERY_KEYS = {"o", "offset", "page", "pr", "start"}
_MAX_PAGES = 5
_JIBE_CONFIG_MARKER = "window.searchConfig"
_JIBE_SIGNATURES = (
    "data-jibe-search-version",
    "app.jibecdn.com/prod/search/",
    '"externalSearch":true',
)
_JIBE_PAGE_SIZE = 100
_ICIMS_SHELL_SCRIPT_SRC = re.compile(
    r"""\bicimsFrame\.src\s*=\s*(['"])(?P<url>.+?)\1""",
    re.IGNORECASE,
)
_ICIMS_SHELL_MARKERS = (
    "icims_content_iframe",
    "icimsFrame.src",
    "iCIMS Content iFrame",
)
_ICIMS_INVENTORY_MARKERS = (
    'id="searchForm"',
    "iCIMS_JobSearchTable",
)
_ICIMS_CUSTOMER_RUNTIME_MARKER = re.compile(
    r"(?<![a-z0-9-])"
    r"(?P<customer>[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.icims\.com)"
    r"/icims2/servlet/icims2(?=[/?#\"'\s<])",
    re.IGNORECASE,
)
_ICIMS_AGGREGATE_EXTRACTION_METHOD = "icims_aggregate_job_card"


class ICIMSAdapter:
    name = "icims"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except (TypeError, ValueError):
            return False
        parts = [part.lower() for part in parsed.path.split("/") if part]
        if not _is_safe_icims_origin(parsed) or not parts or parts[0] != "jobs":
            return False
        if len(parts) == 1:
            return True
        return parts[1].startswith("search") or (
            parts[1].isdigit() and "job" in parts[2:]
        )

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        is_detail = len(parts) >= 2 and parts[1].isdigit()
        is_root = len(parts) == 1
        path = "/jobs/search" if is_root or is_detail else parsed.path.rstrip("/")
        board_url = urlunparse(
            (parsed.scheme or "https", parsed.netloc, path, "", "", "")
        )
        return JobBoard(
            url=board_url,
            provider=self.name,
            identifier=(parsed.hostname or "").lower(),
        )

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        if not _is_safe_web_origin(page_url) or not _is_jibe_page(page.html):
            return None
        parsed = urlparse(page_url)
        parts = [part.casefold() for part in parsed.path.split("/") if part]
        if "jobs" not in parts:
            return None
        return JobBoard(
            url=urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", parsed.query, "")),
            provider=self.name,
            identifier=(parsed.hostname or "").casefold(),
            replay_safe=True,
        )

    def probe_board(self, fetcher, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        if not _is_public_icims_shell_url(page_url):
            return None
        iframe_url = _declared_icims_iframe_url(page.html, page_url)
        if iframe_url is None:
            return None
        try:
            iframe_page = fetcher.fetch(iframe_url)
        except (FetchError, OSError, TimeoutError):
            return None
        iframe_final_url = iframe_page.final_url or iframe_page.url
        expected_host = (urlparse(page_url).hostname or "").casefold()
        if not _is_same_icims_origin(iframe_final_url, expected_host):
            return None
        search_url = _declared_icims_search_url(
            iframe_page.html,
            iframe_final_url,
            expected_host,
        )
        if search_url is None:
            return None
        parsed = urlparse(search_url)
        return JobBoard(
            url=urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", "")),
            provider=self.name,
            identifier=expected_host,
            replay_safe=True,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        inventory_scope = "title_filtered" if query.title else "full"
        if board.identifier and not _ICIMS_HOST.fullmatch(board.identifier):
            return self._list_jibe_jobs(fetcher, board, query)
        if (
            not board.identifier
            or not _ICIMS_HOST.fullmatch(board.identifier)
            or not _is_icims_search_url(board.url, board.identifier)
        ):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_scope=inventory_scope,
                inventory_complete=False,
                trace={
                    "adapter": self.name,
                    "error": "missing iCIMS careers board identifier",
                    "inventory_scope": inventory_scope,
                    "inventory_complete": False,
                },
            )

        candidates: list[JobCandidate] = []
        structured_script_count = 0
        page_urls: list[str] = []
        page_errors: list[dict[str, str]] = []
        rejected_pagination_urls: list[str] = []
        initial_url = _hosted_search_url(board.url, query)
        pending = [initial_url]
        queued = {initial_url}
        response_source: str | None = None
        html_link_count = 0

        while pending and len(page_urls) < _MAX_PAGES:
            requested_url = pending.pop(0)
            try:
                page = fetcher.fetch(requested_url)
            except (FetchError, OSError, TimeoutError) as error:
                page_errors.append({"url": requested_url, "error": str(error)})
                continue

            final_url = page.final_url or page.url
            if not _is_icims_search_url(final_url, board.identifier):
                rejected_pagination_urls.append(final_url)
                continue

            queued.add(final_url)
            page_urls.append(final_url)
            if response_source is None:
                response_source = page.source
            scripts = _ScriptParser()
            try:
                scripts.feed(page.html)
                scripts.close()
            except (TypeError, ValueError) as error:
                page_errors.append({"url": final_url, "error": str(error)})
                continue

            html_link_count += len(scripts.job_links)
            for raw_url, raw_title, raw_location in scripts.job_links:
                candidate = _candidate_from_html_link(
                    raw_url,
                    raw_title,
                    raw_location,
                    final_url,
                    board,
                )
                if candidate is not None:
                    candidates.append(candidate)
            candidates.extend(
                self._aggregate_route_candidates(
                    scripts.job_cards,
                    page.html,
                    final_url,
                    board,
                )
            )

            discovered_urls = list(scripts.pagination_hrefs)
            for script_type, content in scripts.scripts:
                payload = _decode_script_json(content)
                if payload is None:
                    continue
                structured_script_count += 1
                is_json_ld = script_type == "application/ld+json"
                for record in _walk_job_records(payload, json_ld=is_json_ld):
                    candidate = _candidate_from_record(record, board, json_ld=is_json_ld)
                    if candidate is not None:
                        candidates.append(candidate)
                discovered_urls.extend(_walk_pagination_values(payload))

            for raw_url in discovered_urls:
                normalized = safe_normalize_url(raw_url, final_url)
                if not normalized or not _is_icims_search_url(normalized, board.identifier):
                    rejected_pagination_urls.append(str(raw_url))
                    continue
                if normalized not in queued:
                    queued.add(normalized)
                    pending.append(normalized)

        candidates = _reject_duplicate_route_cards(candidates)
        candidates = _dedupe_candidates(candidates)
        if not page_urls and rejected_pagination_urls:
            reason_code = "PROVIDER_VARIANT_UNSUPPORTED"
        elif not candidates and page_errors:
            reason_code = "PROVIDER_FETCH_FAILED"
        else:
            reason_code = None if candidates else "EMPTY_PROVIDER_RESPONSE"
        inventory_complete = bool(
            not pending
            and not page_errors
            and reason_code not in {"PROVIDER_VARIANT_UNSUPPORTED", "PROVIDER_FETCH_FAILED"}
        )
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=reason_code,
            retryable=reason_code == "PROVIDER_FETCH_FAILED",
            inventory_scope=inventory_scope,
            inventory_complete=inventory_complete,
            trace={
                "adapter": self.name,
                "board_urls": page_urls or [board.url],
                "response_source": response_source,
                "structured_script_count": structured_script_count,
                "html_link_count": html_link_count,
                "aggregate_route_count": sum(
                    candidate.route_evidence is not None for candidate in candidates
                ),
                "candidate_count": len(candidates),
                "page_count": len(page_urls),
                "page_errors": page_errors,
                "rejected_pagination_urls": list(dict.fromkeys(rejected_pagination_urls)),
                "inventory_scope": inventory_scope,
                "inventory_complete": inventory_complete,
            },
        )

    def _aggregate_route_candidates(
        self,
        cards: list[_ICIMSJobCard],
        html: str,
        source_response_url: str,
        board: JobBoard,
    ) -> list[JobCandidate]:
        source_board = self.identify_board(board.url)
        canonical_response = _canonical_icims_search_response(
            source_response_url,
            board.identifier,
        )
        customer_identity = _unique_icims_customer_identity(html)
        if (
            source_board is None
            or source_board.identifier != board.identifier
            or canonical_response is None
            or customer_identity is None
        ):
            return []

        candidates: list[JobCandidate] = []
        for card in cards:
            candidate = self._aggregate_route_candidate(
                card,
                source_board,
                canonical_response,
                customer_identity,
            )
            if candidate is not None:
                candidates.append(candidate)
        return _reject_duplicate_route_cards(candidates)

    def _aggregate_route_candidate(
        self,
        card: _ICIMSJobCard,
        source_board: JobBoard,
        source_response_url: str,
        customer_identity: str,
    ) -> JobCandidate | None:
        if not card.location or len(card.links) != 1:
            return None
        raw_url, raw_title = card.links[0]
        detail = _icims_child_detail_route(
            raw_url,
            source_response_url,
            source_board.identifier,
        )
        if detail is None:
            return None
        canonical_url, opening_id, hub = detail
        target_board = self.identify_board(canonical_url)
        if (
            target_board is None
            or target_board.identifier == source_board.identifier
        ):
            return None
        title = _card_title_for_opening(raw_title, opening_id)
        if not title:
            return None
        return JobCandidate(
            title=title,
            url=canonical_url,
            provider=self.name,
            location=card.location,
            raw={
                "id": opening_id,
                "source": "aggregate_html_job_card",
            },
            route_evidence=ProviderOpeningRouteEvidence(
                provider=self.name,
                source_tenant=source_board.identifier,
                source_canonical_board_url=source_board.url,
                target_tenant=target_board.identifier,
                target_canonical_board_url=target_board.url,
                canonical_opening_url=canonical_url,
                opening_id=opening_id,
                source_response_url=source_response_url,
                source_customer_identity=customer_identity,
                target_customer_identity=None,
                route_identity=f"hub:{hub}",
                detail_evidence_url=None,
                extraction_method=_ICIMS_AGGREGATE_EXTRACTION_METHOD,
                detail_verified=False,
            ),
        )

    def _list_jibe_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        inventory_scope = "title_filtered" if query.title else "full"
        if not board.identifier or not _is_same_safe_origin(board.url, board.identifier):
            return _unsupported_jibe_result(
                board,
                "invalid customer-owned board origin",
                inventory_scope,
            )

        try:
            page = fetcher.fetch(board.url)
        except (FetchError, OSError, TimeoutError) as error:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                inventory_scope=inventory_scope,
                inventory_complete=False,
                trace={
                    "adapter": self.name,
                    "variant": "jibe",
                    "error": str(error),
                    "inventory_scope": inventory_scope,
                    "inventory_complete": False,
                },
            )
        final_url = page.final_url or page.url
        if not _is_same_safe_origin(final_url, board.identifier) or not _is_jibe_page(page.html):
            return _unsupported_jibe_result(
                board,
                "board response lacked same-origin Jibe evidence",
                inventory_scope,
            )

        config = _jibe_search_config(page.html)
        if config is None:
            return _unsupported_jibe_result(
                board,
                "missing valid window.searchConfig",
                inventory_scope,
            )
        search_override = config.get("searchOverride")
        params = _safe_jibe_query(search_override)
        if query.title:
            params["keywords"] = query.title
        params.update({"limit": str(_JIBE_PAGE_SIZE), "page": "1"})

        parsed = urlparse(final_url)
        api_origin = urlunparse((parsed.scheme, parsed.netloc, "/api/jobs", "", "", ""))
        candidates: list[JobCandidate] = []
        api_urls: list[str] = []
        errors: list[dict[str, str]] = []
        total_count: int | None = None
        inventory_complete = False
        records_seen = 0

        for page_number in range(1, _MAX_PAGES + 1):
            params["page"] = str(page_number)
            api_url = f"{api_origin}?{urlencode(params)}"
            api_urls.append(api_url)
            try:
                response = fetcher.fetch(api_url)
            except (FetchError, OSError, TimeoutError) as error:
                errors.append({"url": api_url, "error": str(error)})
                break
            response_url = response.final_url or response.url
            if not _is_same_safe_origin(response_url, board.identifier, expected_path="/api/jobs"):
                return _unsupported_jibe_result(
                    board,
                    "Jibe API redirected outside the board origin",
                    inventory_scope,
                )
            try:
                payload = json.loads(response.html)
            except (json.JSONDecodeError, TypeError) as error:
                errors.append({"url": api_url, "error": str(error)})
                break
            if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                return _unsupported_jibe_result(
                    board,
                    "Jibe API returned an unsupported schema",
                    inventory_scope,
                )
            jobs = payload["jobs"]
            records_seen += len(jobs)
            candidates.extend(_jibe_candidates(jobs, final_url, board.identifier))
            page_total = _nonnegative_int(payload.get("totalCount") or payload.get("count"))
            if page_total is not None:
                total_count = max(total_count or 0, page_total)
            if not jobs:
                inventory_complete = True
                break
            if total_count is not None and records_seen >= total_count:
                inventory_complete = True
                break
            if total_count is None:
                break

        candidates = _dedupe_candidates(candidates)
        reason_code = None if candidates else ("PROVIDER_FETCH_FAILED" if errors else "EMPTY_PROVIDER_RESPONSE")
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=reason_code,
            retryable=reason_code == "PROVIDER_FETCH_FAILED",
            inventory_scope=inventory_scope,
            inventory_complete=inventory_complete,
            trace={
                "adapter": self.name,
                "variant": "jibe",
                "board_urls": [final_url],
                "api_urls": api_urls,
                "candidate_count": len(candidates),
                "total_count": total_count,
                "search_override_keys": sorted(params.keys() - {"keywords", "limit", "page"}),
                "errors": errors,
                "inventory_scope": inventory_scope,
                "inventory_complete": inventory_complete,
            },
        )


@dataclass(frozen=True)
class _ICIMSJobCard:
    links: tuple[tuple[str, str], ...]
    location: str | None


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[tuple[str, str]] = []
        self.pagination_hrefs: list[str] = []
        self.job_links: list[tuple[str, str, str | None]] = []
        self.job_cards: list[_ICIMSJobCard] = []
        self._script_type: str | None = None
        self._content: list[str] = []
        self._job_href: str | None = None
        self._job_title: str = ""
        self._job_text: list[str] = []
        self._card_depth = 0
        self._card_fields: list[tuple[bool, str]] = []
        self._card_links: list[tuple[str, str]] = []
        self._field_label_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        attributes = {key.lower(): (value or "") for key, value in attrs}
        classes = attributes.get("class", "").casefold().split()
        if self._card_depth == 0 and "icims_jobcarditem" in classes:
            self._card_depth = 1
            self._card_fields = []
            self._card_links = []
        elif self._card_depth and tag_name not in _HTML_VOID_TAGS:
            self._card_depth += 1
        if self._card_depth:
            if self._field_label_depth and tag_name not in _HTML_VOID_TAGS:
                self._field_label_depth += 1
            elif (
                "field-label" in classes
                or "icims_jobheaderfield" in classes
            ):
                self._field_label_depth = 1
        if tag_name == "a":
            href = attributes.get("href", "")
            if href and "/jobs/" in href.casefold():
                self._job_href = href
                self._job_title = attributes.get("title", "")
                self._job_text = []
            rel = attributes.get("rel", "").casefold().split()
            if href and (
                "next" in rel
                or any("paging" in class_name or "pagination" in class_name for class_name in classes)
                or _has_pagination_query(href)
            ):
                self.pagination_hrefs.append(href)
            return
        if tag_name != "script":
            return
        script_type = attributes.get("type", "").lower().split(";", 1)[0].strip()
        if script_type in {"application/ld+json", "application/json"}:
            self._script_type = script_type
            self._content = []

    def handle_data(self, data: str) -> None:
        if self._card_depth:
            text = " ".join(data.split())
            if text:
                self._card_fields.append((bool(self._field_label_depth), text))
        if self._job_href is not None:
            self._job_text.append(data)
        if self._script_type is not None:
            self._content.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name == "a" and self._job_href is not None:
            title = self._job_title or " ".join("".join(self._job_text).split())
            if self._card_depth:
                self._card_links.append((self._job_href, title))
            else:
                self.job_links.append((self._job_href, title, None))
            self._job_href = None
            self._job_title = ""
            self._job_text = []
        if tag_name == "script" and self._script_type is not None:
            self.scripts.append((self._script_type, "".join(self._content)))
            self._script_type = None
            self._content = []
        if self._field_label_depth:
            self._field_label_depth -= 1
        if self._card_depth:
            self._card_depth -= 1
            if self._card_depth == 0:
                location = _card_location(self._card_fields)
                self.job_cards.append(
                    _ICIMSJobCard(
                        links=tuple(self._card_links),
                        location=location,
                    )
                )
                self.job_links.extend(
                    (href, title, location)
                    for href, title in self._card_links
                )
                self._card_fields = []
                self._card_links = []
                self._field_label_depth = 0


_HTML_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_CARD_LOCATION_LABELS = {
    "campus location",
    "location",
    "job location",
    "job locations",
}


def _card_location(fields: list[tuple[bool, str]]) -> str | None:
    for index, (is_label, value) in enumerate(fields[:-1]):
        if (
            not is_label
            or value.casefold().rstrip(":") not in _CARD_LOCATION_LABELS
        ):
            continue
        for candidate_is_label, candidate in fields[index + 1 :]:
            if candidate_is_label:
                break
            text = candidate.strip()
            if not text:
                continue
            return _normalize_icims_card_location(text)
    return None


def _normalize_icims_card_location(value: str) -> str:
    match = re.fullmatch(
        r"US-([A-Z]{2})-(.*)",
        value.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return value.strip()
    state = match.group(1).upper()
    city = " ".join(part for part in match.group(2).split("-") if part).strip()
    return f"{city + ', ' if city else ''}{state}, United States"


def _decode_script_json(content: str) -> Any | None:
    text = content.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    first = min((index for index in (text.find("{"), text.find("[")) if index >= 0), default=-1)
    if first < 0:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(text[first:])
    except json.JSONDecodeError:
        return None
    return payload


def _walk_job_records(
    value: Any,
    *,
    json_ld: bool,
    in_job_container: bool = False,
) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        record_type = value.get("@type")
        types = {str(item).casefold() for item in record_type} if isinstance(record_type, list) else {str(record_type).casefold()}
        is_job_posting = "jobposting" in types
        if (json_ld and is_job_posting) or (not json_ld and in_job_container):
            yield value
        for key, child in value.items():
            child_container = in_job_container or str(key).casefold() in _JOB_CONTAINER_KEYS
            yield from _walk_job_records(
                child,
                json_ld=json_ld,
                in_job_container=child_container,
            )
    elif isinstance(value, list):
        for item in value:
            yield from _walk_job_records(
                item,
                json_ld=json_ld,
                in_job_container=in_job_container,
            )


def _walk_pagination_values(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in _PAGINATION_KEYS:
                if isinstance(child, str):
                    yield child
                elif isinstance(child, dict):
                    for field in ("href", "url"):
                        target = child.get(field)
                        if isinstance(target, str):
                            yield target
            yield from _walk_pagination_values(child)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_pagination_values(item)


def _hosted_search_url(board_url: str, query: JobQuery) -> str:
    if not query.title:
        return board_url
    parsed = urlparse(board_url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params.update({
        "ss": "1",
        "searchKeyword": query.title,
        "in_iframe": "1",
    })
    return urlunparse(parsed._replace(query=urlencode(params), fragment=""))


def _canonical_icims_search_response(
    url: str,
    expected_host: str | None,
) -> str | None:
    if not _is_icims_search_url(url, expected_host):
        return None
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return None
    return urlunparse(parsed._replace(fragment=""))


def _unique_icims_customer_identity(html: str) -> str | None:
    if not isinstance(html, str):
        return None
    matches = [
        match.group("customer").casefold()
        for match in _ICIMS_CUSTOMER_RUNTIME_MARKER.finditer(html)
    ]
    if len(matches) != 1 or not _ICIMS_HOST.fullmatch(matches[0]):
        return None
    return matches[0]


def _icims_child_detail_route(
    raw_url: str,
    page_url: str,
    source_host: str,
) -> tuple[str, str, int] | None:
    try:
        raw_parsed = urlparse(raw_url)
    except (TypeError, ValueError):
        return None
    if raw_parsed.fragment:
        return None
    detail_url = safe_normalize_url(raw_url, page_url)
    if not detail_url:
        return None
    try:
        parsed = urlparse(detail_url)
    except (TypeError, ValueError):
        return None
    if (
        not _is_safe_icims_origin(parsed)
        or (parsed.hostname or "").casefold() == source_host.casefold()
        or parsed.fragment
    ):
        return None
    path_match = re.fullmatch(
        r"/jobs/(?P<opening_id>[1-9]\d*)/(?P<slug>[^/]+)/job/?",
        parsed.path,
        re.IGNORECASE,
    )
    if path_match is None:
        return None
    raw_slug = path_match.group("slug")
    if re.search(r"%(?![0-9a-f]{2})", raw_slug, re.IGNORECASE):
        return None
    slug = unquote(raw_slug)
    if not slug or slug in {".", ".."} or "/" in slug or "\\" in slug:
        return None

    try:
        query_pairs = parse_qsl(
            raw_parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError:
        return None
    if any(key.casefold() not in {"hub", "in_iframe"} for key, _ in query_pairs):
        return None
    lowered_pairs = [(key.casefold(), value) for key, value in query_pairs]
    hub_values = [value for key, value in lowered_pairs if key == "hub"]
    iframe_values = [value for key, value in lowered_pairs if key == "in_iframe"]
    if (
        len(hub_values) != 1
        or not hub_values[0].isdigit()
        or int(hub_values[0]) <= 0
        or len(iframe_values) > 1
        or (iframe_values and iframe_values[0] != "1")
    ):
        return None
    canonical_url = urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", "")
    )
    return canonical_url, path_match.group("opening_id"), int(hub_values[0])


def _card_title_for_opening(raw_title: str, opening_id: str) -> str:
    title = (raw_title or "").strip()
    prefixed = re.fullmatch(r"(\d+)\s+-\s+(.+)", title)
    if prefixed is not None:
        if prefixed.group(1) != opening_id:
            return ""
        title = prefixed.group(2).strip()
    return title


def _reject_duplicate_route_cards(
    candidates: list[JobCandidate],
) -> list[JobCandidate]:
    url_counts: dict[str, int] = {}
    id_counts: dict[str, int] = {}
    for candidate in candidates:
        evidence = candidate.route_evidence
        if evidence is None:
            continue
        url_counts[candidate.url] = url_counts.get(candidate.url, 0) + 1
        id_counts[evidence.opening_id] = id_counts.get(evidence.opening_id, 0) + 1
    return [
        candidate
        for candidate in candidates
        if candidate.route_evidence is None
        or (
            url_counts[candidate.url] == 1
            and id_counts[candidate.route_evidence.opening_id] == 1
        )
    ]


def _candidate_from_html_link(
    raw_url: str,
    raw_title: str,
    raw_location: str | None,
    page_url: str,
    board: JobBoard,
) -> JobCandidate | None:
    detail_url = safe_normalize_url(raw_url, page_url)
    if not detail_url or not _is_icims_detail_url(detail_url, board.identifier):
        return None
    title = re.sub(r"^\s*\d+\s+-\s+", "", raw_title or "").strip()
    if not title:
        return None
    parsed = urlparse(detail_url)
    canonical_url = urlunparse(parsed._replace(query="", fragment=""))
    parts = [part for part in parsed.path.split("/") if part]
    return JobCandidate(
        title=title,
        url=canonical_url,
        provider="icims",
        location=(raw_location or "").strip() or None,
        raw={"id": parts[1], "source": "html_link"},
    )


def _candidate_from_record(
    record: dict[str, Any],
    board: JobBoard,
    *,
    json_ld: bool,
) -> JobCandidate | None:
    title = _first_text(record, ("title", "name", "jobTitle", "job_title"))
    if not title:
        return None
    raw_url = _first_text(record, _URL_FIELDS)
    job_id = _first_text(record, _ID_FIELDS)
    if raw_url:
        detail_url = safe_normalize_url(raw_url, board.url)
    elif job_id:
        detail_url = safe_normalize_url(
            f"/jobs/{job_id}/{_slugify(title)}/job",
            board.url,
        )
    else:
        return None
    if not detail_url or not _is_icims_detail_url(detail_url, board.identifier):
        return None

    raw = dict(record)
    if json_ld:
        raw = {key: record.get(key) for key in ("@type", "identifier", "datePosted") if key in record}
    return JobCandidate(
        title=title,
        url=detail_url,
        provider="icims",
        location=_location(record),
        raw=raw,
    )


def _first_text(record: dict[str, Any], fields: tuple[str, ...]) -> str:
    for source in _record_sources(record):
        for field in fields:
            value = source.get(field)
            if isinstance(value, dict) and "value" in value:
                value = value["value"]
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
    return ""


def _record_sources(record: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield record
    for key in _NESTED_RECORD_KEYS:
        nested = record.get(key)
        if isinstance(nested, dict):
            yield nested


def _location(record: dict[str, Any]) -> str | None:
    for source in _record_sources(record):
        value = source.get("location") or source.get("jobLocation") or source.get("locations")
        if isinstance(value, dict) and "value" in value:
            value = value["value"]
        if isinstance(value, list):
            locations = [location for item in value if (location := _location_value(item))]
            if locations:
                return "; ".join(dict.fromkeys(locations))
        else:
            location = _location_value(value)
            if location:
                return location
    return None


def _location_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None
    address = value.get("address") if isinstance(value.get("address"), dict) else value
    direct = _first_text(address, ("name", "fullLocation", "formattedAddress"))
    if direct:
        return direct
    parts = [
        _first_text(address, (field,))
        for field in ("addressLocality", "addressRegion", "addressCountry", "city", "state", "country")
    ]
    return ", ".join(dict.fromkeys(part for part in parts if part)) or None


def _is_icims_detail_url(url: str, expected_host: str | None) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    parts = [part.lower() for part in parsed.path.split("/") if part]
    return (
        _is_safe_icims_origin(parsed, expected_host)
        and len(parts) >= 3
        and parts[0] == "jobs"
        and parts[1].isdigit()
        and "job" in parts[2:]
    )


def _is_icims_search_url(url: str, expected_host: str | None) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    parts = [part.casefold() for part in parsed.path.split("/") if part]
    return (
        _is_safe_icims_origin(parsed, expected_host)
        and len(parts) >= 2
        and parts[0] == "jobs"
        and parts[1].startswith("search")
    )


def _is_public_icims_shell_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return False
    if not _is_safe_icims_origin(parsed):
        return False
    parts = [part.casefold() for part in parsed.path.split("/") if part]
    return not parts or parts == ["jobs", "intro"]


def _declared_icims_iframe_url(html: str, page_url: str) -> str | None:
    if not isinstance(html, str) or not all(
        marker.casefold() in html.casefold() for marker in _ICIMS_SHELL_MARKERS
    ):
        return None
    parser = _ICIMSShellParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    raw_urls = list(parser.iframe_urls)
    raw_urls.extend(
        match.group("url").replace("\\/", "/")
        for match in _ICIMS_SHELL_SCRIPT_SRC.finditer(html)
    )
    normalized: list[str] = []
    expected_host = (urlparse(page_url).hostname or "").casefold()
    for raw_url in raw_urls:
        candidate = safe_normalize_url(raw_url, page_url)
        if (
            candidate
            and _is_same_icims_origin(candidate, expected_host)
            and _has_iframe_flag(candidate)
        ):
            normalized.append(candidate)
    unique = list(dict.fromkeys(normalized))
    return unique[0] if len(unique) == 1 else None


def _declared_icims_search_url(
    html: str,
    page_url: str,
    expected_host: str,
) -> str | None:
    if not isinstance(html, str) or not all(
        marker.casefold() in html.casefold() for marker in _ICIMS_INVENTORY_MARKERS
    ):
        return None
    parser = _ICIMSSearchFormParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    normalized: list[str] = []
    for raw_url in parser.search_actions:
        candidate = safe_normalize_url(raw_url, page_url)
        if candidate and _is_icims_search_url(candidate, expected_host):
            normalized.append(candidate)
    unique = list(dict.fromkeys(normalized))
    return unique[0] if len(unique) == 1 else None


def _has_iframe_flag(url: str) -> bool:
    try:
        return any(
            key.casefold() == "in_iframe" and value == "1"
            for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True)
        )
    except (TypeError, ValueError):
        return False


def _is_same_icims_origin(url: str, expected_host: str) -> bool:
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return False
    return _is_safe_icims_origin(parsed, expected_host)


def _has_pagination_query(url: str) -> bool:
    try:
        return any(key.casefold() in _PAGINATION_QUERY_KEYS for key, _ in parse_qsl(urlparse(url).query))
    except ValueError:
        return False


def _is_safe_icims_origin(parsed, expected_host: str | None = None) -> bool:
    try:
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    standard_port = port is None or port == 443
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and standard_port
        and bool(_ICIMS_HOST.fullmatch(host))
        and (expected_host is None or host == expected_host.casefold())
    )


class _ICIMSShellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.iframe_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() != "iframe":
            return
        values = {str(key).casefold(): str(value or "") for key, value in attrs}
        identity = " ".join(
            values.get(key, "") for key in ("id", "name", "title")
        ).casefold()
        source = values.get("src", "").strip()
        if source and "icims" in identity and "iframe" in identity:
            self.iframe_urls.append(source)


class _ICIMSSearchFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.search_actions: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() != "form":
            return
        values = {str(key).casefold(): str(value or "") for key, value in attrs}
        if values.get("id", "").casefold() != "searchform":
            return
        action = values.get("action", "").strip()
        if action:
            self.search_actions.append(action)


def _is_safe_web_origin(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    standard_port = port is None or (parsed.scheme == "https" and port == 443) or (
        parsed.scheme == "http" and port == 80
    )
    return (
        parsed.scheme in {"http", "https"}
        and parsed.username is None
        and parsed.password is None
        and standard_port
        and bool(parsed.hostname)
    )


def _is_same_safe_origin(
    url: str,
    expected_host: str,
    *,
    expected_path: str | None = None,
) -> bool:
    if not _is_safe_web_origin(url):
        return False
    parsed = urlparse(url)
    return (
        (parsed.hostname or "").casefold() == expected_host.casefold()
        and (expected_path is None or parsed.path.rstrip("/") == expected_path.rstrip("/"))
    )


def _is_jibe_page(html: str) -> bool:
    if not isinstance(html, str) or _JIBE_CONFIG_MARKER not in html:
        return False
    return sum(signature in html for signature in _JIBE_SIGNATURES) >= 2


def _jibe_search_config(html: str) -> dict[str, Any] | None:
    marker_index = html.find(_JIBE_CONFIG_MARKER)
    if marker_index < 0:
        return None
    assignment_index = html.find("=", marker_index + len(_JIBE_CONFIG_MARKER))
    if assignment_index < 0:
        return None
    object_index = html.find("{", assignment_index + 1)
    if object_index < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(html[object_index:])
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _safe_jibe_query(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
            continue
        if isinstance(raw, bool):
            safe[key] = str(raw).lower()
        elif isinstance(raw, (str, int, float)) and str(raw).strip():
            safe[key] = str(raw).strip()
    return safe


def _jibe_candidates(
    jobs: list[Any],
    board_url: str,
    expected_host: str,
) -> list[JobCandidate]:
    candidates = []
    for item in jobs:
        record = item.get("data") if isinstance(item, dict) else None
        if not isinstance(record, dict) or str(record.get("ats_code") or "").casefold() != "icims":
            continue
        title = str(record.get("title") or "").strip()
        slug = str(record.get("slug") or record.get("req_id") or "").strip()
        metadata = record.get("meta_data") if isinstance(record.get("meta_data"), dict) else {}
        raw_url = metadata.get("canonical_url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            language = str(record.get("language") or "").strip()
            suffix = f"?lang={language}" if language else ""
            raw_url = f"/jobs/{slug}{suffix}"
        detail_url = safe_normalize_url(raw_url, board_url)
        if (
            not title
            or not slug
            or not detail_url
            or not _is_jibe_detail_url(detail_url, expected_host, slug)
        ):
            continue
        location = str(record.get("full_location") or record.get("location_name") or "").strip() or None
        candidates.append(
            JobCandidate(
                title=title,
                url=detail_url,
                provider="icims",
                location=location,
                raw={"slug": slug, "ats_code": "icims"},
            )
        )
    return candidates


def _is_jibe_detail_url(url: str, expected_host: str, expected_job_id: str) -> bool:
    if not expected_job_id.isdigit() or not _is_same_safe_origin(url, expected_host):
        return False
    try:
        parts = [part for part in urlparse(url).path.split("/") if part]
    except (TypeError, ValueError):
        return False
    return len(parts) >= 2 and parts[-2].casefold() == "jobs" and parts[-1] == expected_job_id


def _nonnegative_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _unsupported_jibe_result(
    board: JobBoard,
    error: str,
    inventory_scope: str,
) -> AdapterResult:
    return AdapterResult(
        provider="icims",
        board=board,
        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
        inventory_scope=inventory_scope,
        inventory_complete=False,
        trace={
            "adapter": "icims",
            "variant": "jibe",
            "error": error,
            "inventory_scope": inventory_scope,
            "inventory_complete": False,
        },
    )


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-") or "opening"


def _dedupe_candidates(candidates: list[JobCandidate]) -> list[JobCandidate]:
    seen: set[str] = set()
    deduped = []
    for candidate in candidates:
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        deduped.append(candidate)
    return deduped


ADAPTER = ICIMSAdapter()
