from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlencode, urljoin, urlparse, urlunparse
from xml.etree import ElementTree

from ..reasons import REASON_SPECS, classify_fetch_error, reason_spec
from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOSTS = {"metacareers.com", "www.metacareers.com"}
_CANONICAL_HOST = "www.metacareers.com"
_BOARD_PATH = "/jobsearch/"
_SITEMAP_PATH = "/jobsearch/sitemap.xml"
_SITEMAP_URL = f"https://{_CANONICAL_HOST}{_SITEMAP_PATH}"
_GRAPHQL_URL = f"https://{_CANONICAL_HOST}/graphql"
_RESULTS_QUERY_NAME = "CareersJobSearchResultsV2DataQuery"
_RESULTS_DOC_ID = "27129360303422352"
_DETAIL_PATH = re.compile(r"^/profile/job_details/(?P<job_id>[0-9]+)/?$")
_LIST_PATH = re.compile(r"^/(?:jobs|jobsearch)/?$")
_ANONYMOUS_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "User-Agent": "AI-Job-Source-Agent/1.0",
}
_SITEMAP_HEADERS = {
    "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
    "User-Agent": "AI-Job-Source-Agent/1.0",
}
_MAX_SITEMAP_BYTES = 2_000_000
# The sitemap has no title metadata. Keep fallback sampling bounded so a single
# Meta record cannot consume the whole company deadline.
_MAX_DETAIL_PROBES = 8
_LSD_TOKEN = re.compile(r'\["LSD",\[\],\{"token":"([^"\\]+)"')


class MetaCareersAdapter:
    name = "meta_careers"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        parsed = _safe_meta_url(url)
        if parsed is None:
            return False
        return (
            _LIST_PATH.fullmatch(parsed.path) is not None
            or _DETAIL_PATH.fullmatch(parsed.path) is not None
        )

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        return JobBoard(
            url=f"https://{_CANONICAL_HOST}{_BOARD_PATH}",
            provider=self.name,
            identifier="meta",
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not _is_canonical_board(board):
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "invalid Meta Careers board"},
            )

        # Meta declares q as the keyword parameter on its public job-search route.
        # Some anonymous transports return 400 or an unhydrated shell, so this is
        # opportunistic and the official sitemap remains the evidence fallback.
        search_url = _title_search_url(board.url, query.title)
        responses: list[dict] = []
        board_failure: tuple[str, bool, int | None] | None = None
        for attempt in range(2):
            try:
                page = fetcher.fetch(search_url, headers=_ANONYMOUS_HEADERS)
            except (FetchError, OSError, TimeoutError) as error:
                reason_code, retryable = _fetch_failure(error)
                status = _error_status(error)
                if status == 400 or retryable:
                    board_failure = reason_code, retryable, status
                    break
                return _fetch_error_result(
                    board,
                    search_url,
                    error,
                    reason_code,
                    retryable,
                    attempt_count=attempt + 1,
                    responses=responses,
                )

            final_url = page.final_url or page.url
            if not _is_canonical_search_url(final_url):
                return _result(
                    board,
                    reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                    trace={
                        "adapter": self.name,
                        "board_urls": [search_url],
                        "attempt_count": attempt + 1,
                        "error": "Meta Careers search response URL rejected",
                    },
                )

            try:
                candidates, public_link_count, rejected_link_count = _extract_candidates(
                    page.html or "",
                    final_url,
                )
            except (TypeError, ValueError):
                return _unsupported_response(
                    board,
                    search_url,
                    "malformed rendered HTML",
                    attempt_count=attempt + 1,
                )
            responses.append(
                {
                    "attempt": attempt + 1,
                    "response_source": page.source,
                    "public_link_count": public_link_count,
                    "rejected_link_count": rejected_link_count,
                }
            )

            if candidates:
                return _result(
                    board,
                    candidates=candidates,
                    inventory_scope="visible_page",
                    inventory_complete=False,
                    trace={
                        "adapter": self.name,
                        "variant": (
                            "anonymous_title_search"
                            if search_url != board.url
                            else "anonymous_unfiltered_board"
                        ),
                        "board_urls": [final_url],
                        "response_source": page.source,
                        "attempt_count": attempt + 1,
                        "responses": responses,
                        "candidate_count": len(candidates),
                        "public_link_count": public_link_count,
                        "rejected_link_count": rejected_link_count,
                        "inventory_scope": "visible_page",
                        "inventory_complete": False,
                        "query_transport": (
                            "official_title_query"
                            if search_url != board.url
                            else "downstream_title_match"
                        ),
                    },
                )
            api_result = _list_graphql_jobs(
                fetcher,
                board,
                query,
                bootstrap_html=page.html or "",
                bootstrap_url=final_url,
                bootstrap_source=page.source,
            )
            if api_result is not None:
                return api_result
            if "browser" not in page.source.casefold():
                break

        return _list_sitemap_jobs(
            fetcher,
            board,
            query,
            board_failure=board_failure,
            board_responses=responses,
        )


def _list_graphql_jobs(
    fetcher,
    board: JobBoard,
    query: JobQuery,
    *,
    bootstrap_html: str,
    bootstrap_url: str,
    bootstrap_source: str,
) -> AdapterResult | None:
    token_match = _LSD_TOKEN.search(bootstrap_html)
    if token_match is None:
        return None
    token = token_match.group(1)
    if not token or len(token) > 512:
        return None

    search_input = {
        "q": _clean_text(query.title or ""),
        "divisions": [],
        "offices": [],
        "roles": [],
        "leadership_levels": [],
        "saved_jobs": [],
        "saved_searches": [],
        "sub_teams": [],
        "teams": [],
        "is_leadership": False,
        "is_remote_only": False,
        "sort_by_new": False,
        "results_per_page": None,
    }
    variables = {
        "search_input": search_input,
        "viewasUserID": None,
        "isLoggedIn": False,
    }
    body = urlencode(
        {
            "av": "0",
            "__user": "0",
            "__a": "1",
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": _RESULTS_QUERY_NAME,
            "variables": json.dumps(variables, separators=(",", ":")),
            "doc_id": _RESULTS_DOC_ID,
            "lsd": token,
        }
    ).encode("utf-8")
    headers = {
        "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": bootstrap_url,
        "User-Agent": _ANONYMOUS_HEADERS["User-Agent"],
        "X-FB-LSD": token,
    }
    try:
        response = fetcher.fetch(_GRAPHQL_URL, data=body, headers=headers)
    except (FetchError, OSError, TimeoutError) as error:
        reason_code, retryable = _fetch_failure(error)
        return _fetch_error_result(
            board,
            _GRAPHQL_URL,
            error,
            reason_code,
            retryable,
            variant="official_graphql_title_search",
            bootstrap_source=bootstrap_source,
        )

    final_url = response.final_url or response.url
    if not _is_canonical_graphql_url(final_url):
        return _unsupported_response(
            board,
            _GRAPHQL_URL,
            "Meta Careers GraphQL response URL rejected",
            variant="official_graphql_title_search",
        )
    try:
        payload = _parse_graphql_payload(response.html or "")
        raw_jobs = payload["data"]["job_search_with_featured_jobs_v2"]["all_jobs"]
        if not isinstance(raw_jobs, list):
            raise TypeError("all_jobs is not a list")
        candidates, rejected_count = _graphql_candidates(raw_jobs)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return _unsupported_response(
            board,
            _GRAPHQL_URL,
            "malformed Meta Careers GraphQL inventory",
            variant="official_graphql_title_search",
            response_source=response.source,
        )

    trace = {
        "adapter": "meta_careers",
        "variant": "official_graphql_title_search",
        "board_urls": [_GRAPHQL_URL],
        "bootstrap_source": bootstrap_source,
        "response_source": response.source,
        "query_name": _RESULTS_QUERY_NAME,
        "query_transport": "official_graphql_title_search",
        "candidate_count": len(candidates),
        "rejected_candidate_count": rejected_count,
        "inventory_scope": "filtered_query",
        "inventory_complete": True,
        "absence_established": True,
    }
    return _result(
        board,
        candidates=candidates,
        inventory_scope="filtered_query",
        inventory_complete=True,
        trace=trace,
    )


def _parse_graphql_payload(body: str) -> dict:
    cleaned = body.lstrip()
    if cleaned.startswith("for (;;);"):
        cleaned = cleaned[len("for (;;);") :].lstrip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict) or payload.get("errors"):
        raise ValueError("Meta Careers GraphQL returned errors")
    return payload


def _graphql_candidates(raw_jobs: list) -> tuple[list[JobCandidate], int]:
    candidates: list[JobCandidate] = []
    seen: set[str] = set()
    rejected = 0
    for raw_job in raw_jobs:
        if not isinstance(raw_job, dict):
            rejected += 1
            continue
        job_id = raw_job.get("id")
        title = raw_job.get("title")
        if (
            not isinstance(job_id, str)
            or not job_id.isdigit()
            or not isinstance(title, str)
            or not _clean_text(title)
            or job_id in seen
        ):
            rejected += 1
            continue
        locations = raw_job.get("locations")
        if locations is not None and not isinstance(locations, list):
            rejected += 1
            continue
        cleaned_locations = []
        for location in locations or []:
            if not isinstance(location, str):
                rejected += 1
                cleaned_locations = []
                break
            cleaned = _clean_text(location)
            if cleaned and cleaned not in cleaned_locations:
                cleaned_locations.append(cleaned)
        else:
            seen.add(job_id)
            candidates.append(
                JobCandidate(
                    title=_clean_text(title),
                    url=f"https://{_CANONICAL_HOST}/profile/job_details/{job_id}",
                    provider="meta_careers",
                    location=" + ".join(cleaned_locations) or None,
                    raw={
                        "job_id": job_id,
                        "evidence": "official_graphql_inventory",
                        "hiring_organization": "Meta",
                    },
                )
            )
    return candidates, rejected


class _MetaJobsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.job_links: list[tuple[str, str, str]] = []
        self._href: str | None = None
        self._depth = 0
        self._heading_depth = 0
        self._title_text: list[str] = []
        self._associated_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._href is not None:
            self._depth += 1
            if tag.casefold() in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self._heading_depth += 1
            return
        if tag.casefold() != "a":
            return
        attributes = {key.casefold(): value or "" for key, value in attrs}
        self._href = attributes.get("href")
        self._depth = 0
        self._heading_depth = 0
        self._title_text = []
        self._associated_text = []

    def handle_data(self, data: str) -> None:
        if self._href is None:
            return
        if self._depth == 0 or self._heading_depth:
            self._title_text.append(data)
        else:
            self._associated_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._href is None:
            return
        if tag.casefold() == "a" and self._depth == 0:
            self.job_links.append(
                (
                    self._href,
                    _clean_text("".join(self._title_text)),
                    _clean_text("".join(self._associated_text)),
                )
            )
            self._href = None
            self._heading_depth = 0
            self._title_text = []
            self._associated_text = []
            return
        if tag.casefold() in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_depth = max(0, self._heading_depth - 1)
        if self._depth > 0:
            self._depth -= 1


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.documents: list[str] = []
        self._capturing = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script" or self._capturing:
            return
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if attributes.get("type", "").casefold() == "application/ld+json":
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


def _extract_candidates(
    html: str,
    base_url: str,
) -> tuple[list[JobCandidate], int, int]:
    parser = _MetaJobsParser()
    parser.feed(html)
    candidates: list[JobCandidate] = []
    seen: set[str] = set()
    rejected_link_count = 0
    for href, title, location in parser.job_links:
        detail = _canonical_detail_url(href, base_url)
        if detail is None or not title:
            rejected_link_count += 1
            continue
        detail_url, job_id = detail
        if detail_url in seen:
            continue
        seen.add(detail_url)
        candidates.append(
            JobCandidate(
                title=title,
                url=detail_url,
                provider="meta_careers",
                location=location or None,
                raw={"job_id": job_id},
            )
        )
    return candidates, len(parser.job_links), rejected_link_count


def _list_sitemap_jobs(
    fetcher,
    board: JobBoard,
    query: JobQuery,
    *,
    board_failure: tuple[str, bool, int | None] | None,
    board_responses: list[dict],
) -> AdapterResult:
    board_trace = {
        "board_responses": board_responses,
        "public_link_count": sum(
            item.get("public_link_count", 0) for item in board_responses
        ),
        "rejected_link_count": sum(
            item.get("rejected_link_count", 0) for item in board_responses
        ),
    }
    try:
        sitemap_page = fetcher.fetch(_SITEMAP_URL, headers=_SITEMAP_HEADERS)
    except (FetchError, OSError, TimeoutError) as error:
        reason_code, retryable = _fetch_failure(error)
        if board_failure is not None and _error_status(error) == 400:
            reason_code, retryable, _ = board_failure
        return _fetch_error_result(
            board,
            _SITEMAP_URL,
            error,
            reason_code,
            retryable,
            variant="public_sitemap_jobposting",
            **board_trace,
        )

    final_url = sitemap_page.final_url or sitemap_page.url
    if not _is_canonical_sitemap_url(final_url):
        return _unsupported_response(
            board,
            _SITEMAP_URL,
            "Meta Careers sitemap response URL rejected",
            variant="public_sitemap_jobposting",
            **board_trace,
        )

    try:
        detail_urls, rejected_url_count = _extract_sitemap_detail_urls(
            sitemap_page.html or ""
        )
    except (ElementTree.ParseError, TypeError, ValueError):
        return _unsupported_response(
            board,
            _SITEMAP_URL,
            "malformed Meta Careers sitemap",
            variant="public_sitemap_jobposting",
            **board_trace,
        )

    candidates: list[JobCandidate] = []
    detail_failure_count = 0
    detail_failure_reasons: list[tuple[str, bool]] = []
    malformed_detail_count = 0
    probe_count = 0
    target_title = _normalized_title(query.title)
    selected_detail_urls, selection_strategy = _select_sitemap_detail_urls(
        detail_urls,
        target_title,
        _MAX_DETAIL_PROBES,
    )
    for detail_url in selected_detail_urls:
        probe_count += 1
        try:
            detail_page = fetcher.fetch(detail_url, headers=_ANONYMOUS_HEADERS)
        except (FetchError, OSError, TimeoutError) as error:
            detail_failure_count += 1
            detail_failure_reasons.append(_fetch_failure(error))
            continue
        final_detail_url = detail_page.final_url or detail_page.url
        canonical = _canonical_detail_url(final_detail_url, detail_url)
        if canonical is None or canonical[0] != detail_url:
            malformed_detail_count += 1
            continue
        candidate = _extract_jobposting_candidate(detail_page.html or "", canonical)
        if candidate is None:
            malformed_detail_count += 1
            continue
        candidates.append(candidate)
        if target_title and _normalized_title(candidate.title) == target_title:
            break

    trace = {
        "adapter": "meta_careers",
        "variant": "public_sitemap_jobposting",
        "board_urls": [_SITEMAP_URL],
        "response_source": sitemap_page.source,
        **board_trace,
        "sitemap_url_count": len(detail_urls),
        "rejected_url_count": rejected_url_count,
        "detail_probe_limit": _MAX_DETAIL_PROBES,
        "detail_probe_count": probe_count,
        "detail_selection_strategy": selection_strategy,
        "detail_selection_count": len(selected_detail_urls),
        "detail_failure_count": detail_failure_count,
        "detail_failure_reason_codes": sorted(
            {reason_code for reason_code, _ in detail_failure_reasons}
        ),
        "malformed_detail_count": malformed_detail_count,
        "candidate_count": len(candidates),
        "inventory_scope": "visible_page",
        "inventory_complete": False,
        "query_transport": "bounded_title_directed_sitemap_probe",
        "absence_established": False,
    }
    if candidates:
        return _result(
            board,
            candidates=candidates,
            inventory_scope="visible_page",
            inventory_complete=False,
            trace=trace,
        )
    retryable_failures = [
        (reason_code, retryable)
        for reason_code, retryable in detail_failure_reasons
        if retryable
    ]
    if retryable_failures:
        reason_code = retryable_failures[0][0]
        return _result(
            board,
            reason_code=reason_code,
            retryable=True,
            trace=trace,
        )
    return _unsupported_response(
        board,
        _SITEMAP_URL,
        "missing public Meta JobPosting evidence",
        **trace,
    )


def _extract_sitemap_detail_urls(xml: str) -> tuple[list[str], int]:
    if len(xml.encode("utf-8")) > _MAX_SITEMAP_BYTES:
        raise ValueError("Meta Careers sitemap exceeds size limit")
    root = ElementTree.fromstring(xml)
    if _local_name(root.tag) != "urlset":
        raise ValueError("Meta Careers sitemap root is not urlset")
    detail_urls: list[str] = []
    seen: set[str] = set()
    rejected = 0
    for element in root.iter():
        if _local_name(element.tag) != "loc":
            continue
        detail = _canonical_detail_url(element.text or "", _SITEMAP_URL)
        if detail is None:
            rejected += 1
            continue
        detail_url, _ = detail
        if detail_url in seen:
            continue
        seen.add(detail_url)
        detail_urls.append(detail_url)
    if not detail_urls:
        raise ValueError("Meta Careers sitemap has no safe detail URLs")
    return detail_urls, rejected


def _select_sitemap_detail_urls(
    detail_urls: list[str],
    target_title: str,
    limit: int,
) -> tuple[list[str], str]:
    if len(detail_urls) <= limit:
        return list(detail_urls), "complete_sitemap_order"
    if not target_title:
        return detail_urls[:limit], "bounded_sitemap_prefix"

    # Numeric sitemap URLs carry no title metadata. A title-seeded offset in each
    # stratum removes the permanent prefix bias while preserving a strict fetch
    # bound and deterministic replay for the same query and sitemap.
    digest = hashlib.sha256(target_title.encode("utf-8")).digest()
    selected: list[str] = []
    count = len(detail_urls)
    for stratum in range(limit):
        start = stratum * count // limit
        end = (stratum + 1) * count // limit
        seed_offset = (stratum * 4) % len(digest)
        seed = int.from_bytes(digest[seed_offset : seed_offset + 4], "big")
        selected.append(detail_urls[start + seed % (end - start)])
    return selected, "title_seeded_stratified_sitemap"


def _extract_jobposting_candidate(
    html: str,
    canonical: tuple[str, str],
) -> JobCandidate | None:
    parser = _JsonLdParser()
    parser.feed(html)
    detail_url, job_id = canonical
    for document in parser.documents:
        try:
            payload = json.loads(document)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        for posting in _jobposting_objects(payload):
            title = posting.get("title")
            if (
                not isinstance(title, str)
                or not _clean_text(title)
                or not _is_meta_hiring_organization(posting)
            ):
                continue
            return JobCandidate(
                title=_clean_text(title),
                url=detail_url,
                provider="meta_careers",
                location=_jobposting_location(posting),
                raw={
                    "job_id": job_id,
                    "evidence": "schema_org_jobposting",
                    "hiring_organization": "Meta",
                },
            )
    return None


def _jobposting_objects(value):
    if isinstance(value, list):
        for item in value:
            yield from _jobposting_objects(item)
        return
    if not isinstance(value, dict):
        return
    item_type = value.get("@type")
    types = item_type if isinstance(item_type, list) else [item_type]
    if any(isinstance(item, str) and item.casefold() == "jobposting" for item in types):
        yield value
    graph = value.get("@graph")
    if isinstance(graph, (dict, list)):
        yield from _jobposting_objects(graph)


def _jobposting_location(posting: dict) -> str | None:
    raw_locations = posting.get("jobLocation")
    locations = raw_locations if isinstance(raw_locations, list) else [raw_locations]
    names: list[str] = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        name = location.get("name")
        if isinstance(name, str):
            cleaned = _clean_text(name)
            if cleaned and cleaned not in names:
                names.append(cleaned)
    return " + ".join(names) or None


def _is_meta_hiring_organization(posting: dict) -> bool:
    organization = posting.get("hiringOrganization")
    if not isinstance(organization, dict):
        return False
    name = organization.get("name")
    if not isinstance(name, str) or _clean_text(name).casefold() != "meta":
        return False
    same_as = organization.get("sameAs")
    if not isinstance(same_as, str):
        return False
    try:
        parsed = urlparse(same_as)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme.casefold() == "https"
        and (parsed.hostname or "").casefold() in {"meta.com", "www.meta.com"}
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _normalized_title(value: str | None) -> str:
    return _clean_text(value or "").casefold()


def _title_search_url(board_url: str, title: str | None) -> str:
    cleaned = _clean_text(title or "")
    if not cleaned:
        return board_url
    return f"{board_url}?{urlencode({'q': cleaned})}"


def _safe_meta_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (AttributeError, TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or host not in _HOSTS
    ):
        return None
    return parsed


def _is_canonical_board(board: JobBoard) -> bool:
    return (
        board.provider == "meta_careers"
        and board.identifier == "meta"
        and board.url == f"https://{_CANONICAL_HOST}{_BOARD_PATH}"
    )


def _is_canonical_search_url(url: str) -> bool:
    parsed = _safe_meta_url(url)
    return (
        parsed is not None
        and (parsed.hostname or "").casefold() == _CANONICAL_HOST
        and parsed.path == _BOARD_PATH
    )


def _is_canonical_sitemap_url(url: str) -> bool:
    parsed = _safe_meta_url(url)
    return (
        parsed is not None
        and (parsed.hostname or "").casefold() == _CANONICAL_HOST
        and parsed.path == _SITEMAP_PATH
        and not parsed.query
        and not parsed.fragment
    )


def _is_canonical_graphql_url(url: str) -> bool:
    parsed = _safe_meta_url(url)
    return (
        parsed is not None
        and (parsed.hostname or "").casefold() == _CANONICAL_HOST
        and parsed.path == "/graphql"
        and not parsed.query
        and not parsed.fragment
    )


def _canonical_detail_url(href: str, base_url: str) -> tuple[str, str] | None:
    try:
        resolved = urljoin(base_url, href)
    except (AttributeError, TypeError, ValueError):
        return None
    parsed = _safe_meta_url(resolved)
    if parsed is None:
        return None
    match = _DETAIL_PATH.fullmatch(parsed.path)
    if match is None:
        return None
    job_id = match.group("job_id")
    path = f"/profile/job_details/{job_id}"
    return urlunparse(("https", _CANONICAL_HOST, path, "", "", "")), job_id


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _fetch_failure(error: Exception) -> tuple[str, bool]:
    typed = getattr(error, "reason_code", None)
    status_reason = _status_reason(_error_status(error))
    if (
        isinstance(typed, str)
        and typed in REASON_SPECS
        and (typed not in {"FETCH_FAILED", "PROVIDER_FETCH_FAILED"} or not status_reason)
    ):
        reason_code = typed
    elif status_reason:
        reason_code = status_reason
    else:
        classified = classify_fetch_error(str(error))
        reason_code = (
            "PROVIDER_FETCH_FAILED" if classified == "FETCH_FAILED" else classified
        )

    typed_retryable = getattr(error, "retryable", None)
    retryable = (
        typed_retryable
        if isinstance(typed_retryable, bool) and typed == reason_code
        else reason_spec(reason_code).retryable
    )
    return reason_code, retryable


def _error_status(error: Exception) -> int | None:
    status = getattr(error, "status", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _status_reason(status: int | None) -> str | None:
    if status == 400:
        return "PROVIDER_VARIANT_UNSUPPORTED"
    if status == 401:
        return "LOGIN_REQUIRED"
    if status == 403:
        return "HTTP_FORBIDDEN"
    if status == 404:
        return "HTTP_NOT_FOUND"
    if status == 429:
        return "RATE_LIMITED"
    if status is not None and 500 <= status <= 599:
        return "SERVER_ERROR"
    return None


def _fetch_error_result(
    board: JobBoard,
    request_url: str,
    error: Exception,
    reason_code: str,
    retryable: bool,
    **trace_values,
) -> AdapterResult:
    return _result(
        board,
        reason_code=reason_code,
        retryable=retryable,
        trace={
            "adapter": "meta_careers",
            "variant": "anonymous_unfiltered_board",
            "board_urls": [request_url],
            "error_type": type(error).__name__,
            "error_status": _error_status(error),
            "error_classification": reason_code,
            "query_transport": "downstream_title_match",
            **trace_values,
        },
    )


def _unsupported_response(
    board: JobBoard,
    board_url: str,
    error: str,
    **trace_values,
) -> AdapterResult:
    return _result(
        board,
        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
        trace={
            "adapter": "meta_careers",
            "board_urls": [board_url],
            "error": error,
            **trace_values,
        },
    )


def _result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None = None,
    retryable: bool = False,
    trace: dict | None = None,
    inventory_scope: str = "unknown",
    inventory_complete: bool = False,
) -> AdapterResult:
    return AdapterResult(
        provider="meta_careers",
        board=board,
        candidates=candidates or [],
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope=inventory_scope,
        inventory_complete=inventory_complete,
        trace=trace or {},
    )


ADAPTER = MetaCareersAdapter()
