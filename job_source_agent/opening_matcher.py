from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse, urlunparse

from .providers import DEFAULT_PROVIDER_REGISTRY, JobQuery, ProviderRegistry
from .listing_extraction import extract_listing_candidates, validate_output_url
from .scoring import is_likely_job_detail, score_job_link
from .web import FetchError, Fetcher, Page, RawLink, extract_links, safe_normalize_url
from .website_resolver import location_region


STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "for",
    "i",
    "ii",
    "iii",
    "in",
    "of",
    "the",
    "to",
}

MIN_TITLE_MATCH_SCORE = 45
MIN_PROVIDER_TITLE_MATCH_SCORE = 65
SEARCH_FIELD_NAMES = {"q", "query", "keyword", "keywords", "search", "searchkeyword"}
MAX_DISCOVERED_SEARCH_FORMS = 4
SENSITIVE_FORM_QUERY_NAMES = {
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


@dataclass
class OpeningMatch:
    url: str
    title: str
    score: int
    provider: str
    reasons: list[str]
    job_list_page_url: str | None = None
    location_score: int = 0


@dataclass
class ProviderApiRequest:
    url: str
    data: bytes | None = None
    headers: dict[str, str] | None = None


class JobOpeningMatcher:
    def __init__(self, fetcher: Fetcher, provider_registry: ProviderRegistry | None = None) -> None:
        self.fetcher = fetcher
        self.provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY

    def match(
        self,
        job_list_url: str,
        target_title: str | None,
        target_location: str | None = None,
    ) -> tuple[OpeningMatch | None, dict]:
        trace = {
            "job_list_url": job_list_url,
            "target_title": target_title,
            "target_location": target_location,
            "provider": self.provider_registry.detect(job_list_url),
            "searched_urls": [],
            "candidates": [],
        }
        if not target_title:
            return None, trace

        api_match, api_trace, landing_page = self._match_provider_api(
            job_list_url,
            target_title,
            target_location,
        )
        trace["provider_api"] = api_trace
        if api_trace.get("provider") and api_trace["provider"] != "generic":
            trace["provider"] = api_trace["provider"]
        if api_match:
            trace["selected"] = {
                "url": api_match.url,
                "title": api_match.title,
                "score": api_match.score,
                "reasons": api_match.reasons,
            }
            return api_match, trace

        search_plan = _build_search_plan(job_list_url, target_title, landing_page)
        trace["search_plan"] = [
            {"url": search_url, "source": source}
            for search_url, _page, source in search_plan
        ]
        for search_url, reusable_page, _source in search_plan:
            trace["searched_urls"].append(search_url)
            if reusable_page is not None:
                page = reusable_page
            else:
                try:
                    page = self.fetcher.fetch(search_url)
                except FetchError as exc:
                    trace.setdefault("errors", []).append({"url": search_url, "error": str(exc)})
                    continue

            page_url = page.final_url or page.url
            candidates = []
            links = extract_links(page) + structured_job_links(page.html, page_url)
            for link in dedupe_raw_links(links):
                validated_url = validate_output_url(link.url, page_url, title=link.text)
                if not validated_url:
                    continue
                link = RawLink(validated_url, link.text, link.source_url, link.origin)
                scored = score_job_link(link, page_url)
                title_score, title_reasons = score_title_match(link.text, target_title)
                if title_score < MIN_TITLE_MATCH_SCORE:
                    continue
                total_score = scored.score + title_score
                reasons = scored.reasons + title_reasons + [f"listing origin: {link.origin}"]
                if not is_likely_job_detail(scored) and title_score < 60:
                    continue
                if total_score < 70:
                    continue
                candidates.append(
                    OpeningMatch(
                        url=link.url,
                        title=link.text,
                        score=total_score,
                        provider=trace["provider"],
                        reasons=reasons,
                        job_list_page_url=page_url,
                    )
                )

            candidates.sort(key=lambda candidate: candidate.score, reverse=True)
            trace["candidates"].extend(
                [
                    {
                        "url": candidate.url,
                        "title": candidate.title,
                        "score": candidate.score,
                        "reasons": candidate.reasons,
                    }
                    for candidate in candidates[:8]
                ]
            )
            if candidates:
                trace["selected"] = {
                    "url": candidates[0].url,
                    "title": candidates[0].title,
                    "score": candidates[0].score,
                    "reasons": candidates[0].reasons,
                }
                return candidates[0], trace

        fallback_url = build_search_result_url(job_list_url, target_title)
        if fallback_url:
            trace["fallback_search_url"] = fallback_url
        return None, trace

    def _match_provider_api(
        self,
        job_list_url: str,
        target_title: str,
        target_location: str | None = None,
    ) -> tuple[OpeningMatch | None, dict, Page | None]:
        provider = self.provider_registry.detect(job_list_url)
        adapter = self.provider_registry.adapter_for(job_list_url)
        board = adapter.identify_board(job_list_url) if adapter else None
        page_detection = None
        landing_page = None
        if adapter is None:
            try:
                landing_page = self.fetcher.fetch(job_list_url)
            except FetchError as exc:
                page_detection = {"method": "page_evidence", "error": str(exc)}
            else:
                identified = self.provider_registry.board_for_page(landing_page, self.fetcher)
                if identified is not None:
                    adapter, board = identified
                    provider = adapter.name
                    page_detection = {
                        "method": "page_evidence",
                        "provider": provider,
                        "url": board.url,
                    }
        if adapter:
            if board:
                try:
                    adapter_result = adapter.list_jobs(
                        self.fetcher,
                        board,
                        JobQuery(title=target_title, location=target_location),
                    )
                except FetchError as exc:
                    if adapter.supports_listing:
                        failure_trace = {
                            "provider": provider,
                            "adapter": adapter.name,
                            "api_urls": [],
                            "candidates": [],
                            "errors": [{"url": job_list_url, "error": str(exc)}],
                        }
                        if page_detection is not None:
                            failure_trace["provider_detection"] = page_detection
                        return None, failure_trace, landing_page
                else:
                    if adapter_result.reason_code == "PROVIDER_VARIANT_UNSUPPORTED":
                        unsupported_trace = {
                            "provider": provider,
                            "adapter": adapter.name,
                            "api_urls": list(adapter_result.trace.get("api_urls", [])),
                            "candidates": [],
                            "adapter_trace": adapter_result.trace,
                            "inventory": {
                                "source": "native_adapter",
                                "status": "incomplete",
                                "scope": adapter_result.trace.get(
                                    "inventory_scope",
                                    adapter_result.inventory_scope,
                                ),
                                "complete": False,
                                "candidate_count": 0,
                                "strongest_title_score": 0,
                                "reason_code": adapter_result.reason_code,
                            },
                        }
                        if page_detection is not None:
                            unsupported_trace["provider_detection"] = page_detection
                        return None, unsupported_trace, landing_page
                    else:
                        inventory_scope = adapter_result.trace.get(
                            "inventory_scope",
                            adapter_result.inventory_scope,
                        )
                        adapter_errors = any(
                            isinstance(adapter_result.trace.get(key), list)
                            and bool(adapter_result.trace[key])
                            for key in ("errors", "page_errors")
                        )
                        inventory_complete = bool(
                            adapter_result.inventory_complete
                            and not adapter_result.retryable
                            and adapter_result.reason_code in {None, "EMPTY_PROVIDER_RESPONSE"}
                            and not adapter_errors
                        )
                        scored_titles = [
                            score_title_match(candidate.title, target_title)[0]
                            for candidate in adapter_result.candidates
                        ]
                        trace = {
                            "provider": provider,
                            "adapter": adapter.name,
                            "api_urls": list(adapter_result.trace.get("api_urls", [])),
                            "candidates": [],
                            "adapter_trace": adapter_result.trace,
                            "inventory": {
                                "source": "native_adapter",
                                "status": (
                                    "incomplete"
                                    if not inventory_complete
                                    else
                                    "verified"
                                    if adapter_result.candidates
                                    else "verified_filtered_empty"
                                    if (
                                        adapter_result.reason_code == "EMPTY_PROVIDER_RESPONSE"
                                        and inventory_scope == "title_filtered"
                                    )
                                    else "verified_empty"
                                    if adapter_result.reason_code == "EMPTY_PROVIDER_RESPONSE"
                                    else "incomplete"
                                ),
                                "scope": inventory_scope,
                                "complete": inventory_complete,
                                "candidate_count": len(adapter_result.candidates),
                                "strongest_title_score": max(scored_titles, default=0),
                                "reason_code": adapter_result.reason_code,
                            },
                        }
                        if page_detection is not None:
                            trace["provider_detection"] = page_detection
                        scored = []
                        for candidate in adapter_result.candidates:
                            title_score, title_reasons = score_title_match(candidate.title, target_title)
                            if title_score < MIN_PROVIDER_TITLE_MATCH_SCORE:
                                continue
                            location_score, location_reasons = score_location_match(
                                candidate.location,
                                target_location,
                            )
                            scored.append(
                                OpeningMatch(
                                    url=candidate.url,
                                    title=candidate.title,
                                    score=title_score + 100,
                                    provider=candidate.provider,
                                    reasons=(
                                        ["provider adapter result"]
                                        + title_reasons
                                        + location_reasons
                                    ),
                                    job_list_page_url=job_list_url,
                                    location_score=location_score,
                                )
                            )
                        scored.sort(
                            key=lambda candidate: (candidate.score, candidate.location_score),
                            reverse=True,
                        )
                        trace["candidates"] = [
                            {
                                "url": candidate.url,
                                "title": candidate.title,
                                "score": candidate.score,
                                "reasons": candidate.reasons,
                            }
                            for candidate in scored[:8]
                        ]
                        return (scored[0] if scored else None), trace, landing_page

        api_requests = build_provider_api_requests(job_list_url, target_title)
        trace = {"provider": provider, "api_urls": [request.url for request in api_requests], "candidates": []}
        if page_detection and page_detection.get("error"):
            trace["errors"] = [
                {
                    "url": job_list_url,
                    "error": page_detection["error"],
                    "phase": "page_evidence",
                }
            ]
        successful_api_fetches = 0
        inventory_candidate_count = 0
        strongest_title_score = 0
        for api_request in api_requests:
            try:
                page = self.fetcher.fetch(api_request.url, data=api_request.data, headers=api_request.headers)
            except FetchError as exc:
                trace.setdefault("errors", []).append({"url": api_request.url, "error": str(exc)})
                continue
            successful_api_fetches += 1
            candidates = provider_api_candidates(provider, page.html, job_list_url)
            inventory_candidate_count += len(candidates)
            scored = []
            for title, url in candidates:
                title_score, title_reasons = score_title_match(title, target_title)
                strongest_title_score = max(strongest_title_score, title_score)
                if title_score < MIN_TITLE_MATCH_SCORE:
                    continue
                scored.append(
                    OpeningMatch(
                        url=url,
                        title=title,
                        score=title_score + 100,
                        provider=provider,
                        reasons=["provider API result"] + title_reasons,
                        job_list_page_url=job_list_url,
                    )
                )
            scored.sort(key=lambda candidate: candidate.score, reverse=True)
            trace["candidates"].extend(
                [
                    {
                        "url": candidate.url,
                        "title": candidate.title,
                        "score": candidate.score,
                        "reasons": candidate.reasons,
                    }
                    for candidate in scored[:8]
                ]
            )
            if scored:
                trace["inventory"] = {
                    "source": "provider_api",
                    "status": "verified",
                    "candidate_count": inventory_candidate_count,
                    "strongest_title_score": strongest_title_score,
                }
                return scored[0], trace, landing_page
        if successful_api_fetches:
            trace["inventory"] = {
                "source": "provider_api",
                "status": "verified" if inventory_candidate_count else "verified_empty",
                "candidate_count": inventory_candidate_count,
                "strongest_title_score": strongest_title_score,
            }
        return None, trace, landing_page


@dataclass(frozen=True)
class _SearchForm:
    action: str
    method: str
    field_name: str


class _SearchFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[_SearchForm] = []
        self._action: str | None = None
        self._method = "get"
        self._field_name: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag_name == "form":
            self._action = attributes.get("action", "")
            self._method = attributes.get("method", "get").casefold()
            self._field_name = None
            return
        if tag_name != "input" or self._action is None or self._field_name is not None:
            return
        input_type = attributes.get("type", "text").casefold()
        field_name = attributes.get("name", "")
        if input_type in {"", "search", "text"} and field_name.casefold() in SEARCH_FIELD_NAMES:
            self._field_name = field_name

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "form" or self._action is None:
            return
        if self._field_name and len(self.forms) < MAX_DISCOVERED_SEARCH_FORMS:
            self.forms.append(_SearchForm(self._action, self._method, self._field_name))
        self._action = None
        self._method = "get"
        self._field_name = None


def build_search_form_urls(page: Page, target_title: str) -> list[str]:
    """Build bounded, same-host GET searches declared by a public listing page."""

    page_url = page.final_url or page.url
    parser = _SearchFormParser()
    try:
        parser.feed(page.html or "")
    except (TypeError, ValueError):
        return []

    urls: list[str] = []
    seen: set[str] = set()
    for form in parser.forms:
        if form.method != "get":
            continue
        normalized = safe_normalize_url(form.action or page_url, page_url)
        if not normalized:
            continue
        try:
            parsed = urlparse(normalized)
            page_parsed = urlparse(page_url)
            port = parsed.port
            page_port = page_parsed.port
        except (TypeError, ValueError):
            continue
        if (
            parsed.scheme != "https"
            or page_parsed.scheme != "https"
            or not parsed.hostname
            or parsed.hostname.casefold() != (page_parsed.hostname or "").casefold()
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or page_port not in {None, 443}
        ):
            continue
        existing = parse_qs(parsed.query, keep_blank_values=True)
        if any(key.casefold() in SENSITIVE_FORM_QUERY_NAMES for key in existing):
            continue
        query_items = [
            (key, value)
            for key, values in existing.items()
            if key.casefold() != form.field_name.casefold()
            for value in values
        ]
        query_items.append((form.field_name, target_title))
        search_url = urlunparse(parsed._replace(query=urlencode(query_items)))
        if search_url not in seen:
            seen.add(search_url)
            urls.append(search_url)
    return urls


def _build_search_plan(
    job_list_url: str,
    target_title: str,
    landing_page: Page | None,
) -> list[tuple[str, Page | None, str]]:
    plan: list[tuple[str, Page | None, str]] = []
    seen: set[str] = set()

    def append(url: str, page: Page | None, source: str) -> None:
        normalized = safe_normalize_url(url)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        plan.append((url, page, source))

    if landing_page is not None:
        append(job_list_url, landing_page, "reused_landing_page")
        final_url = landing_page.final_url or landing_page.url
        final_normalized = safe_normalize_url(final_url)
        if final_normalized:
            seen.add(final_normalized)
        for form_url in build_search_form_urls(landing_page, target_title):
            append(form_url, None, "declared_get_form")

    for search_url in build_provider_search_urls(job_list_url, target_title):
        append(search_url, None, "provider_fallback")
    return plan


def detect_provider(url: str) -> str:
    return DEFAULT_PROVIDER_REGISTRY.detect(url)


def score_location_match(
    candidate_location: str | None,
    target_location: str | None,
) -> tuple[int, list[str]]:
    """Use location only to break title ties; missing location never rejects a job."""

    if not candidate_location or not target_location:
        return 0, []
    candidate_normalized = " ".join(re.findall(r"[a-z0-9]+", candidate_location.casefold()))
    target_normalized = " ".join(re.findall(r"[a-z0-9]+", target_location.casefold()))
    if not candidate_normalized or not target_normalized:
        return 0, []

    score = 0
    reasons: list[str] = []
    if candidate_normalized == target_normalized:
        score += 20
        reasons.append("exact location match")
    else:
        overlap = set(candidate_normalized.split()) & set(target_normalized.split())
        if overlap:
            score += min(12, 4 * len(overlap))
            reasons.append("location token overlap")

    candidate_region = location_region(candidate_location)
    target_region = location_region(target_location)
    if candidate_region and candidate_region == target_region:
        score += 8
        reasons.append(f"location region match '{target_region}'")
    return score, reasons


def build_provider_search_urls(job_list_url: str, target_title: str) -> list[str]:
    query = quote_plus(target_title)
    provider = detect_provider(job_list_url)
    parsed = urlparse(job_list_url)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
    if provider == "google_careers":
        return [
            f"https://www.google.com/about/careers/applications/jobs/results/?q={query}",
            job_list_url,
        ]
    if provider == "meta_careers":
        return [
            f"https://www.metacareers.com/jobs/?q={query}",
            job_list_url,
        ]
    if provider in {"greenhouse", "lever", "ashby"}:
        return [job_list_url, add_query_params(job_list_url, {"q": target_title})]
    if provider == "workable":
        return [job_list_url, add_query_params(job_list_url, {"query": target_title})]
    if provider == "smartrecruiters":
        return [job_list_url, add_query_params(job_list_url, {"search": target_title})]
    if provider == "icims":
        return [
            job_list_url,
            add_query_params(job_list_url, {"ss": "1", "searchKeyword": target_title}),
        ]
    if provider == "workday":
        return [job_list_url, add_query_params(job_list_url, {"q": target_title})]
    if provider == "successfactors":
        return [
            job_list_url,
            add_query_params(job_list_url, {"q": target_title}),
            add_query_params(job_list_url, {"keyword": target_title}),
        ]
    if provider == "rippling":
        return [job_list_url]
    if provider == "bamboohr":
        return [job_list_url]
    return [job_list_url, f"{base}?q={query}", f"{base}?search={query}"]


def build_provider_api_urls(job_list_url: str) -> list[str]:
    return [request.url for request in build_provider_api_requests(job_list_url)]


def build_provider_api_requests(job_list_url: str, target_title: str | None = None) -> list[ProviderApiRequest]:
    provider = detect_provider(job_list_url)
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    if provider == "greenhouse" and parts:
        board = parts[0]
        return [ProviderApiRequest(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")]
    if provider == "lever" and parts:
        company = parts[0]
        return [ProviderApiRequest(f"https://api.lever.co/v0/postings/{company}?mode=json")]
    if provider == "smartrecruiters" and parts:
        company = parts[0]
        return [ProviderApiRequest(f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100")]
    if provider == "ashby":
        board = _ashby_board_name(job_list_url)
        if board:
            return [ProviderApiRequest(f"https://api.ashbyhq.com/posting-api/job-board/{board}")]
    if provider == "workday":
        workday_api_url = build_workday_api_url(job_list_url)
        if workday_api_url:
            payload = {
                "appliedFacets": {},
                "limit": 50,
                "offset": 0,
                "searchText": target_title or "",
            }
            return [ProviderApiRequest(workday_api_url, data=json.dumps(payload).encode("utf-8"))]
    if provider == "bamboohr":
        return [ProviderApiRequest(_bamboohr_jobs_api_url(job_list_url))]
    return []


def provider_api_candidates(provider: str, body: str, job_list_url: str) -> list[tuple[str, str]]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    if provider == "greenhouse":
        return [
            (str(job.get("title") or ""), str(job.get("absolute_url") or ""))
            for job in data.get("jobs", [])
            if job.get("title") and job.get("absolute_url")
        ]
    if provider == "lever" and isinstance(data, list):
        return [
            (str(job.get("text") or ""), str(job.get("hostedUrl") or job.get("applyUrl") or ""))
            for job in data
            if job.get("text") and (job.get("hostedUrl") or job.get("applyUrl"))
        ]
    if provider == "smartrecruiters":
        candidates = []
        for job in data.get("content", []):
            title = str(job.get("name") or "")
            url = _smartrecruiters_job_url(job, job_list_url)
            if title and url:
                candidates.append((title, url))
        return candidates
    if provider == "workday":
        candidates = []
        for job in data.get("jobPostings", []):
            title = str(job.get("title") or "")
            url = _workday_job_url(job, job_list_url)
            if title and url:
                candidates.append((title, url))
        return candidates
    if provider == "ashby":
        candidates = []
        for job in data.get("jobs", []):
            title = str(job.get("title") or "")
            url = _ashby_job_url(job, job_list_url)
            if title and url:
                candidates.append((title, url))
        return candidates
    if provider == "bamboohr":
        candidates = []
        for job in data.get("result", []):
            title = str(job.get("jobOpeningName") or "")
            url = _bamboohr_job_url(job, job_list_url)
            if title and url:
                candidates.append((title, url))
        return candidates
    return []


def structured_job_links(html: str, source_url: str) -> list[RawLink]:
    links: list[RawLink] = []
    for script_attrs, script_body in _script_blocks(html):
        if "application/ld+json" not in script_attrs.lower():
            continue
        try:
            data = json.loads(unescape(script_body.strip()))
        except json.JSONDecodeError:
            continue
        for job in _walk_json_ld_jobs(data):
            title = str(job.get("title") or job.get("name") or "").strip()
            url = _json_ld_url(job)
            normalized = safe_normalize_url(url, source_url) if url else None
            if title and normalized:
                links.append(RawLink(url=normalized, text=title, source_url=source_url))
    for script_attrs, script_body in _script_blocks(html):
        if not _looks_like_json_script(script_attrs, script_body):
            continue
        data = _parse_script_json(script_body)
        if data is None:
            continue
        for title, url in _walk_structured_job_records(data, source_url):
            links.append(RawLink(url=url, text=title, source_url=source_url))
    links.extend(candidate.as_raw_link() for candidate in extract_listing_candidates(html, source_url))
    return dedupe_raw_links(links)


def _script_blocks(html: str) -> list[tuple[str, str]]:
    return [
        (attrs, unescape(body.strip()))
        for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.I | re.S)
        if body.strip()
    ]


def _looks_like_json_script(attrs: str, body: str) -> bool:
    attrs_lower = attrs.lower()
    body_stripped = body.strip()
    return (
        "application/json" in attrs_lower
        or "application/ld+json" in attrs_lower
        or body_stripped.startswith("{")
        or body_stripped.startswith("[")
    )


def _parse_script_json(body: str):
    text = body.strip()
    if text.startswith("<![CDATA["):
        text = text.removeprefix("<![CDATA[").removesuffix("]]>").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _walk_json_ld_jobs(value):
    if isinstance(value, dict):
        item_type = value.get("@type")
        types = item_type if isinstance(item_type, list) else [item_type]
        if any(str(kind).lower() == "jobposting" for kind in types):
            yield value
        for child in value.values():
            yield from _walk_json_ld_jobs(child)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json_ld_jobs(item)


def _json_ld_url(job: dict) -> str:
    raw_url = job.get("url") or job.get("sameAs")
    if isinstance(raw_url, list):
        raw_url = raw_url[0] if raw_url else ""
    if isinstance(raw_url, dict):
        raw_url = raw_url.get("@id") or raw_url.get("url") or ""
    return str(raw_url or "")


STRUCTURED_TITLE_FIELDS = ("title", "name", "jobTitle", "job_title", "text")
STRUCTURED_URL_FIELDS = (
    "url",
    "absolute_url",
    "absoluteUrl",
    "hostedUrl",
    "applyUrl",
    "jobUrl",
    "job_url",
    "externalPath",
    "detailUrl",
    "link",
)


def _walk_structured_job_records(value, source_url: str):
    if isinstance(value, dict):
        title = _first_text_field(value, STRUCTURED_TITLE_FIELDS)
        url = _structured_record_url(value, source_url, title)
        if title and url and _looks_like_structured_job_record(value, url, source_url, title):
            yield title, url
        for child in value.values():
            yield from _walk_structured_job_records(child, source_url)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_structured_job_records(item, source_url)


def _first_text_field(record: dict, fields: tuple[str, ...]) -> str:
    for field in fields:
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return ""


def _structured_record_url(record: dict, source_url: str, title: str) -> str | None:
    raw_url = _first_text_field(record, STRUCTURED_URL_FIELDS)
    provider = detect_provider(source_url)
    if raw_url:
        normalized = safe_normalize_url(raw_url, source_url)
        if normalized:
            return normalized

    if provider == "successfactors":
        job_req_id = _first_text_field(record, ("career_job_req_id", "jobReqId", "job_req_id", "id"))
        if job_req_id:
            return add_query_params(source_url, {"career_ns": "job_listing", "career_job_req_id": job_req_id})

    if provider == "icims":
        job_id = _first_text_field(record, ("id", "jobId", "job_id", "jobNumber"))
        if job_id and title:
            return safe_normalize_url(f"/jobs/{job_id}/{_slugify_title(title)}/job", source_url)

    if provider == "ashby":
        job_id = _first_text_field(record, ("id", "jobId", "job_id"))
        board = _ashby_board_name(source_url)
        if job_id and board:
            return f"https://jobs.ashbyhq.com/{board}/{job_id}"

    if provider == "workable":
        short_code = _first_text_field(record, ("shortcode", "shortCode", "code", "id"))
        account = _workable_account_name(source_url)
        if short_code and account:
            return f"https://apply.workable.com/{account}/j/{short_code}/"

    return None


def _looks_like_structured_job_record(
    record: dict,
    url: str,
    source_url: str,
    title: str,
) -> bool:
    keys = " ".join(str(key).lower() for key in record)
    query = urlparse(url).query.lower()
    candidate = score_job_link(RawLink(url=url, text=title, source_url=source_url), source_url)
    reason_text = " ".join(candidate.reasons)
    return (
        is_likely_job_detail(candidate)
        or "ATS job detail pattern" in reason_text
        or "career_job_req_id" in query
        or "jobreqid" in query
        or ("job" in keys and detect_provider(url) != "generic" and candidate.score >= 90)
    )


def _slugify_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def dedupe_raw_links(links: list[RawLink]) -> list[RawLink]:
    seen: set[tuple[str, str]] = set()
    deduped: list[RawLink] = []
    for link in links:
        key = (link.url.rstrip("/"), link.text.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(link)
    return deduped


def _smartrecruiters_job_url(job: dict, job_list_url: str) -> str:
    actions = job.get("actions") or {}
    if actions.get("details"):
        return str(actions["details"])
    ref = job.get("ref")
    if ref:
        return str(ref)
    job_id = job.get("id")
    if not job_id:
        return ""
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    return f"https://jobs.smartrecruiters.com/{parts[0]}/{job_id}"


def _ashby_board_name(job_list_url: str) -> str:
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    host = parsed.netloc.lower()
    if host == "jobs.ashbyhq.com" and parts:
        return parts[0]
    if host.endswith(".ashbyhq.com") and host not in {"api.ashbyhq.com", "jobs.ashbyhq.com"}:
        return host.split(".", 1)[0]
    return ""


def _ashby_job_url(job: dict, job_list_url: str) -> str:
    raw_url = str(job.get("jobUrl") or job.get("hostedUrl") or job.get("url") or "")
    if raw_url:
        normalized = safe_normalize_url(raw_url, job_list_url)
        if normalized:
            return normalized
    job_id = str(job.get("id") or "")
    board = _ashby_board_name(job_list_url)
    if job_id and board:
        return f"https://jobs.ashbyhq.com/{board}/{job_id}"
    return ""


def _workable_account_name(job_list_url: str) -> str:
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc.lower() == "apply.workable.com" and parts:
        return parts[0]
    return ""


def build_workday_api_url(job_list_url: str) -> str | None:
    parsed = urlparse(job_list_url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    site = parts[-1]
    tenant = parsed.netloc.split(".", 1)[0]
    if not tenant or not site:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/wday/cxs/{tenant}/{site}/jobs"


def _workday_job_url(job: dict, job_list_url: str) -> str:
    external_path = str(job.get("externalPath") or "")
    if not external_path:
        return ""
    if external_path.startswith("http"):
        return external_path
    parsed = urlparse(job_list_url)
    board_path = parsed.path.rstrip("/")
    if not external_path.startswith("/"):
        external_path = "/" + external_path
    return f"{parsed.scheme}://{parsed.netloc}{board_path}{external_path}"


def _bamboohr_jobs_api_url(job_list_url: str) -> str:
    parsed = urlparse(job_list_url)
    return f"{parsed.scheme}://{parsed.netloc}/careers/list"


def _bamboohr_job_url(job: dict, job_list_url: str) -> str:
    job_id = str(job.get("id") or "")
    if not job_id:
        return ""
    parsed = urlparse(job_list_url)
    return f"{parsed.scheme}://{parsed.netloc}/careers/{job_id}"


def build_search_result_url(job_list_url: str, target_title: str) -> str | None:
    query = quote_plus(target_title)
    provider = detect_provider(job_list_url)
    if provider == "google_careers":
        return f"https://www.google.com/about/careers/applications/jobs/results/?q={query}"
    if provider == "meta_careers":
        return f"https://www.metacareers.com/jobs/?q={query}"
    if provider in {
        "lever",
        "greenhouse",
        "ashby",
    }:
        return job_list_url
    if provider in {"workable", "smartrecruiters", "icims", "workday", "successfactors", "rippling", "bamboohr"}:
        return build_provider_search_urls(job_list_url, target_title)[-1]
    return None


def add_query_params(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key, value in params.items():
        query[key] = [value]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def score_title_match(candidate_title: str, target_title: str) -> tuple[int, list[str]]:
    candidate_tokens = _tokens(candidate_title)
    target_tokens = _tokens(target_title)
    if not candidate_tokens or not target_tokens:
        return 0, []

    overlap = candidate_tokens & target_tokens
    recall = len(overlap) / len(target_tokens)
    precision = len(overlap) / len(candidate_tokens)
    score = int((recall * 70) + (precision * 30))
    reasons = []
    if overlap:
        reasons.append(f"title token overlap: {', '.join(sorted(overlap))}")
    if candidate_title.strip().lower() == target_title.strip().lower():
        score += 50
        reasons.append("exact title match")
    return score, reasons


def _tokens(text: str) -> set[str]:
    normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
    return {token for token in normalized.split() if len(token) >= 2 and token not in STOPWORDS}
