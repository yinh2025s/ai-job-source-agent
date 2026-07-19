from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
from typing import Any
import unicodedata
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST = re.compile(
    r"^(?P<tenant>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
    r"\.fa(?:\.(?P<region>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))?"
    r"\.oraclecloud\.com$"
)
_LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z]{2})?$")
_SITE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_OPENING_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_LOCATOR_VERSION = 1
_INVENTORY_LIMIT = 25
_MAX_INVENTORY_PAGES = 10
_MAX_INVENTORY_ROWS = _INVENTORY_LIMIT * _MAX_INVENTORY_PAGES
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "credential",
        "jwt",
        "password",
        "session",
        "sessionid",
        "token",
    }
)
_CLOSED_MARKERS = (
    "job is no longer available",
    "job posting is no longer available",
    "position has been filled",
    "no longer accepting applications",
)
_LOGIN_MARKERS = (
    "sign in to access",
    "sign in to continue",
    "log in to continue",
    "oracle cloud account sign in",
)


class OracleHCMAdapter:
    name = "oracle_hcm"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _parse_candidate_url(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        route = _parse_candidate_url(url)
        if route is None:
            return None
        board_url = _candidate_url(route, opening_id=None)
        locator: dict[str, Any] = {
            "host": route["host"],
            "locale": route["locale"],
            "site": route["site"],
            "tenant": route["tenant"],
            "v": _LOCATOR_VERSION,
        }
        if route["opening_id"] is not None:
            locator["detail_url"] = _candidate_url(route, route["opening_id"])
            locator["opening_id"] = route["opening_id"]
        return JobBoard(
            url=board_url,
            provider=self.name,
            identifier=json.dumps(locator, sort_keys=True, separators=(",", ":")),
            replay_safe=True,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        locator = _validated_locator(board)
        if locator is None:
            return _incomplete(board, "invalid Oracle HCM CandidateExperience locator")
        detail_url = locator.get("detail_url")
        opening_id = locator.get("opening_id")
        if not isinstance(detail_url, str) or not isinstance(opening_id, str):
            return _list_inventory(fetcher, board, locator, query)

        try:
            page = fetcher.fetch(detail_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _incomplete(
                board,
                str(error),
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                detail_url=detail_url,
            )

        final_url = page.final_url or page.url
        final_route = _parse_candidate_url(final_url)
        if not _same_exact_route(final_route, locator):
            return _incomplete(
                board,
                "Oracle HCM detail redirect broke tenant/site/opening continuity",
                rejected_response_url=final_url,
                detail_url=detail_url,
            )

        html = page.html or ""
        folded_html = re.sub(r"\s+", " ", html).casefold()
        if any(marker in folded_html for marker in _LOGIN_MARKERS):
            return _incomplete(
                board,
                "Oracle HCM detail returned a login wall",
                reason_code="LOGIN_REQUIRED",
                detail_url=detail_url,
            )
        if any(marker in folded_html for marker in _CLOSED_MARKERS):
            return _empty(board, detail_url, page.source, "closed_page_evidence")

        postings, malformed = _job_postings(html)
        if malformed or not postings:
            return _incomplete(
                board,
                "missing or malformed Oracle HCM JobPosting JSON-LD",
                reason_code="INVALID_STRUCTURED_DATA",
                detail_url=detail_url,
                response_source=page.source,
            )

        validated: list[dict[str, Any]] = []
        for posting in postings:
            evidence_error = _posting_conflict(posting, locator)
            if evidence_error:
                return _incomplete(
                    board,
                    evidence_error,
                    reason_code="INVALID_STRUCTURED_DATA",
                    detail_url=detail_url,
                    response_source=page.source,
                )
            validated.append(posting)
        if len(validated) != 1:
            return _incomplete(
                board,
                "ambiguous Oracle HCM JobPosting JSON-LD",
                reason_code="INVALID_STRUCTURED_DATA",
                detail_url=detail_url,
                response_source=page.source,
            )

        posting = validated[0]
        title = _text(posting.get("title"))
        if not title:
            return _incomplete(
                board,
                "Oracle HCM JobPosting is missing a title",
                reason_code="INVALID_STRUCTURED_DATA",
                detail_url=detail_url,
                response_source=page.source,
            )
        expiration = _expiration_state(posting.get("validThrough"))
        if expiration == "invalid":
            return _incomplete(
                board,
                "Oracle HCM JobPosting has an invalid validThrough value",
                reason_code="INVALID_STRUCTURED_DATA",
                detail_url=detail_url,
                response_source=page.source,
            )
        if expiration == "expired":
            return _empty(board, detail_url, page.source, "expired_valid_through")
        if query.title and _normalized_text(title) != _normalized_text(query.title):
            return _empty(board, detail_url, page.source, "title_mismatch")

        raw = {
            key: posting[key]
            for key in (
                "identifier",
                "hiringOrganization",
                "jobLocation",
                "jobLocationType",
                "url",
                "datePosted",
                "validThrough",
            )
            if key in posting
        }
        hiring_organization = posting.get("hiringOrganization")
        if isinstance(hiring_organization, dict):
            organization_name = _text(hiring_organization.get("name"))
            if organization_name:
                raw["hiring_organization_name"] = organization_name
        candidate = JobCandidate(
            title=title,
            url=detail_url,
            provider=self.name,
            location=_location(posting.get("jobLocation")),
            raw=raw,
        )
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=[candidate],
            inventory_scope="title_filtered",
            inventory_complete=True,
            trace={
                "adapter": self.name,
                "variant": "candidate_experience_exact_detail",
                "detail_urls": [detail_url],
                "response_source": page.source,
                "tenant": locator["tenant"],
                "site": locator["site"],
                "opening_id": opening_id,
                "candidate_count": 1,
                "inventory_scope": "title_filtered",
                "inventory_complete": True,
            },
        )


def _list_inventory(
    fetcher,
    board: JobBoard,
    locator: dict[str, Any],
    query: JobQuery,
) -> AdapterResult:
    title = _text(query.title)
    if (
        title is None
        or len(title) > 500
        or any(unicodedata.category(character).startswith("C") for character in title)
        or any(character in {",", ";"} for character in title)
    ):
        return _incomplete(board, "bounded Oracle HCM title query is required")
    api_urls: list[str] = []
    response_sources: list[str] = []
    rows: list[dict[str, str | None]] = []
    seen_ids: set[str] = set()
    expected_total: int | None = None
    offset = 0
    while expected_total is None or offset < expected_total:
        endpoint = _inventory_endpoint(locator, title, offset)
        api_urls.append(endpoint)
        try:
            page = fetcher.fetch(endpoint, headers={"Accept": "application/json"})
        except (FetchError, OSError, TimeoutError) as error:
            return _incomplete(
                board,
                str(error),
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                api_urls=api_urls,
            )
        response_url = page.final_url or page.url
        if not _same_inventory_endpoint(response_url, locator["host"]):
            return _incomplete(
                board,
                "Oracle HCM inventory redirect broke tenant continuity",
                api_urls=api_urls,
                rejected_response_url=response_url,
            )
        response_sources.append(page.source)
        try:
            payload = json.loads(page.html)
        except (TypeError, json.JSONDecodeError):
            return _incomplete(
                board,
                "Oracle HCM inventory returned malformed JSON",
                reason_code="INVALID_STRUCTURED_DATA",
                api_urls=api_urls,
            )
        parsed = _inventory_rows(payload, locator)
        if isinstance(parsed, str):
            return _incomplete(
                board,
                parsed,
                reason_code="INVALID_STRUCTURED_DATA",
                api_urls=api_urls,
            )
        page_rows, total = parsed
        if expected_total is None:
            expected_total = total
            if total > _MAX_INVENTORY_ROWS:
                return _incomplete(
                    board,
                    "Oracle HCM inventory exceeds the bounded pagination limit",
                    api_urls=api_urls,
                    total=total,
                    candidate_count=0,
                    stop_reason="result_cap_reached",
                )
        elif total != expected_total:
            return _incomplete(
                board,
                "Oracle HCM inventory total changed during pagination",
                reason_code="INVALID_STRUCTURED_DATA",
                api_urls=api_urls,
            )
        expected_page_size = min(_INVENTORY_LIMIT, expected_total - offset)
        if len(page_rows) != expected_page_size:
            return _incomplete(
                board,
                "Oracle HCM inventory page is incomplete",
                reason_code="INVALID_STRUCTURED_DATA",
                api_urls=api_urls,
            )
        for row in page_rows:
            folded_id = row["id"].casefold()
            if folded_id in seen_ids:
                return _incomplete(
                    board,
                    "Oracle HCM inventory repeats a job across pages",
                    reason_code="INVALID_STRUCTURED_DATA",
                    api_urls=api_urls,
                )
            seen_ids.add(folded_id)
            rows.append(row)
        offset += len(page_rows)

    total = expected_total or 0
    candidates = [
        JobCandidate(
            title=row["title"],
            url=_candidate_url(locator, row["id"]),
            provider="oracle_hcm",
            location=row["location"],
            raw={"job_id": row["id"], "posted_date": row["posted_date"]},
        )
        for row in rows
    ]
    reason_code = None if candidates else "EMPTY_PROVIDER_RESPONSE"
    return AdapterResult(
        provider="oracle_hcm",
        board=board,
        candidates=candidates,
        reason_code=reason_code,
        inventory_scope="title_filtered",
        inventory_complete=True,
        trace={
            "adapter": "oracle_hcm",
            "variant": "candidate_experience_public_inventory",
            "api_urls": api_urls,
            "response_sources": response_sources,
            "tenant": locator["tenant"],
            "site": locator["site"],
            "total": total,
            "candidate_count": len(candidates),
            "inventory_scope": "title_filtered",
            "inventory_complete": True,
            "stop_reason": "complete",
        },
    )


def _inventory_endpoint(locator: dict[str, Any], title: str, offset: int) -> str:
    finder = (
        f"findReqs;siteNumber={locator['site']},keyword={title},"
        f"limit={_INVENTORY_LIMIT},offset={offset}"
    )
    return urlunparse(
        (
            "https",
            locator["host"],
            "/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
            "",
            urlencode(
                {
                    "onlyData": "true",
                    "expand": "requisitionList",
                    "finder": finder,
                }
            ),
            "",
        )
    )


def _same_inventory_endpoint(url: str, expected_host: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme.casefold() == "https"
        and (parsed.hostname or "").casefold() == expected_host
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and parsed.path.rstrip("/")
        in {
            "/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
            "/hcmRestApi/resources/11.13.18.05/recruitingCEJobRequisitions",
        }
    )


def _inventory_rows(
    payload: Any,
    locator: dict[str, Any],
) -> tuple[list[dict[str, str | None]], int] | str:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return "Oracle HCM inventory envelope is invalid"
    if len(payload["items"]) != 1 or not isinstance(payload["items"][0], dict):
        return "Oracle HCM inventory search envelope is ambiguous"
    search = payload["items"][0]
    if search.get("SiteNumber") != locator["site"]:
        return "Oracle HCM inventory site conflicts with the board locator"
    total = search.get("TotalJobsCount")
    rows = search.get("requisitionList")
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total < 0
        or not isinstance(rows, list)
        or len(rows) > _INVENTORY_LIMIT
        or len(rows) > total
    ):
        return "Oracle HCM inventory counts are invalid"
    parsed: list[dict[str, str | None]] = []
    seen_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            return "Oracle HCM inventory contains a malformed row"
        opening_id = _text(row.get("Id"))
        title = _text(row.get("Title"))
        location = _text(row.get("PrimaryLocation"))
        posted_date = _text(row.get("PostedDate"))
        if (
            opening_id is None
            or not _OPENING_ID.fullmatch(opening_id)
            or opening_id.casefold() in seen_ids
            or title is None
        ):
            return "Oracle HCM inventory contains an invalid job identity"
        seen_ids.add(opening_id.casefold())
        parsed.append(
            {
                "id": opening_id,
                "title": title,
                "location": location,
                "posted_date": posted_date,
            }
        )
    return parsed, total


def _parse_candidate_url(url: str) -> dict[str, str | None] | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold()
    host_match = _HOST.fullmatch(host)
    query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or host_match is None
        or query_keys & _SENSITIVE_QUERY_KEYS
    ):
        return None
    raw_parts = [part for part in parsed.path.split("/") if part]
    try:
        parts = [unquote(part) for part in raw_parts]
    except (TypeError, ValueError):
        return None
    if any(
        decoded in {".", ".."}
        or "/" in decoded
        or "\\" in decoded
        or decoded != raw
        for raw, decoded in zip(raw_parts, parts)
    ):
        return None
    if len(parts) not in {5, 6, 7} or parts[:2] != ["hcmUI", "CandidateExperience"]:
        return None
    locale, sites, site = parts[2:5]
    if sites != "sites" or not _LOCALE.fullmatch(locale) or not _SITE.fullmatch(site):
        return None
    opening_id = None
    if len(parts) == 6:
        if parts[5] != "jobs":
            return None
    elif len(parts) == 7:
        if parts[5] != "job" or not _OPENING_ID.fullmatch(parts[6]):
            return None
        opening_id = parts[6]
    return {
        "host": host,
        "tenant": host_match.group("tenant"),
        "locale": locale,
        "site": site,
        "opening_id": opening_id,
    }


def _candidate_url(route: dict[str, Any], opening_id: str | None) -> str:
    path = (
        f"/hcmUI/CandidateExperience/{route['locale']}/sites/{route['site']}"
        + (f"/job/{opening_id}" if opening_id else "")
    )
    return urlunparse(("https", route["host"], path, "", "", ""))


def _validated_locator(board: JobBoard) -> dict[str, Any] | None:
    if board.provider != "oracle_hcm" or not board.identifier:
        return None
    try:
        locator = json.loads(board.identifier)
    except (TypeError, json.JSONDecodeError):
        return None
    allowed = {"v", "host", "tenant", "locale", "site", "detail_url", "opening_id"}
    if not isinstance(locator, dict) or not set(locator).issubset(allowed):
        return None
    required = {"v", "host", "tenant", "locale", "site"}
    if not required.issubset(locator) or locator.get("v") != _LOCATOR_VERSION:
        return None
    board_route = _parse_candidate_url(board.url)
    if board_route is None or board_route["opening_id"] is not None:
        return None
    for key in ("host", "tenant", "locale", "site"):
        if locator.get(key) != board_route.get(key):
            return None
    has_url = "detail_url" in locator
    has_id = "opening_id" in locator
    if has_url != has_id:
        return None
    if has_url:
        detail_route = _parse_candidate_url(locator["detail_url"])
        if not _same_exact_route(detail_route, locator):
            return None
    return locator


def _same_exact_route(route: dict[str, Any] | None, locator: dict[str, Any]) -> bool:
    return route is not None and all(
        route.get(key) == locator.get(key)
        for key in ("host", "tenant", "locale", "site", "opening_id")
    )


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []
        self._capturing = False
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): (value or "") for key, value in attrs}
        script_type = attributes.get("type", "").split(";", 1)[0].strip().casefold()
        if tag.casefold() == "script" and script_type == "application/ld+json":
            self._capturing = True
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._capturing:
            self.values.append("".join(self._chunks))
            self._capturing = False
            self._chunks = []


def _job_postings(html: str) -> tuple[list[dict[str, Any]], bool]:
    parser = _JsonLdParser()
    try:
        parser.feed(html)
    except (ValueError, TypeError):
        return [], True
    postings: list[dict[str, Any]] = []
    malformed = False
    for payload in parser.values:
        try:
            value = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            malformed = True
            continue
        postings.extend(_walk_job_postings(value))
    return postings, malformed


def _walk_job_postings(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [posting for item in value for posting in _walk_job_postings(item)]
    if not isinstance(value, dict):
        return []
    if _is_job_posting(value.get("@type")):
        return [value]
    return [
        posting
        for nested in value.values()
        for posting in _walk_job_postings(nested)
    ]


def _is_job_posting(value: Any) -> bool:
    if isinstance(value, str):
        return value.casefold() == "jobposting"
    return isinstance(value, list) and any(_is_job_posting(item) for item in value)


def _posting_conflict(posting: dict[str, Any], locator: dict[str, Any]) -> str | None:
    structured_url = posting.get("url")
    if structured_url is not None:
        if not isinstance(structured_url, str):
            return "Oracle HCM JobPosting URL is not a string"
        route = _parse_candidate_url(structured_url)
        if not _same_exact_route(route, locator):
            return "Oracle HCM JobPosting URL conflicts with exact detail locator"
    identifier = _structured_identifier(posting.get("identifier"))
    if identifier is not None and identifier != locator.get("opening_id"):
        return "Oracle HCM JobPosting identifier conflicts with exact detail locator"
    return None


def _structured_identifier(value: Any) -> str | None:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return str(value).strip() or None
    if isinstance(value, dict):
        for key in ("value", "identifier", "name"):
            candidate = value.get(key)
            if isinstance(candidate, (str, int)) and not isinstance(candidate, bool):
                return str(candidate).strip() or None
    return None


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalized_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _location(value: Any) -> str | None:
    if isinstance(value, list):
        locations = [location for item in value if (location := _location(item))]
        return "; ".join(dict.fromkeys(locations)) or None
    if isinstance(value, str):
        return _text(value)
    if not isinstance(value, dict):
        return None
    name = _text(value.get("name"))
    address = value.get("address")
    if isinstance(address, str):
        return _text(address) or name
    if isinstance(address, dict):
        parts = [
            _text(address.get(key))
            for key in ("addressLocality", "addressRegion", "addressCountry")
        ]
        rendered = ", ".join(part for part in parts if part)
        return rendered or name
    return name


def _expiration_state(value: Any) -> str:
    if value is None:
        return "absent"
    if not isinstance(value, str) or not value.strip():
        return "invalid"
    candidate = value.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(candidate)
    except ValueError:
        return "invalid"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return "expired" if moment < datetime.now(timezone.utc) else "current"


def _incomplete(
    board: JobBoard,
    error: str,
    *,
    reason_code: str = "PROVIDER_VARIANT_UNSUPPORTED",
    retryable: bool = False,
    **trace: Any,
) -> AdapterResult:
    return AdapterResult(
        provider="oracle_hcm",
        board=board,
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope="title_filtered",
        inventory_complete=False,
        trace={
            "adapter": "oracle_hcm",
            "variant": "candidate_experience_exact_detail",
            "error": error,
            "inventory_scope": "title_filtered",
            "inventory_complete": False,
            **trace,
        },
    )


def _empty(board: JobBoard, detail_url: str, source: str, evidence: str) -> AdapterResult:
    return AdapterResult(
        provider="oracle_hcm",
        board=board,
        reason_code="EMPTY_PROVIDER_RESPONSE",
        inventory_scope="title_filtered",
        inventory_complete=True,
        trace={
            "adapter": "oracle_hcm",
            "variant": "candidate_experience_exact_detail",
            "detail_urls": [detail_url],
            "response_source": source,
            "empty_evidence": evidence,
            "candidate_count": 0,
            "inventory_scope": "title_filtered",
            "inventory_complete": True,
        },
    )


ADAPTER = OracleHCMAdapter()
