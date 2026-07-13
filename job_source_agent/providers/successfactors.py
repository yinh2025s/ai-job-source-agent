from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

from ..web import FetchError, safe_normalize_url
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST_SUFFIXES = ("successfactors.com", "sapsf.com")
_CLOUD_SAP_SUFFIX = ".jobs.hr.cloud.sap"
_CLOUD_IDENTIFIER_PREFIX = "cloud:"
_CLOUD_PAGE_SIZE = 10
_CLOUD_MAX_PAGES = 5
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
_DETAIL_QUERY_KEYS = {"career_job_req_id", "jobreqid", "job_req_id"}
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
        identifier = company or (parsed.hostname or "").lower()
        return JobBoard(url=board_url, provider=self.name, identifier=identifier or None)

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

        search_url = _search_url(board.url, query.title)
        try:
            page = fetcher.fetch(search_url)
        except FetchError as exc:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                trace={"adapter": self.name, "search_urls": [search_url], "error": str(exc)},
            )
        parser = _SuccessFactorsHTMLParser()
        parser.feed(page.html or "")

        candidates = _anchor_candidates(parser.links, board)
        candidates.extend(_record_candidate(record, board) for record in parser.theme_records)
        candidates = [candidate for candidate in candidates if candidate is not None]
        values, malformed_json = _embedded_json_values(page.html or "", parser.scripts)
        for value in values:
            candidates.extend(_walk_candidates(value, board))
        candidates = _dedupe_candidates(candidates)
        pagination = _pagination_metadata(values)

        reason_code = None
        if not candidates:
            reason_code = "INVALID_STRUCTURED_DATA" if malformed_json else "EMPTY_PROVIDER_RESPONSE"
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=reason_code,
            trace={
                "adapter": self.name,
                "search_urls": [search_url],
                "response_source": page.source,
                "candidate_count": len(candidates),
                "embedded_payload_count": len(values),
                "pagination": pagination,
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
        self._script_type = ""
        self._script_parts: list[str] | None = None
        self._href = ""
        self._link_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "script":
            self._script_type = attributes.get("type", "")
            self._script_parts = []
        elif tag.casefold() == "a" and attributes.get("href"):
            self._href = attributes["href"]
            self._link_parts = []
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


def _search_url(board_url: str, title: str | None) -> str:
    if not title or not title.strip():
        return board_url
    parsed = urlparse(board_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _SEARCH_QUERY_KEYS
    ]
    query.append(("keyword", title.strip()))
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


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
        query = {key.casefold(): value for key, value in parse_qsl(urlparse(normalized).query)}
        job_req_id = next((query[key] for key in _DETAIL_QUERY_KEYS if query.get(key)), "")
        if not job_req_id or not text.strip():
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
    return normalized if _same_board_tenant(normalized, board) else None


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
    if not _recognized_host(url):
        return False
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
