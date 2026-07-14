from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from urllib.parse import quote, urlparse, urlunparse

from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError, Page
from .base import (
    AdapterResult,
    JobBoard,
    JobCandidate,
    JobQuery,
    pagination_fetch_reserve_seconds,
    require_fetch_reserve,
)


_API_PATH = "/api/data/jobs/summarized"
_MAX_HTML_CHARS = 2_000_000
_MAX_CONFIG_CHARS = 20_000
_MAX_PAGES = 10
_MAX_RANGE = 1_000_000_000
_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class SitecoreNextJobsAdapter:
    name = "sitecore_next_jobs"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        # These boards are hosted on customer domains and require page evidence.
        return False

    def identify_board(self, url: str) -> JobBoard | None:
        return None

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        parsed = _safe_https_url(page_url)
        if parsed is None:
            return None
        next_data = _next_data(page.html)
        identity = _identity_from_next_data(next_data, parsed)
        if identity is None:
            return None
        board_url = urlunparse(parsed._replace(query="", fragment=""))
        return JobBoard(
            url=board_url,
            provider=self.name,
            identifier=_encode_identity(identity),
            replay_safe=True,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        inventory_scope = "title_filtered" if query.title else "full"
        identity = _board_identity(board)
        if identity is None:
            return _failure(
                board,
                inventory_scope,
                "PROVIDER_VARIANT_UNSUPPORTED",
                "invalid Sitecore/Next JobSearch board identity",
            )

        api_url = identity["origin"] + _API_PATH
        candidates: list[JobCandidate] = []
        candidate_ids: set[str] = set()
        ranges: list[int] = []
        errors: list[dict[str, object]] = []
        response_source: str | None = None
        expected_total: int | None = None
        records_seen = 0
        current_range = 0
        inventory_complete = False
        failure_reason: str | None = None
        failure_retryable = False
        stopped_on_exact_title = False
        stop_reason: str | None = None

        for page_number in range(_MAX_PAGES):
            if current_range in ranges:
                failure_reason = "INVALID_STRUCTURED_DATA"
                errors.append({"range": current_range, "error": "repeated pagination range"})
                break
            body = _request_body(identity, query, current_range)
            request_data = json.dumps(
                body,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
            request_headers = {
                "Accept": "application/json",
                "Content-Type": "text/plain;charset=UTF-8",
            }
            try:
                if page_number > 0:
                    require_fetch_reserve(
                        fetcher,
                        pagination_fetch_reserve_seconds(
                            fetcher,
                            publication_reserve_seconds=1.0,
                        ),
                        url=api_url,
                        data=request_data,
                        headers=request_headers,
                    )
                ranges.append(current_range)
                response = fetcher.fetch(
                    api_url,
                    data=request_data,
                    headers=request_headers,
                )
            except (FetchError, OSError, TimeoutError) as error:
                detail = str(error)
                failure_reason = _fetch_reason(error)
                failure_retryable = reason_spec(failure_reason).retryable
                if failure_reason == "FETCH_BUDGET_EXHAUSTED":
                    stop_reason = "soft_deadline_reserve"
                errors.append({"range": current_range, "error": detail})
                break

            response_source = response_source or response.source
            response_url = response.final_url or response.url
            if not _same_endpoint(response_url, api_url):
                failure_reason = "PROVIDER_VARIANT_UNSUPPORTED"
                errors.append(
                    {"range": current_range, "error": "API redirected away from endpoint"}
                )
                break
            try:
                payload = json.loads(response.html)
            except (json.JSONDecodeError, TypeError, ValueError):
                failure_reason = "INVALID_STRUCTURED_DATA"
                errors.append({"range": current_range, "error": "invalid API JSON"})
                break

            parsed_page = _inventory_page(payload)
            if parsed_page is None:
                failure_reason = "INVALID_STRUCTURED_DATA"
                errors.append({"range": current_range, "error": "invalid inventory schema"})
                break
            jobs, page_total, next_range = parsed_page
            if expected_total is None:
                expected_total = page_total
            elif page_total != expected_total:
                failure_reason = "INVALID_STRUCTURED_DATA"
                errors.append({"range": current_range, "error": "contradictory total"})
                break

            records_seen += len(jobs)
            if records_seen > expected_total or (not jobs and records_seen < expected_total):
                failure_reason = "INVALID_STRUCTURED_DATA"
                errors.append(
                    {"range": current_range, "error": "inventory count contradicts total"}
                )
                break

            invalid_record = False
            for record in jobs:
                candidate = _candidate(record, board, identity)
                if candidate is None:
                    invalid_record = True
                    continue
                job_id = candidate.raw["jobId"].casefold()
                if job_id in candidate_ids:
                    invalid_record = True
                    continue
                candidates.append(candidate)
                candidate_ids.add(job_id)
                if query.title and _same_title(candidate.title, query.title):
                    stopped_on_exact_title = True
                    break
            if invalid_record:
                failure_reason = "INVALID_STRUCTURED_DATA"
                errors.append(
                    {
                        "range": current_range,
                        "error": "invalid, duplicate, or cross-tenant job record",
                    }
                )
                break
            if stopped_on_exact_title:
                break
            if records_seen >= expected_total:
                inventory_complete = True
                break
            if (
                next_range is None
                or next_range <= current_range
                or next_range in ranges
                or next_range > _MAX_RANGE
            ):
                failure_reason = "INVALID_STRUCTURED_DATA"
                errors.append({"range": current_range, "error": "invalid nextRange"})
                break
            current_range = next_range
        else:
            failure_reason = "FETCH_BUDGET_EXHAUSTED"
            failure_retryable = True
            errors.append({"range": current_range, "error": "pagination cap reached"})

        if stopped_on_exact_title:
            inventory_complete = False
        if failure_reason:
            reason_code = failure_reason
        elif not candidates and inventory_complete:
            reason_code = "EMPTY_PROVIDER_RESPONSE"
        elif not candidates:
            reason_code = "PROVIDER_FETCH_FAILED"
        else:
            reason_code = None

        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=reason_code,
            retryable=failure_retryable if failure_reason else False,
            inventory_scope=inventory_scope,
            inventory_complete=inventory_complete,
            trace={
                "adapter": self.name,
                "endpoint": api_url,
                "response_source": response_source,
                "ranges": ranges,
                "page_count": len(ranges),
                "records_seen": records_seen,
                "total": expected_total,
                "candidate_count": len(candidates),
                "stopped_on_exact_title": stopped_on_exact_title,
                "stop_reason": stop_reason,
                "errors": errors,
                "inventory_scope": inventory_scope,
                "inventory_complete": inventory_complete,
            },
        )


def _fetch_reason(error: Exception) -> str:
    typed = getattr(error, "reason_code", None)
    if isinstance(typed, str) and typed:
        return typed
    reason_code = classify_fetch_error(str(error))
    return "PROVIDER_FETCH_FAILED" if reason_code == "FETCH_FAILED" else reason_code


class _NextDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capturing = False
        self._parts: list[str] = []
        self.documents: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.casefold(): value for name, value in attrs}
        if tag.casefold() == "script" and attributes.get("id") == "__NEXT_DATA__":
            self._capturing = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._capturing:
            self.documents.append("".join(self._parts))
            self._capturing = False
            self._parts = []


def _next_data(html: str) -> dict | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    parser = _NextDataParser()
    try:
        parser.feed(html)
        parser.close()
        if len(parser.documents) != 1:
            return None
        value = json.loads(parser.documents[0])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _identity_from_next_data(value: dict | None, parsed_url) -> dict | None:
    if value is None:
        return None
    page_props = value.get("props", {}).get("pageProps")
    if not isinstance(page_props, dict):
        return None
    sitecore = page_props.get("layoutData", {}).get("sitecore")
    if not isinstance(sitecore, dict) or not isinstance(sitecore.get("context"), dict):
        return None
    context = sitecore["context"]
    site = context.get("site")
    route = sitecore.get("route")
    if not isinstance(site, dict) or not isinstance(route, dict):
        return None
    components = _job_search_components(route)
    if len(components) != 1:
        return None
    component = components[0]
    uid = component.get("uid")
    component_props = page_props.get("componentProps")
    props = component_props.get(uid) if isinstance(component_props, dict) else None
    params = component.get("params")
    dictionary = page_props.get("dictionary")
    if not all(isinstance(item, dict) for item in (props, params, dictionary)):
        return None

    hostname = (parsed_url.hostname or "").casefold()
    site_host = _bounded_string(props.get("siteHost"))
    identity = {
        "origin": _origin(parsed_url),
        "path": parsed_url.path or "/",
        "site": _bounded_string(site.get("name")),
        "language": _bounded_string(props.get("contextLanguage")),
        "country": _bounded_string(props.get("contextCountry")),
        "brand": _bounded_string(props.get("brandName")),
        "config": {
            "baseSearchQuery": _bounded_string(props.get("baseSearchQuery")),
            "filtersToDisplay": _bounded_string(params.get("JobSearchFiltering")),
            "brandFromDictionary": _bounded_string(
                props.get("brandAustralia") or dictionary.get("brandAustralia")
            ),
        },
    }
    if site_host is None or site_host.casefold() != hostname or not _valid_identity(identity):
        return None
    return identity


def _job_search_components(value) -> list[dict]:
    found: list[dict] = []
    if isinstance(value, dict):
        if value.get("componentName") == "JobSearch":
            found.append(value)
        for child in value.values():
            found.extend(_job_search_components(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_job_search_components(child))
    return found


def _bounded_string(value) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if len(value) > _MAX_CONFIG_CHARS or any(ord(character) < 32 for character in value):
        return None
    return value


def _valid_identity(identity) -> bool:
    if not isinstance(identity, dict) or set(identity) != {
        "origin",
        "path",
        "site",
        "language",
        "country",
        "brand",
        "config",
    }:
        return False
    config = identity.get("config")
    if not isinstance(config, dict) or set(config) != {
        "baseSearchQuery",
        "filtersToDisplay",
        "brandFromDictionary",
    }:
        return False
    scalar_values = [
        identity[key]
        for key in ("origin", "path", "site", "language", "country", "brand")
    ]
    scalar_values.extend(config.values())
    return all(_bounded_string(item) is not None for item in scalar_values)


def _encode_identity(identity: dict) -> str:
    return json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _board_identity(board: JobBoard) -> dict | None:
    if board.provider != "sitecore_next_jobs" or not board.identifier:
        return None
    parsed = _safe_https_url(board.url)
    if parsed is None or parsed.query or parsed.fragment:
        return None
    try:
        identity = json.loads(board.identifier)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not _valid_identity(identity):
        return None
    if (
        identity["origin"] != _origin(parsed)
        or identity["path"] != (parsed.path or "/")
    ):
        return None
    return identity


def _request_body(identity: dict, query: JobQuery, current_range: int) -> dict:
    config = identity["config"]
    query_string = "&sort=PostedDate desc&facet.pivot=IsRemote"
    if query.title:
        query_string += "&q=" + quote(query.title, safe="")
    return {
        "baseSearchQuery": config["baseSearchQuery"],
        "filtersToDisplay": config["filtersToDisplay"],
        "queryString": query_string,
        "range": current_range,
        "siteName": identity["site"],
        "brand": identity["brand"],
        "countryCookie": identity["country"],
        "langCookie": identity["language"].lower(),
        "brandFromDictionary": config["brandFromDictionary"],
    }


def _inventory_page(payload) -> tuple[list, int, int | None] | None:
    if not isinstance(payload, dict):
        return None
    jobs = payload.get("jobs")
    facet_counts = payload.get("facet_counts")
    facets = payload.get("facets")
    pagination = payload.get("pagination")
    filters = payload.get("filters")
    if not (
        isinstance(jobs, list)
        and isinstance(facet_counts, dict)
        and isinstance(facets, dict)
        and isinstance(pagination, dict)
        and isinstance(filters, list)
    ):
        return None
    total = pagination.get("total")
    next_range = pagination.get("nextRange")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return None
    if next_range is not None and (
        isinstance(next_range, bool) or not isinstance(next_range, int) or next_range < 0
    ):
        return None
    if any(not isinstance(record, dict) for record in jobs):
        return None
    return jobs, total, next_range


def _candidate(record: dict, board: JobBoard, identity: dict) -> JobCandidate | None:
    title = _clean_record_string(record.get("jobTitle"))
    job_id = record.get("jobId")
    location_value = record.get("jobLocation")
    location = _clean_record_string(location_value) if location_value else None
    if title is None or not isinstance(job_id, str):
        return None
    if not _JOB_ID_PATTERN.fullmatch(job_id):
        return None
    if location_value and location is None:
        return None
    if not _record_matches_identity(record, identity):
        return None
    parsed = urlparse(board.url)
    candidate_url = urlunparse(parsed._replace(query="jobId=" + job_id.lower(), fragment=""))
    return JobCandidate(
        title=title,
        url=candidate_url,
        provider="sitecore_next_jobs",
        location=location or None,
        raw={
            "jobId": job_id,
            "brandName": record["brandName"],
            "language": record["language"],
            "countryId": record["countryId"],
        },
    )


def _record_matches_identity(record: dict, identity: dict) -> bool:
    brand = _clean_record_string(record.get("brandName"))
    language = _clean_record_string(record.get("language"))
    country = _clean_record_string(record.get("countryId"))
    if None in (brand, language, country):
        return False
    if brand.casefold() != identity["brand"].casefold():
        return False
    language_parts = language.replace("_", "-").split("-")
    expected_language = identity["language"].replace("_", "-").split("-")[0]
    expected_country = identity["country"].upper()
    if language_parts[0].casefold() != expected_language.casefold():
        return False
    if len(language_parts) > 1 and language_parts[-1].upper() != expected_country:
        return False
    record_country = country.upper()
    return record_country == expected_country or (
        len(record_country) == 3 and record_country.startswith(expected_country)
    )


def _clean_record_string(value) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > _MAX_CONFIG_CHARS:
        return None
    return cleaned


def _same_title(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def _safe_https_url(url: str):
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
    hostname = (parsed.hostname or "").casefold()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return f"https://{hostname}"


def _same_endpoint(left: str, right: str) -> bool:
    left_parsed = _safe_https_url(left)
    right_parsed = _safe_https_url(right)
    return (
        left_parsed is not None
        and right_parsed is not None
        and _origin(left_parsed) == _origin(right_parsed)
        and left_parsed.path == right_parsed.path
        and not left_parsed.query
        and not left_parsed.fragment
    )


def _failure(
    board: JobBoard,
    inventory_scope: str,
    reason_code: str,
    error: str,
) -> AdapterResult:
    return AdapterResult(
        provider="sitecore_next_jobs",
        board=board,
        reason_code=reason_code,
        retryable=False,
        inventory_scope=inventory_scope,
        inventory_complete=False,
        trace={
            "adapter": "sitecore_next_jobs",
            "error": error,
            "inventory_scope": inventory_scope,
            "inventory_complete": False,
        },
    )


ADAPTER = SitecoreNextJobsAdapter()
