from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

from ..web import FetchError, Page, safe_normalize_url
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST_SUFFIXES = ("successfactors.com", "successfactors.eu", "sapsf.com", "sapsf.eu")
_CLOUD_SAP_SUFFIX = ".jobs.hr.cloud.sap"
_CLOUD_IDENTIFIER_PREFIX = "cloud:"
_CUSTOM_IDENTIFIER_PREFIX = "custom:"
_CLOUD_PAGE_SIZE = 10
_CLOUD_MAX_PAGES = 5
_LEGACY_MAX_PAGES = 5
_MAX_PAGE_EVIDENCE_CHARS = 2_000_000
_SHARED_LEGACY_HOST = re.compile(
    r"^career\d+\.(?:successfactors|sapsf)\.(?:com|eu)$",
    re.IGNORECASE,
)
_TENANT_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SSO_COMPANY_ID = re.compile(
    r'''["']?ssoCompanyId["']?\s*:\s*["']([^"']+)["']''',
    re.IGNORECASE,
)
_SSO_URL = re.compile(
    r'''["']?ssoUrl["']?\s*:\s*["']([^"']+)["']''',
    re.IGNORECASE,
)
_J2W_INIT = re.compile(r"\bj2w\.init\s*\(\s*(\{.*?\})\s*\)", re.IGNORECASE | re.DOTALL)
_DETAIL_ID_FIELDS = (
    "career_job_req_id",
    "jobReqId",
    "job_req_id",
    "requisitionId",
    "jobRequisitionId",
    "externalCode",
)
_TITLE_FIELDS = ("jobTitle", "title", "job_title", "jobTitleText", "name")
_URL_FIELDS = (
    "jobUrl",
    "job_url",
    "detailUrl",
    "jobDetailUrl",
    "jobPath",
    "externalPath",
    "url",
    "href",
)
_DETAIL_QUERY_KEYS = {"career_job_req_id", "jobid", "jobreqid", "job_req_id"}
_SEARCH_QUERY_KEYS = {"keyword", "q", "search"}


class SuccessFactorsAdapter:
    name = "successfactors"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme.casefold() in {"http", "https"}
            and not parsed.username
            and not parsed.password
            and port in {None, 80, 443}
            and (
                any(host == suffix or host.endswith(f".{suffix}") for suffix in _HOST_SUFFIXES)
                or host.endswith(_CLOUD_SAP_SUFFIX)
            )
        )

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        normalized = safe_normalize_url(url)
        if not normalized:
            return None

        parsed = urlparse(normalized)
        if _is_cloud_sap_host(parsed.hostname or ""):
            query = [
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if key.casefold() == "locale" and value
            ]
            board_url = urlunparse(
                parsed._replace(path="/search/", query=urlencode(query), fragment="")
            )
            return JobBoard(
                url=board_url,
                provider=self.name,
                identifier=f"{_CLOUD_IDENTIFIER_PREFIX}{(parsed.hostname or '').casefold()}",
            )
        query = []
        company = ""
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            key_folded = key.casefold()
            if key_folded in {"company", "companyid", "company_id"} and value and not company:
                company = value
            if key_folded in _DETAIL_QUERY_KEYS or key_folded in _SEARCH_QUERY_KEYS:
                continue
            if key_folded == "career_ns" and value.casefold() == "job_listing":
                continue
            query.append((key, value))

        board_url = urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))
        identifier = company
        if not identifier and not _is_shared_legacy_host(parsed.hostname or ""):
            identifier = (parsed.hostname or "").lower()
        return JobBoard(url=board_url, provider=self.name, identifier=identifier or None)

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        if not _safe_page_evidence_url(page_url):
            return None
        html = page.html
        if not isinstance(html, str) or len(html) > _MAX_PAGE_EVIDENCE_CHARS:
            return None

        identity = _j2w_tenant_identity(html)
        if identity is None:
            return None
        company, sso_url = identity
        board = self.identify_board(sso_url)
        if board is None:
            return None
        if not _recognized_host(page_url):
            custom_url = _custom_board_url(page_url, html)
            if not custom_url:
                return None
            return JobBoard(
                url=custom_url,
                provider=self.name,
                identifier=f"{_CUSTOM_IDENTIFIER_PREFIX}{company}",
            )
        if board.identifier:
            observed_company = _legacy_company(urlparse(board.url).query)
            return board if not observed_company or observed_company == company else None

        parsed = urlparse(board.url)
        if parsed.path not in {"", "/"} or parsed.query:
            return None
        tenant_url = urlunparse(
            parsed._replace(path="/career", query=urlencode({"company": company}))
        )
        return self.identify_board(tenant_url)

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not board.identifier:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "missing SuccessFactors board identifier"},
            )
        if board.identifier.startswith(_CLOUD_IDENTIFIER_PREFIX):
            return self._list_cloud_sap_jobs(fetcher, board, query)
        custom_tenant = _custom_tenant(board)
        search_url = _search_url(board.url, query.title, custom=bool(custom_tenant))
        search_urls = [search_url]
        candidates: list[JobCandidate] = []
        embedded_payload_count = 0
        pagination: dict[str, object] = {}
        malformed_json = False
        inventory_complete = True
        stop_reason: str | None = None
        expected_host = (urlparse(board.url).hostname or "").casefold()
        expected_path = urlparse(search_url).path.rstrip("/") or "/"
        expected_query = _pagination_query(search_url)
        current_url = search_url
        seen_urls = set()
        exact_title = _normalized_title(query.title)

        for _ in range(_LEGACY_MAX_PAGES):
            if current_url in seen_urls:
                inventory_complete = False
                stop_reason = "pagination_cycle"
                break
            seen_urls.add(current_url)
            try:
                page = fetcher.fetch(current_url)
            except FetchError as exc:
                if not candidates:
                    return AdapterResult(
                        provider=self.name,
                        board=board,
                        reason_code="PROVIDER_FETCH_FAILED",
                        retryable=True,
                        trace={"adapter": self.name, "search_urls": search_urls, "error": str(exc)},
                    )
                inventory_complete = False
                stop_reason = "pagination_fetch_failed"
                break
            final_url = page.final_url or page.url
            if (
                not _same_safe_host(final_url, expected_host)
                or (urlparse(final_url).path.rstrip("/") or "/") != expected_path
                or _pagination_query(final_url) != expected_query
            ):
                inventory_complete = False
                stop_reason = "pagination_redirect"
                break
            if custom_tenant and not _custom_page_matches(page, board, custom_tenant):
                inventory_complete = False
                stop_reason = "custom_tenant_identity_mismatch"
                break
            page_candidates, values, page_malformed = _page_candidates(page.html or "", board)
            candidates.extend(page_candidates)
            embedded_payload_count += len(values)
            malformed_json = malformed_json or page_malformed
            pagination = _pagination_metadata(values) or pagination
            candidates = _dedupe_candidates(candidates)
            if exact_title and any(_normalized_title(candidate.title) == exact_title for candidate in page_candidates):
                break
            if not query.title:
                break
            next_urls = _pagination_urls(page, expected_host, expected_path, expected_query)
            if not next_urls:
                break
            next_url = next((url for url in next_urls if url not in seen_urls), None)
            if next_url is None:
                stop_reason = "numbered_pagination_complete"
                break
            current_url = next_url
            search_urls.append(next_url)
        else:
            inventory_complete = False
            stop_reason = "pagination_limit"

        reason_code = None
        if not candidates and inventory_complete:
            reason_code = "INVALID_STRUCTURED_DATA" if malformed_json else "EMPTY_PROVIDER_RESPONSE"
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=reason_code,
            inventory_scope="title_filtered" if query.title else "full",
            inventory_complete=inventory_complete,
            trace={
                "adapter": self.name,
                "search_urls": search_urls,
                "candidate_count": len(candidates),
                "embedded_payload_count": embedded_payload_count,
                "pagination": pagination,
                "inventory_complete": inventory_complete,
                "stop_reason": stop_reason,
            },
        )

    def _list_cloud_sap_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        expected_host = board.identifier.removeprefix(_CLOUD_IDENTIFIER_PREFIX).casefold()
        if not expected_host or not _same_safe_host(board.url, expected_host):
            return _unsupported_cloud_result(board, "invalid SAP Career Site board origin")

        search_url = _cloud_search_url(board.url, query.title)
        try:
            page = fetcher.fetch(search_url)
        except FetchError as exc:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                trace={"adapter": self.name, "variant": "cloud_sap", "search_urls": [search_url], "error": str(exc)},
            )
        final_url = page.final_url or page.url
        if not _same_safe_host(final_url, expected_host):
            return _unsupported_cloud_result(board, "SAP Career Site search redirected outside origin")
        csrf_token = _cloud_csrf_token(page.html)
        locale = _cloud_locale(page.html, final_url)
        if not csrf_token or not locale:
            return _unsupported_cloud_result(board, "missing SAP Career Site CSRF token or locale")

        parsed = urlparse(final_url)
        api_url = urlunparse((parsed.scheme, parsed.netloc, "/services/recruiting/v1/jobs", "", "", ""))
        candidates: list[JobCandidate] = []
        api_urls: list[str] = []
        total_jobs: int | None = None
        response_source: str | None = None
        normalized_target = _normalized_title(query.title)
        exact_title_found = False
        inventory_scope = "title_filtered" if query.title else "full"
        inventory_complete = False
        for page_number in range(_CLOUD_MAX_PAGES):
            payload = {
                "locale": locale,
                "pageNumber": page_number,
                "sortBy": "",
                "keywords": (query.title or "").strip(),
                "location": (query.location or "").strip(),
                "facetFilters": {},
                "brand": "",
                "skills": [],
                "categoryId": 0,
                "alertId": "",
                "rcmCandidateId": "",
            }
            api_urls.append(api_url)
            try:
                response = fetcher.fetch(
                    api_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "X-CSRF-Token": csrf_token,
                        "Origin": _origin(final_url),
                        "Referer": final_url,
                    },
                )
            except FetchError as exc:
                if candidates:
                    break
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    reason_code="PROVIDER_FETCH_FAILED",
                    retryable=True,
                    trace={
                        "adapter": self.name,
                        "variant": "cloud_sap",
                        "search_urls": [search_url],
                        "api_urls": api_urls,
                        "error": str(exc),
                    },
                )
            response_source = response_source or response.source
            response_url = response.final_url or response.url
            if not _same_safe_host(response_url, expected_host, expected_path="/services/recruiting/v1/jobs"):
                return _unsupported_cloud_result(board, "SAP recruiting API redirected outside origin")
            try:
                payload_data = json.loads(response.html)
            except (json.JSONDecodeError, TypeError):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    reason_code="INVALID_STRUCTURED_DATA",
                    trace={"adapter": self.name, "variant": "cloud_sap", "api_urls": api_urls},
                )
            results = payload_data.get("jobSearchResult") if isinstance(payload_data, dict) else None
            if not isinstance(results, list):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    reason_code="INVALID_STRUCTURED_DATA",
                    trace={"adapter": self.name, "variant": "cloud_sap", "api_urls": api_urls},
                )
            page_candidates = _cloud_candidates(results, final_url, expected_host, locale)
            candidates.extend(page_candidates)
            if normalized_target and any(
                _normalized_title(candidate.title) == normalized_target
                for candidate in page_candidates
            ):
                exact_title_found = True
            parsed_total = _nonnegative_int(payload_data.get("totalJobs"))
            if parsed_total is not None:
                total_jobs = max(total_jobs or 0, parsed_total)
            if (
                exact_title_found
                or not results
                or total_jobs is None
                or len(candidates) >= total_jobs
            ):
                inventory_complete = bool(
                    not results or (total_jobs is not None and len(candidates) >= total_jobs)
                )
                break

        candidates = _dedupe_candidates(candidates)
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            inventory_scope=inventory_scope,
            inventory_complete=inventory_complete,
            trace={
                "adapter": self.name,
                "variant": "cloud_sap",
                "search_urls": [search_url],
                "api_urls": api_urls,
                "response_source": response_source,
                "candidate_count": len(candidates),
                "page_count": len(api_urls),
                "total_jobs": total_jobs,
                "locale": locale,
                "exact_title_found": exact_title_found,
                "inventory_scope": inventory_scope,
                "inventory_complete": inventory_complete,
            },
        )


class _SuccessFactorsHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[tuple[str, str]] = []
        self.links: list[tuple[str, str]] = []
        self.theme_records: list[dict] = []
        self.search_form_actions: list[str] = []
        self._script_type = ""
        self._script_parts: list[str] | None = None
        self._href = ""
        self._link_parts: list[str] | None = None
        self._form_action = ""
        self._form_method = ""
        self._form_has_query = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "script":
            self._script_type = attributes.get("type", "")
            self._script_parts = []
        elif tag.casefold() == "a" and attributes.get("href"):
            self._href = attributes["href"]
            self._link_parts = []
        elif tag.casefold() == "form" and not self._form_method:
            self._form_action = attributes.get("action", "")
            self._form_method = attributes.get("method", "get").casefold()
            self._form_has_query = False
        elif (
            tag.casefold() == "input"
            and self._form_method
            and attributes.get("name", "").casefold() in _SEARCH_QUERY_KEYS
        ):
            self._form_has_query = True
        job_req_id = next(
            (
                attributes[key]
                for key in ("data-job-req-id", "data-jobreqid", "data-job-id")
                if attributes.get(key)
            ),
            "",
        )
        title = next(
            (
                attributes[key]
                for key in ("data-job-title", "data-title", "aria-label")
                if attributes.get(key)
            ),
            "",
        )
        if job_req_id and title:
            self.theme_records.append(
                {
                    "jobReqId": job_req_id,
                    "jobTitle": title,
                    "location": attributes.get("data-location", ""),
                }
            )

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)
        if self._link_parts is not None:
            self._link_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._script_parts is not None:
            self.scripts.append((self._script_type, "".join(self._script_parts)))
            self._script_parts = None
            self._script_type = ""
        elif tag.casefold() == "a" and self._link_parts is not None:
            self.links.append((self._href, " ".join("".join(self._link_parts).split())))
            self._href = ""
            self._link_parts = None
        elif tag.casefold() == "form" and self._form_method:
            if self._form_method == "get" and self._form_has_query:
                self.search_form_actions.append(self._form_action)
            self._form_action = ""
            self._form_method = ""
            self._form_has_query = False


def _cloud_search_url(board_url: str, title: str | None) -> str:
    parsed = urlparse(board_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() != "q"
    ]
    if title and title.strip():
        query.append(("q", title.strip()))
    return urlunparse(parsed._replace(path="/search/", query=urlencode(query), fragment=""))


def _cloud_csrf_token(html: str) -> str:
    match = re.search(r'\bCSRFToken\s*=\s*["\']([^"\']+)', html or "")
    return match.group(1).strip() if match else ""


def _cloud_locale(html: str, page_url: str) -> str:
    match = re.search(r'\blocale\s*:\s*["\']([a-z]{2}_[A-Z]{2})["\']', html or "")
    locale = match.group(1) if match else _query_value(urlparse(page_url).query, "locale")
    return locale if re.fullmatch(r"[a-z]{2}_[A-Z]{2}", locale) else ""


def _cloud_candidates(
    results: list[object],
    board_url: str,
    expected_host: str,
    locale: str,
) -> list[JobCandidate]:
    candidates = []
    for item in results:
        record = item.get("response") if isinstance(item, dict) else None
        if not isinstance(record, dict):
            continue
        title = str(record.get("unifiedStandardTitle") or "").strip()
        job_id = str(record.get("id") or "").strip()
        raw_slug = str(record.get("unifiedUrlTitle") or record.get("urlTitle") or "").strip()
        if not title or not job_id.isdigit() or not raw_slug:
            continue
        detail_locale = _cloud_record_locale(record, locale)
        slug = quote(unescape(raw_slug), safe="-._~%")
        detail_url = safe_normalize_url(
            f"/job/{slug}/{job_id}-{detail_locale}/",
            board_url,
        )
        if not detail_url or not _same_safe_host(detail_url, expected_host):
            continue
        candidates.append(
            JobCandidate(
                title=title,
                url=detail_url,
                provider="successfactors",
                location=_cloud_location(record),
                raw={"job_req_id": job_id},
            )
        )
    return candidates


def _cloud_record_locale(record: dict, fallback: str) -> str:
    values = record.get("supportedLocales")
    if isinstance(values, list):
        for value in values:
            if isinstance(value, str) and re.fullmatch(r"[a-z]{2}_[A-Z]{2}", value):
                return value
    return fallback


def _cloud_location(record: dict) -> str | None:
    values = record.get("jobLocationShort")
    if not isinstance(values, list):
        return None
    locations = []
    for value in values:
        if not isinstance(value, str):
            continue
        text = unescape(re.sub(r"<[^>]+>", " ", value))
        text = " ".join(text.split())
        if text and text not in locations:
            locations.append(text)
    return "; ".join(locations) or None


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def _is_cloud_sap_host(host: str) -> bool:
    return host.casefold().endswith(_CLOUD_SAP_SUFFIX)


def _is_shared_legacy_host(host: str) -> bool:
    return bool(_SHARED_LEGACY_HOST.fullmatch(host))


def _legacy_company(query: str) -> str:
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key.casefold() in {"company", "companyid", "company_id"} and value:
            return value
    return ""


def _safe_page_evidence_url(url: str) -> bool:
    normalized = safe_normalize_url(url)
    if not normalized:
        return False
    try:
        parsed = urlparse(normalized)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and port in {None, 80, 443}
    )


def _same_safe_host(
    url: str,
    expected_host: str,
    *,
    expected_path: str | None = None,
) -> bool:
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
        and (parsed.hostname or "").casefold() == expected_host.casefold()
        and (expected_path is None or parsed.path.rstrip("/") == expected_path.rstrip("/"))
    )


def _nonnegative_int(value) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _normalized_title(value: str | None) -> str:
    return " ".join(unescape(value or "").casefold().split())


def _unsupported_cloud_result(board: JobBoard, error: str) -> AdapterResult:
    return AdapterResult(
        provider="successfactors",
        board=board,
        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
        trace={"adapter": "successfactors", "variant": "cloud_sap", "error": error},
    )


def _search_url(board_url: str, title: str | None, *, custom: bool = False) -> str:
    if not title or not title.strip():
        return board_url
    parsed = urlparse(board_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _SEARCH_QUERY_KEYS
    ]
    query.append(("q" if custom else "keyword", title.strip()))
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


def _j2w_tenant_identity(html: str) -> tuple[str, str] | None:
    identities = set()
    parser = _SuccessFactorsHTMLParser()
    parser.feed(html)
    for _, script in parser.scripts:
        for config in _J2W_INIT.findall(unescape(script)):
            company_ids = {match.strip() for match in _SSO_COMPANY_ID.findall(config)}
            sso_urls = {safe_normalize_url(match.strip()) for match in _SSO_URL.findall(config)}
            if (
                len(company_ids) != 1
                or len(sso_urls) != 1
                or None in sso_urls
                or not all(_TENANT_IDENTIFIER.fullmatch(value) for value in company_ids)
            ):
                continue
            company = next(iter(company_ids))
            sso_url = next(iter(sso_urls))
            if sso_url and _legacy_sso_url(sso_url):
                identities.add((company, sso_url))
    return next(iter(identities)) if len(identities) == 1 else None


def _legacy_sso_url(url: str) -> bool:
    board_url = urlparse(url)
    return (
        _recognized_host(url)
        and not _is_cloud_sap_host(board_url.hostname or "")
        and board_url.path in {"", "/"}
        and not board_url.query
    )


def _custom_board_url(page_url: str, html: str) -> str | None:
    normalized = safe_normalize_url(page_url)
    if not normalized:
        return None
    parser = _SuccessFactorsHTMLParser()
    parser.feed(html)
    observed_actions = {
        action_url
        for raw_action in parser.search_form_actions
        if (
            action_url := safe_normalize_url(raw_action or normalized, normalized)
        )
        and _same_safe_host(action_url, urlparse(normalized).hostname or "")
    }
    if len(observed_actions) > 1:
        return None
    selected = next(iter(observed_actions), normalized)
    parsed = urlparse(selected)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _SEARCH_QUERY_KEYS and key.casefold() != "startrow"
    ]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


def _custom_tenant(board: JobBoard) -> str:
    identifier = board.identifier or ""
    tenant = identifier.removeprefix(_CUSTOM_IDENTIFIER_PREFIX)
    return tenant if identifier.startswith(_CUSTOM_IDENTIFIER_PREFIX) and _TENANT_IDENTIFIER.fullmatch(tenant) else ""


def _custom_page_matches(page: Page, board: JobBoard, tenant: str) -> bool:
    page_url = page.final_url or page.url
    expected_host = (urlparse(board.url).hostname or "").casefold()
    identity = _j2w_tenant_identity(page.html or "")
    return _same_safe_host(page_url, expected_host) and identity is not None and identity[0] == tenant


def _page_candidates(html: str, board: JobBoard) -> tuple[list[JobCandidate], list[object], bool]:
    parser = _SuccessFactorsHTMLParser()
    parser.feed(html)
    candidates = _anchor_candidates(parser.links, board)
    candidates.extend(_record_candidate(record, board) for record in parser.theme_records)
    values, malformed_json = _embedded_json_values(html, parser.scripts)
    for value in values:
        candidates.extend(_walk_candidates(value, board))
    return [candidate for candidate in candidates if candidate is not None], values, malformed_json


def _pagination_query(url: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (key.casefold(), value)
            for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True)
            if key.casefold() != "startrow"
        )
    )


def _pagination_urls(page: Page, expected_host: str, expected_path: str, expected_query: tuple[tuple[str, str], ...]) -> list[str]:
    parser = _SuccessFactorsHTMLParser()
    parser.feed(page.html or "")
    base_url = page.final_url or page.url
    urls: dict[int, str] = {}
    for href, _ in parser.links:
        normalized = safe_normalize_url(href, base_url)
        if not normalized or not _same_safe_host(normalized, expected_host):
            continue
        parsed = urlparse(normalized)
        if (parsed.path.rstrip("/") or "/") != expected_path or _pagination_query(normalized) != expected_query:
            continue
        start_rows = [value for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key.casefold() == "startrow"]
        if len(start_rows) != 1 or not start_rows[0].isdigit() or int(start_rows[0]) <= 0:
            continue
        urls[int(start_rows[0])] = normalized
    return [urls[offset] for offset in sorted(urls)]


def _embedded_json_values(
    html: str,
    scripts: list[tuple[str, str]],
) -> tuple[list[object], bool]:
    values: list[object] = []
    malformed_json = False
    for script_type, script in scripts:
        decoded = unescape(script).strip()
        if not decoded:
            continue
        script_values = _decode_json_fragments(decoded)
        values.extend(script_values)
        if "json" in script_type.casefold() and not script_values:
            malformed_json = True

    # Some SuccessFactors themes serialize state into data attributes rather
    # than script tags. Scanning the full document catches those JSON objects.
    values.extend(_decode_json_fragments(unescape(html)))
    return values, malformed_json


def _decode_json_fragments(text: str) -> list[object]:
    decoder = json.JSONDecoder()
    values = []
    cursor = 0
    while cursor < len(text):
        starts = [position for token in ("{", "[") if (position := text.find(token, cursor)) >= 0]
        if not starts:
            break
        start = min(starts)
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        values.append(value)
        cursor = max(end, start + 1)
    return values


def _walk_candidates(value: object, board: JobBoard):
    if isinstance(value, dict):
        candidate = _record_candidate(value, board)
        if candidate is not None:
            yield candidate
        for child in value.values():
            yield from _walk_candidates(child, board)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_candidates(child, board)
    elif isinstance(value, str):
        decoded = value.strip()
        if decoded.startswith(("{", "[")):
            try:
                yield from _walk_candidates(json.loads(decoded), board)
            except json.JSONDecodeError:
                return


def _record_candidate(record: dict, board: JobBoard) -> JobCandidate | None:
    title = _first_text(record, _TITLE_FIELDS)
    if not title:
        return None
    job_req_id = _first_text(record, _DETAIL_ID_FIELDS)
    has_explicit_url = bool(_first_text(record, _URL_FIELDS))
    detail_url = _explicit_detail_url(record, board)
    if has_explicit_url and not detail_url:
        return None
    if _custom_tenant(board) and not detail_url:
        return None
    if not detail_url and job_req_id:
        detail_url = _reconstruct_detail_url(board.url, job_req_id)
    if not detail_url:
        return None
    return JobCandidate(
        title=title,
        url=detail_url,
        provider="successfactors",
        location=_location(record),
        raw={"job_req_id": job_req_id or None},
    )


def _anchor_candidates(links: list[tuple[str, str]], board: JobBoard) -> list[JobCandidate]:
    candidates = []
    for href, text in links:
        normalized = safe_normalize_url(href, board.url)
        if not normalized:
            continue
        normalized = _inherit_board_company(normalized, board)
        if not _same_board_tenant(normalized, board):
            continue
        parsed = urlparse(normalized)
        query = {key.casefold(): value for key, value in parse_qsl(parsed.query)}
        job_req_id = next((query[key] for key in _DETAIL_QUERY_KEYS if query.get(key)), "")
        if not job_req_id and _custom_tenant(board):
            match = re.search(r"/(\d+)(?:/)?$", parsed.path)
            job_req_id = match.group(1) if match else ""
        if not job_req_id or not text.strip() or (_custom_tenant(board) and not _custom_detail_url(normalized)):
            continue
        candidates.append(
            JobCandidate(
                title=text.strip(),
                url=normalized,
                provider="successfactors",
                raw={"job_req_id": job_req_id},
            )
        )
    return candidates


def _explicit_detail_url(record: dict, board: JobBoard) -> str | None:
    raw_url = _first_text(record, _URL_FIELDS)
    if not raw_url:
        return None
    normalized = safe_normalize_url(urljoin(board.url, raw_url))
    if not normalized:
        return None
    normalized = _inherit_board_company(normalized, board)
    if not _same_board_tenant(normalized, board):
        return None
    return normalized if not _custom_tenant(board) or _custom_detail_url(normalized) else None


def _custom_detail_url(url: str) -> bool:
    parsed = urlparse(url)
    if any(key.casefold() in _DETAIL_QUERY_KEYS and value for key, value in parse_qsl(parsed.query)):
        return True
    return bool(re.fullmatch(r"/job(?:/.+)?/\d+/", parsed.path.rstrip("/") + "/"))


def _reconstruct_detail_url(board_url: str, job_req_id: str) -> str:
    parsed = urlparse(board_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _DETAIL_QUERY_KEYS and key.casefold() != "career_ns"
    ]
    query.extend(
        (("career_ns", "job_listing"), ("career_job_req_id", job_req_id.strip()))
    )
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


def _location(record: dict) -> str | None:
    value = next(
        (
            record[field]
            for field in (
                "location",
                "jobLocation",
                "locationName",
                "formattedLocation",
                "jobLocationText",
            )
            if record.get(field)
        ),
        None,
    )
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        address = value.get("address") if isinstance(value.get("address"), dict) else value
        name = _first_text(address, ("name", "addressLocality", "city"))
        region = _first_text(address, ("addressRegion", "state"))
        country = _first_text(address, ("addressCountry", "country"))
        parts = []
        for part in (name, region, country):
            if part and part.casefold() not in {existing.casefold() for existing in parts}:
                parts.append(part)
        return ", ".join(parts) or None
    return None


def _first_text(record: dict, fields: tuple[str, ...]) -> str:
    for field in fields:
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
        if isinstance(value, dict):
            nested = _first_text(value, ("value", "label", "text", "name"))
            if nested:
                return nested
    return ""


def _recognized_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return (
        any(host == suffix or host.endswith(f".{suffix}") for suffix in _HOST_SUFFIXES)
        or _is_cloud_sap_host(host)
    )


def _same_board_tenant(url: str, board: JobBoard) -> bool:
    try:
        candidate = urlparse(url)
        expected = urlparse(board.url)
        candidate_port = candidate.port
    except ValueError:
        return False
    if (
        candidate.scheme.casefold() not in {"http", "https"}
        or candidate.username
        or candidate.password
        or candidate_port not in {None, 80, 443}
        or (candidate.hostname or "").casefold() != (expected.hostname or "").casefold()
    ):
        return False
    if _custom_tenant(board):
        return True
    if not _recognized_host(url):
        return False
    expected_companies = _query_values(expected.query, "company")
    candidate_companies = _query_values(candidate.query, "company")
    return not expected_companies or candidate_companies == expected_companies


def _inherit_board_company(url: str, board: JobBoard) -> str:
    parsed = urlparse(url)
    company = _query_value(urlparse(board.url).query, "company")
    if not company or _query_value(parsed.query, "company"):
        return url
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("company", company))
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _query_value(query: str, expected_key: str) -> str:
    return next(
        (
            value.strip()
            for key, value in parse_qsl(query, keep_blank_values=True)
            if key.casefold() == expected_key.casefold() and value.strip()
        ),
        "",
    )


def _query_values(query: str, expected_key: str) -> set[str]:
    return {
        value.strip().casefold()
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key.casefold() == expected_key.casefold() and value.strip()
    }


def _pagination_metadata(values: list[object]) -> dict[str, object]:
    aliases = {
        "total_results": ("totalResults", "totalCount"),
        "page_size": ("pageSize", "resultsPerPage"),
        "current_page": ("currentPage", "pageNumber"),
        "offset": ("startRow", "offset", "startIndex"),
        "has_more": ("hasMore", "moreAvailable"),
        "next_page": ("nextPage", "nextPageUrl"),
    }
    metadata: dict[str, object] = {}
    for value in values:
        for record in _walk_records(value):
            for normalized_key, fields in aliases.items():
                if normalized_key in metadata:
                    continue
                raw = next((record[field] for field in fields if field in record), None)
                if isinstance(raw, (str, int, float, bool)) and raw != "":
                    metadata[normalized_key] = raw
    return metadata


def _walk_records(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_records(child)
    elif isinstance(value, str) and value.strip().startswith(("{", "[")):
        try:
            yield from _walk_records(json.loads(value))
        except json.JSONDecodeError:
            return


def _dedupe_candidates(candidates: list[JobCandidate]) -> list[JobCandidate]:
    seen = set()
    deduped = []
    for candidate in candidates:
        key = candidate.url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


ADAPTER = SuccessFactorsAdapter()
