from __future__ import annotations

import base64
import hashlib
import math
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse
from xml.etree import ElementTree as ET

from .contracts import FetchBudget
from .models import LinkCandidate
from .scoring import is_ats_url, is_resource_url, score_career_link
from .search_backend import SearchBackend, SearchQuery
from .web import FetchError, Fetcher, RawLink, domain_of, safe_normalize_url


BING_SEARCH_ENDPOINT = "https://www.bing.com/search"
DUCKDUCKGO_SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
SOURCE_CIRCUIT_REASON = "non_retryable_fetch_error"

BLOCKED_SEARCH_DOMAINS = {
    "bing.com",
    "microsoft.com",
    "duckduckgo.com",
    "linkedin.com",
    "licdn.com",
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "youtube.com",
    "wikipedia.org",
    "crunchbase.com",
    "glassdoor.com",
    "indeed.com",
    "wellfound.com",
    "ziprecruiter.com",
    "monster.com",
}

@dataclass
class CareerSearchResult:
    candidates: list[LinkCandidate]
    trace: dict


@dataclass(frozen=True)
class _SearchSource:
    name: str
    url: str


@dataclass(frozen=True)
class _SearchResult:
    url: str
    title: str = ""
    snippet: str = ""


@dataclass(frozen=True)
class _SearchExecution:
    request_url: str
    final_url: str
    results: tuple[_SearchResult, ...]
    disposition: str = "ok"
    reason: str | None = None


@dataclass(frozen=True)
class _AtsSearchQuery:
    query: str
    provider_family: str | None


_ATS_PROVIDER_FAMILIES: tuple[tuple[str, str], ...] = (
    ("greenhouse", "job-boards.greenhouse.io"),
    ("lever", "jobs.lever.co"),
    ("ashby", "jobs.ashbyhq.com"),
    ("workable", "apply.workable.com"),
    ("pinpoint", "pinpointhq.com"),
    ("smartrecruiters", "jobs.smartrecruiters.com"),
    ("workday", "myworkdayjobs.com"),
    ("oracle", "oraclecloud.com"),
    ("eightfold", "eightfold.ai"),
)


class CareerSearchResolver:
    def __init__(
        self,
        fetcher: Fetcher,
        max_results: int = 8,
        max_queries: int = 5,
        max_source_fetches: int = 6,
        search_backend: SearchBackend | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.max_results = max(0, max_results)
        self.max_queries = max(0, max_queries)
        self.max_source_fetches = max(0, max_source_fetches)
        self.search_backend = search_backend

    def search(
        self,
        company_name: str,
        company_website_url: str,
        *,
        target_title: str | None = None,
        ats_only: bool = False,
        exhaustive: bool = False,
        allow_unbound_career: bool = False,
        query_diversity_first: bool = False,
    ) -> CareerSearchResult:
        official_domain = domain_of(company_website_url)
        candidates: list[LinkCandidate] = []
        seen: set[str] = set()
        fetch_budget = self.fetcher if isinstance(self.fetcher, FetchBudget) else None
        trace = {
            "queries": [],
            "query_url": None,
            "candidates": [],
            "error": None,
            "source_circuit_breaks": [],
            "source_circuit_skips": [],
            "source_fetch_budget": self.max_source_fetches,
            "source_fetch_budget_exhausted": False,
            "fetch_budget_supported": fetch_budget is not None,
            "fetch_budget_checks": 0,
            "fetch_budget_unavailable": False,
            "fetch_budget_invalid": False,
            "stopped_reason": None,
            "ats_only": ats_only,
            "exhaustive": exhaustive,
            "allow_unbound_career": allow_unbound_career,
            "query_diversity_first": query_diversity_first,
            "ats_secondary_rescue": {
                "attempt_count": 0,
                "rejection_count": 0,
                "attempts": [],
            },
        }
        if self.search_backend is not None:
            trace["search_backend"] = self.search_backend.public_configuration()

        effective_query_limit = self.max_queries if ats_only else min(self.max_queries, 3)
        ats_query_plan = (
            build_ats_search_query_plan(
                company_name,
                target_title,
                max_queries=effective_query_limit,
            )
            if ats_only
            else ()
        )
        queries = (
            [item.query for item in ats_query_plan]
            if ats_only
            else build_search_queries(company_name, official_domain)[:effective_query_limit]
        )
        trace["configured_query_limit"] = self.max_queries
        trace["effective_query_limit"] = effective_query_limit
        if ats_only:
            trace["ats_query_plan"] = [
                {
                    "query": item.query,
                    "provider_family": item.provider_family,
                    "bucket_index": index,
                }
                for index, item in enumerate(ats_query_plan)
            ]
            trace["ats_provider_rotation_start"] = _ats_rotation_start(
                company_name,
                target_title,
            )
        source_fetches = 0
        disabled_sources: set[str] = set()
        candidate_buckets: list[list[LinkCandidate]] = [[] for _ in queries]
        completed_query_count = 0
        for query_index, query_text in enumerate(queries):
            completed_query_count += 1
            query_candidate_count = len(candidates)
            sources = _search_sources_for_backend(query_text, self.search_backend)
            diversity_mode = query_diversity_first and (
                ats_only or allow_unbound_career
            )
            if diversity_mode and self.search_backend is None:
                # Spend a small budget across distinct queries. Repeating one
                # SERP via HTML or a challenge-prone secondary source provides
                # less recall than reaching the next provider/site query.
                sources = [source for source in sources if source.name == "bing_rss"]
            elif ats_only and self.search_backend is None:
                sources = [
                    source
                    for source in sources
                    if source.name in {"bing_rss", "duckduckgo_html"}
                ]
            use_ats_secondary = False
            for source in sources:
                if (
                    ats_only
                    and not diversity_mode
                    and source.name == "duckduckgo_html"
                    and not use_ats_secondary
                ):
                    continue
                if source.name in disabled_sources:
                    trace["source_circuit_skips"].append(
                        {
                            "source": source.name,
                            "reason": SOURCE_CIRCUIT_REASON,
                        }
                    )
                    continue
                if source_fetches >= self.max_source_fetches:
                    trace["source_fetch_budget_exhausted"] = True
                    break
                if fetch_budget is not None:
                    trace["fetch_budget_checks"] += 1
                    available, invalid = _fetch_budget_available(fetch_budget)
                    if not available:
                        trace["fetch_budget_unavailable"] = True
                        trace["fetch_budget_invalid"] = invalid
                        trace["stopped_reason"] = "deadline_exhausted"
                        break
                source_fetches += 1
                query_trace = {
                    "source": source.name,
                    "query_url": source.url,
                    "query": query_text,
                    "candidates": [],
                    "error": None,
                    "result_count": 0,
                }
                trace["queries"].append(query_trace)
                if trace["query_url"] is None:
                    trace["query_url"] = source.url
                try:
                    execution = _execute_search(
                        self.fetcher,
                        query_text,
                        source,
                        self.search_backend,
                    )
                except FetchError as exc:
                    error_trace = _search_fetch_error_trace(
                        exc,
                        self.search_backend,
                    )
                    query_trace["error"] = error_trace
                    trace["error"] = trace["error"] or error_trace
                    if exc.retryable is False:
                        disabled_sources.add(source.name)
                        trace["source_circuit_breaks"].append(
                            {
                                "source": source.name,
                                "reason": SOURCE_CIRCUIT_REASON,
                            }
                        )
                    continue

                query_trace["query_url"] = execution.request_url
                query_trace["final_url"] = execution.final_url
                if trace["query_url"] is None:
                    trace["query_url"] = execution.request_url
                if execution.disposition != "ok":
                    query_trace["error"] = execution.reason or execution.disposition
                    query_trace["response_disposition"] = execution.disposition
                    disabled_sources.add(source.name)
                    trace["source_circuit_breaks"].append(
                        {
                            "source": source.name,
                            "reason": execution.disposition,
                        }
                    )
                    continue

                raw_urls = execution.results
                query_trace["result_count"] = len(raw_urls)
                before_collect = len(candidates)
                self._collect_search_candidates(
                    raw_urls,
                    execution.request_url,
                    query_text,
                    company_name,
                    official_domain,
                    candidates,
                    seen,
                    query_trace,
                    ats_only=ats_only,
                    allow_unbound_ats=bool(ats_only and target_title),
                    allow_unbound_career=allow_unbound_career,
                )
                candidate_buckets[query_index].extend(candidates[before_collect:])
                if len(candidates) > query_candidate_count:
                    if not exhaustive:
                        trace["stopped_reason"] = "search_candidate_found"
                    break
                if (
                    ats_only
                    and not diversity_mode
                    and self.search_backend is None
                    and source.name == "bing_rss"
                    and raw_urls
                ):
                    use_ats_secondary = True
                    query_trace["skipped_sources"] = [
                        {
                            "source": "bing_html",
                            "reason": "rss_returned_results_without_valid_candidate",
                        }
                    ]
            if len(candidates) > query_candidate_count and not exhaustive:
                break
            if trace["source_fetch_budget_exhausted"]:
                break
            if trace["stopped_reason"] == "deadline_exhausted":
                break

        diversity_sweep_complete = completed_query_count == len(queries)
        if (
            ats_only
            and query_diversity_first
            and self.search_backend is None
            and diversity_sweep_complete
            and trace["stopped_reason"] != "deadline_exhausted"
            and source_fetches < self.max_source_fetches
        ):
            rescue_trace = trace["ats_secondary_rescue"]
            for query_index, query_text in enumerate(queries):
                if candidate_buckets[query_index]:
                    continue
                if "duckduckgo_html" in disabled_sources:
                    trace["source_circuit_skips"].append(
                        {
                            "source": "duckduckgo_html",
                            "reason": SOURCE_CIRCUIT_REASON,
                        }
                    )
                    continue
                if source_fetches >= self.max_source_fetches:
                    trace["source_fetch_budget_exhausted"] = True
                    break
                if fetch_budget is not None:
                    trace["fetch_budget_checks"] += 1
                    available, invalid = _fetch_budget_available(fetch_budget)
                    if not available:
                        trace["fetch_budget_unavailable"] = True
                        trace["fetch_budget_invalid"] = invalid
                        trace["stopped_reason"] = "deadline_exhausted"
                        break

                source = next(
                    source
                    for source in _search_sources(query_text)
                    if source.name == "duckduckgo_html"
                )
                source_fetches += 1
                query_trace = {
                    "source": source.name,
                    "query_url": source.url,
                    "query": query_text,
                    "candidates": [],
                    "error": None,
                    "result_count": 0,
                    "rescue_attempt": True,
                    "bucket_index": query_index,
                    "rejection_count": 0,
                }
                trace["queries"].append(query_trace)
                attempt_trace = {
                    "bucket_index": query_index,
                    "source": source.name,
                    "result_count": 0,
                    "accepted_count": 0,
                    "rejection_count": 0,
                }
                rescue_trace["attempt_count"] += 1
                rescue_trace["attempts"].append(attempt_trace)
                try:
                    execution = _execute_search(
                        self.fetcher,
                        query_text,
                        source,
                        self.search_backend,
                    )
                except FetchError as exc:
                    error_trace = _search_fetch_error_trace(
                        exc,
                        self.search_backend,
                    )
                    query_trace["error"] = error_trace
                    trace["error"] = trace["error"] or error_trace
                    if exc.retryable is False:
                        disabled_sources.add(source.name)
                        trace["source_circuit_breaks"].append(
                            {
                                "source": source.name,
                                "reason": SOURCE_CIRCUIT_REASON,
                            }
                        )
                    continue

                query_trace["query_url"] = execution.request_url
                query_trace["final_url"] = execution.final_url
                if execution.disposition != "ok":
                    query_trace["error"] = execution.reason or execution.disposition
                    query_trace["response_disposition"] = execution.disposition
                    disabled_sources.add(source.name)
                    trace["source_circuit_breaks"].append(
                        {
                            "source": source.name,
                            "reason": execution.disposition,
                        }
                    )
                    continue

                raw_urls = execution.results
                query_trace["result_count"] = len(raw_urls)
                before_collect = len(candidates)
                self._collect_search_candidates(
                    raw_urls,
                    execution.request_url,
                    query_text,
                    company_name,
                    official_domain,
                    candidates,
                    seen,
                    query_trace,
                    ats_only=True,
                    allow_unbound_ats=bool(target_title),
                    allow_unbound_career=allow_unbound_career,
                )
                accepted_count = len(candidates) - before_collect
                rejection_count = len(raw_urls) - accepted_count
                candidate_buckets[query_index].extend(candidates[before_collect:])
                query_trace["rejection_count"] = rejection_count
                attempt_trace.update(
                    {
                        "result_count": len(raw_urls),
                        "accepted_count": accepted_count,
                        "rejection_count": rejection_count,
                    }
                )
                rescue_trace["rejection_count"] += rejection_count
                if accepted_count and not exhaustive:
                    trace["stopped_reason"] = "search_candidate_found"
                    break

        selected = (
            _select_bucket_fair_candidates(candidate_buckets, self.max_results)
            if ats_only
            else _select_ranked_candidates(candidates, self.max_results)
        )
        if ats_only:
            trace["candidate_bucket_counts"] = [len(bucket) for bucket in candidate_buckets]
            trace["candidate_selection"] = "bucket_round_robin"
        trace["candidates"] = [_candidate_trace(candidate) for candidate in selected]
        if trace["stopped_reason"] is None:
            trace["stopped_reason"] = (
                "query_plan_complete" if candidates else "no_valid_candidates"
            )
        return CareerSearchResult(selected, trace)

    def _collect_search_candidates(
        self,
        raw_results: list[_SearchResult],
        source_url: str,
        query_text: str,
        company_name: str,
        official_domain: str,
        candidates: list[LinkCandidate],
        seen: set[str],
        query_trace: dict,
        *,
        ats_only: bool = False,
        allow_unbound_ats: bool = False,
        allow_unbound_career: bool = False,
    ) -> None:
        for result in raw_results:
            cleaned = clean_search_result_url(result.url)
            key = _dedupe_key(cleaned)
            if not cleaned or key in seen:
                continue
            seen.add(key)
            if ats_only and not is_ats_url(cleaned):
                continue
            if not _is_valid_search_result(
                cleaned,
                company_name,
                official_domain,
                search_text=f"{result.title} {result.snippet}",
                allow_unbound_ats=allow_unbound_ats,
                allow_unbound_career=allow_unbound_career,
            ):
                continue
            link = RawLink(url=cleaned, text=cleaned, source_url=source_url, origin="search_result")
            candidate = score_career_link(link)
            candidate.score += _search_bonus(cleaned, official_domain, query_text)
            if _is_branded_career_microsite(
                cleaned,
                company_name,
                official_domain,
                f"{result.title} {result.snippet}",
            ):
                candidate.score += 80
                candidate.reasons.append("unverified branded career microsite search lead")
            if candidate.score < 60:
                continue
            candidates.append(candidate)
            query_trace["candidates"].append(_candidate_trace(candidate))


def search_site_openings(
    fetcher: Fetcher,
    official_url: str,
    target_title: str,
    *,
    max_results: int = 3,
    max_source_fetches: int = 2,
    search_backend: SearchBackend | None = None,
) -> CareerSearchResult:
    """Return bounded same-site opening leads; callers must verify every page."""

    official_domain = domain_of(official_url)
    official_site = _registrable_site(official_domain)
    normalized_title = " ".join(target_title.replace('"', " ").split())
    trace = {
        "query": None,
        "queries": [],
        "candidates": [],
        "source_fetch_budget": max_source_fetches,
        "source_fetch_budget_exhausted": False,
        "stopped_reason": None,
    }
    if search_backend is not None:
        trace["search_backend"] = search_backend.public_configuration()
    if (
        not official_domain
        or not official_site
        or not normalized_title
        or max_results < 1
        or max_source_fetches < 1
    ):
        trace["stopped_reason"] = "invalid_or_empty_request"
        return CareerSearchResult([], trace)

    query_text = f'site:{official_site} "{normalized_title}"'
    trace["query"] = query_text
    candidates: list[LinkCandidate] = []
    seen: set[str] = set()
    source_fetches = 0
    for source in _search_sources_for_backend(query_text, search_backend):
        if search_backend is None and source.name == "bing_html":
            continue
        if source_fetches >= max_source_fetches:
            trace["source_fetch_budget_exhausted"] = True
            break
        source_fetches += 1
        query_trace = {
            "source": source.name,
            "query_url": source.url,
            "result_count": 0,
            "candidates": [],
            "error": None,
        }
        trace["queries"].append(query_trace)
        try:
            execution = _execute_search(
                fetcher,
                query_text,
                source,
                search_backend,
            )
        except FetchError as error:
            query_trace["error"] = _search_fetch_error_trace(
                error,
                search_backend,
            )
            continue
        query_trace["query_url"] = execution.request_url
        query_trace["final_url"] = execution.final_url
        if execution.disposition != "ok":
            query_trace["error"] = execution.reason or execution.disposition
            query_trace["response_disposition"] = execution.disposition
            continue
        raw_results = execution.results
        query_trace["result_count"] = len(raw_results)
        for result in raw_results:
            cleaned = clean_search_result_url(result.url)
            key = _dedupe_key(cleaned)
            if not cleaned or key in seen or is_resource_url(cleaned):
                continue
            seen.add(key)
            candidate_domain = domain_of(cleaned)
            if _registrable_site(candidate_domain) != official_site:
                continue
            path = urlparse(cleaned).path.casefold()
            if not re.search(r"(?:^|[-_/])jobs?(?:[-_/]|$)", path):
                continue
            candidate = LinkCandidate(
                url=cleaned,
                text=cleaned,
                source_url=execution.request_url,
                score=100,
                reasons=["same-site title-targeted opening lead"],
            )
            candidates.append(candidate)
            query_trace["candidates"].append(_candidate_trace(candidate))
            if len(candidates) >= max_results:
                break
        if candidates:
            break
    trace["candidates"] = [_candidate_trace(candidate) for candidate in candidates]
    trace["stopped_reason"] = (
        "candidate_limit_reached"
        if len(candidates) >= max_results
        else "query_plan_complete"
        if candidates
        else "no_valid_candidates"
    )
    return CareerSearchResult(candidates, trace)


def _registrable_site(host: str) -> str:
    labels = host.casefold().strip(".").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    two_level_suffixes = {"co.jp", "co.nz", "co.uk", "com.au", "com.br", "com.sg"}
    suffix = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if suffix in two_level_suffixes else suffix


def _fetch_budget_available(fetcher: FetchBudget) -> tuple[bool, bool]:
    try:
        remaining = fetcher.remaining_fetch_seconds()
    except Exception:
        return False, True
    if remaining is None:
        return True, False
    if (
        isinstance(remaining, bool)
        or not isinstance(remaining, (int, float))
        or not math.isfinite(remaining)
    ):
        return False, True
    if remaining < 0:
        return False, True
    return remaining > 0, False


class _BingResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[_SearchResult] = []
        self._in_result_heading = False
        self._in_snippet = False
        self._url = ""
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        classes = set(attrs_dict.get("class", "").split())
        if tag == "li" and "b_algo" in classes:
            self._finish_result()
        if tag in {"h2", "h3"}:
            self._in_result_heading = True
        if tag == "a" and self._in_result_heading and attrs_dict.get("href"):
            self._url = attrs_dict["href"]
        if tag == "p" and self._url:
            self._in_snippet = True

    def handle_data(self, data: str) -> None:
        if self._in_result_heading:
            self._title_parts.append(data)
        elif self._in_snippet:
            self._snippet_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h2", "h3"}:
            self._in_result_heading = False
        elif tag == "p":
            self._in_snippet = False
        elif tag == "li":
            self._finish_result()

    def close(self) -> None:
        super().close()
        self._finish_result()

    def _finish_result(self) -> None:
        if self._url:
            self.results.append(
                _SearchResult(
                    self._url,
                    " ".join("".join(self._title_parts).split()),
                    " ".join("".join(self._snippet_parts).split()),
                )
            )
        self._url = ""
        self._title_parts = []
        self._snippet_parts = []
        self._in_result_heading = False
        self._in_snippet = False


class _DuckDuckGoResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[_SearchResult] = []
        self._url = ""
        self._in_title = False
        self._in_snippet = False
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        classes = set(attrs_dict.get("class", "").split())
        if tag == "a" and "result__a" in classes:
            self._finish_result()
            if attrs_dict.get("href"):
                self._url = attrs_dict["href"]
                self._in_title = True
        elif "result__snippet" in classes:
            self._in_snippet = True

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        elif self._in_snippet:
            self._snippet_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title:
            self._in_title = False
        elif self._in_snippet and tag in {"a", "div", "span"}:
            self._in_snippet = False

    def close(self) -> None:
        super().close()
        self._finish_result()

    def _finish_result(self) -> None:
        if self._url:
            self.results.append(
                _SearchResult(
                    self._url,
                    " ".join("".join(self._title_parts).split()),
                    " ".join("".join(self._snippet_parts).split()),
                )
            )
        self._url = ""
        self._title_parts = []
        self._snippet_parts = []
        self._in_title = False
        self._in_snippet = False


def _search_sources(query_text: str) -> list[_SearchSource]:
    locale = {"setlang": "en-us", "cc": "us"}
    query = urlencode({"q": query_text, **locale})
    rss_query = urlencode({"q": query_text, "format": "rss", **locale})
    return [
        _SearchSource("bing_rss", f"{BING_SEARCH_ENDPOINT}?{rss_query}"),
        _SearchSource("bing_html", f"{BING_SEARCH_ENDPOINT}?{query}"),
        _SearchSource("duckduckgo_html", f"{DUCKDUCKGO_SEARCH_ENDPOINT}?{query}"),
    ]


def _search_sources_for_backend(
    query_text: str,
    search_backend: SearchBackend | None,
) -> list[_SearchSource]:
    if search_backend is None:
        return _search_sources(query_text)
    public_configuration = search_backend.public_configuration()
    profile_digest = public_configuration.get(
        "search_backend_profile_digest",
        "unconfigured",
    )
    provenance_url = (
        f"https://search-backend.invalid/{search_backend.name}/{profile_digest}"
    )
    return [_SearchSource(search_backend.name, provenance_url)]


def _execute_search(
    fetcher: Fetcher,
    query_text: str,
    source: _SearchSource,
    search_backend: SearchBackend | None,
) -> _SearchExecution:
    if search_backend is not None:
        response = search_backend.search(SearchQuery(query_text), fetcher=fetcher)
        return _SearchExecution(
            request_url=response.request_url,
            final_url=response.final_url,
            results=tuple(
                _SearchResult(hit.url, hit.title, hit.snippet)
                for hit in response.hits
            ),
            disposition=response.disposition,
            reason=response.reason,
        )
    page = fetcher.fetch(source.url)
    challenge_reason = _search_challenge_reason(source.name, page.html)
    return _SearchExecution(
        request_url=source.url,
        final_url=page.final_url or page.url,
        results=tuple(_parse_search_results(source.name, page.html)),
        disposition="challenge" if challenge_reason is not None else "ok",
        reason=challenge_reason,
    )


def _search_fetch_error_trace(
    error: FetchError,
    search_backend: SearchBackend | None,
):
    if search_backend is None:
        return str(error)
    return {
        "reason_code": error.reason_code or "FETCH_FAILED",
        "status": error.status,
        "retryable": error.retryable,
        "transport_phase": error.transport_phase,
    }


def _search_challenge_reason(source: str, body: str) -> str | None:
    if source != "duckduckgo_html":
        return None
    text = (body or "")[:100_000].casefold()
    markers = (
        "anomaly.js",
        "bots use duckduckgo",
        "challenge-form",
        "captcha",
    )
    return "duckduckgo_challenge" if any(marker in text for marker in markers) else None


def _parse_search_results(source: str, body: str) -> list[_SearchResult]:
    if source == "bing_rss":
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return []
        return [
            _SearchResult(
                (item.findtext("link") or "").strip(),
                (item.findtext("title") or "").strip(),
                (item.findtext("description") or "").strip(),
            )
            for item in root.findall(".//item")
        ]
    parser = _DuckDuckGoResultParser() if source == "duckduckgo_html" else _BingResultParser()
    parser.feed(body)
    parser.close()
    return parser.results


def clean_search_result_url(url: str) -> str:
    url = unescape((url or "").strip())
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return ""
    if parsed.netloc.lower().removeprefix("www.") in {"bing.com", "microsoft.com"} or url.startswith("/ck/a"):
        values = parse_qs(parsed.query)
        target = (values.get("u") or [""])[0]
        if target.startswith("a1"):
            target = _decode_bing_target(target[2:])
        url = unquote(target)
    elif "duckduckgo.com" in parsed.netloc.lower() and parsed.path.startswith("/l/"):
        url = unquote((parse_qs(parsed.query).get("uddg") or [""])[0])
    normalized = safe_normalize_url(url)
    if not normalized:
        return ""
    try:
        parsed = urlparse(normalized)
        unsafe_authority = bool(
            parsed.username or parsed.password or parsed.port not in {None, 80, 443}
        )
    except (TypeError, ValueError):
        return ""
    if unsafe_authority:
        return ""
    if _is_blocked(normalized):
        return ""
    return safe_normalize_url(urlunparse(parsed._replace(fragment=""))) or ""


def _decode_bing_target(value: str) -> str:
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


def _is_blocked(url: str) -> bool:
    domain = domain_of(url)
    return any(domain == blocked or domain.endswith("." + blocked) for blocked in BLOCKED_SEARCH_DOMAINS)


def _is_valid_search_result(
    url: str,
    company_name: str,
    official_domain: str,
    *,
    search_text: str = "",
    allow_unbound_ats: bool = False,
    allow_unbound_career: bool = False,
) -> bool:
    if is_resource_url(url) or _is_blocked(url):
        return False
    domain = domain_of(url)
    if official_domain and (
        domain == official_domain or domain.endswith("." + official_domain)
    ):
        path = urlparse(url).path.lower()
        return domain.startswith(("careers.", "jobs.")) or bool(
            re.search(
                r"(?:^|[-_/])(careers?|jobs?|join|openings|positions)(?:[-_/]|$)",
                path,
            )
        )
    if not is_ats_url(url):
        return (
            _is_branded_career_microsite(
                url, company_name, official_domain, search_text
            )
            or (
                allow_unbound_career
                and _is_unbound_career_search_lead(url, company_name, search_text)
            )
        )
    # Title-targeted search only produces untrusted leads. Opaque ATS tenants
    # are verified by provider and identity contracts downstream.
    if allow_unbound_ats:
        return True
    haystack = re.sub(r"[^a-z0-9]+", "", f"{domain}{urlparse(url).path}".lower())
    tokens = _identity_tokens(company_name, official_domain)
    company_tokens = _identity_tokens(company_name)
    compact_company = "".join(company_tokens)
    return bool(tokens) and (
        (bool(compact_company) and compact_company in haystack)
        or all(token in haystack for token in company_tokens)
        or any(token == official_domain.split(".", 1)[0].lower() and token in haystack for token in tokens)
    )


def _is_branded_career_microsite(
    url: str,
    company_name: str,
    official_domain: str,
    search_text: str,
) -> bool:
    domain = domain_of(url)
    if not domain or not search_text or is_ats_url(url):
        return False
    if official_domain and (domain == official_domain or domain.endswith("." + official_domain)):
        return False

    company_tokens = _identity_tokens(company_name)
    if not company_tokens:
        return False
    site_identity = re.sub(r"[^a-z0-9]+", "", _registrable_site(domain).lower())
    compact_company = "".join(company_tokens)
    brand_bound = compact_company in site_identity or all(
        token in site_identity for token in company_tokens
    )
    if not brand_bound:
        return False

    normalized_search_text = re.sub(r"[^a-z0-9]+", " ", search_text.lower())
    return bool(
        re.search(
            r"\b(careers?|jobs?|hiring|openings?|positions?|employment|"
            r"opportunities|join (?:our|the) team|work with us)\b",
            normalized_search_text,
        )
    )


def _is_unbound_career_search_lead(
    url: str,
    company_name: str,
    search_text: str,
) -> bool:
    """Admit an untrusted lead; current-page verification remains mandatory."""

    parsed = urlparse(url)
    route_text = f"{parsed.hostname or ''} {parsed.path}"
    if not re.search(
        r"(?:^|[.\-_/])(careers?|jobs?|employment|openings|positions)(?:[.\-_/]|$)",
        route_text.casefold(),
    ):
        return False
    tokens = _identity_tokens(company_name)
    normalized_search = re.sub(r"[^a-z0-9]+", " ", search_text.casefold())
    return bool(tokens) and all(
        re.search(rf"\b{re.escape(token)}\b", normalized_search)
        for token in tokens
    )


def build_search_queries(company_name: str, official_domain: str) -> list[str]:
    queries = [f"{company_name} careers jobs"]
    if official_domain:
        queries.extend([f"site:{official_domain} careers", f"site:{official_domain} jobs"])
    queries.extend([f"{company_name} careers", f"{company_name} jobs"])
    return dedupe_preserving_order(queries)


def build_ats_search_queries(
    company_name: str,
    target_title: str | None = None,
) -> list[str]:
    normalized_company = " ".join(_identity_tokens(company_name)) or company_name
    normalized_title = " ".join((target_title or "").replace('"', " ").split())
    if normalized_title:
        return [
            f'"{normalized_company}" "{normalized_title}" jobs',
            f'site:job-boards.greenhouse.io "{normalized_company}" "{normalized_title}"',
            f'site:jobs.lever.co "{normalized_company}" "{normalized_title}"',
            f'site:jobs.ashbyhq.com "{normalized_company}" "{normalized_title}"',
            f'site:apply.workable.com "{normalized_company}" "{normalized_title}"',
            f'site:pinpointhq.com "{normalized_company}" "{normalized_title}"',
            f'site:jobs.smartrecruiters.com "{normalized_company}" "{normalized_title}"',
            f'site:myworkdayjobs.com "{normalized_company}" "{normalized_title}"',
            f'site:oraclecloud.com "{normalized_company}" "{normalized_title}"',
            f'site:eightfold.ai "{normalized_company}" "{normalized_title}"',
        ]
    return [
        f'"{normalized_company}" careers jobs',
        f'site:job-boards.greenhouse.io "{normalized_company}" jobs',
        f'site:jobs.lever.co "{normalized_company}" jobs',
        f'site:jobs.ashbyhq.com "{normalized_company}" jobs',
        f'site:apply.workable.com "{normalized_company}" jobs',
        f'site:pinpointhq.com "{normalized_company}" jobs',
        f'site:jobs.smartrecruiters.com "{normalized_company}" jobs',
        f'site:myworkdayjobs.com "{normalized_company}" jobs',
        f'site:eightfold.ai "{normalized_company}" jobs',
        f'site:oraclecloud.com "{normalized_company}" jobs',
    ]


def build_ats_search_query_plan(
    company_name: str,
    target_title: str | None = None,
    *,
    max_queries: int,
) -> tuple[_AtsSearchQuery, ...]:
    """Plan a fixed ATS search budget without permanently starving providers.

    The plan always spends its first slot on a general role query. Remaining
    slots rotate through provider families from a stable identity-derived
    offset. The resulting schedule is deterministic and can be replayed from
    the normalized S1 identity alone.
    """

    if isinstance(max_queries, bool) or not isinstance(max_queries, int):
        raise TypeError("ATS query limit must be an integer")
    if max_queries <= 0:
        return ()

    normalized_company = " ".join(_identity_tokens(company_name)) or company_name
    normalized_title = " ".join((target_title or "").replace('"', " ").split())
    general_query = (
        f'"{normalized_company}" "{normalized_title}" jobs'
        if normalized_title
        else f'"{normalized_company}" careers jobs'
    )
    provider_count = min(max_queries - 1, len(_ATS_PROVIDER_FAMILIES))
    rotation_start = _ats_rotation_start(company_name, target_title)
    plan = [_AtsSearchQuery(general_query, None)]
    for offset in range(provider_count):
        family_index = (rotation_start + offset) % len(_ATS_PROVIDER_FAMILIES)
        provider_family, domain = _ATS_PROVIDER_FAMILIES[family_index]
        provider_query = (
            f'site:{domain} "{normalized_company}" "{normalized_title}"'
            if normalized_title
            else f'site:{domain} "{normalized_company}" jobs'
        )
        plan.append(_AtsSearchQuery(provider_query, provider_family))
    return tuple(plan)


def _ats_rotation_start(company_name: str, target_title: str | None) -> int:
    normalized_company = " ".join(_identity_tokens(company_name)) or company_name.casefold().strip()
    normalized_title = " ".join((target_title or "").replace('"', " ").casefold().split())
    identity = f"{normalized_company}\x00{normalized_title}".encode("utf-8")
    digest = hashlib.sha256(identity).digest()
    return int.from_bytes(digest[:8], "big") % len(_ATS_PROVIDER_FAMILIES)


def _select_ranked_candidates(
    candidates: list[LinkCandidate],
    limit: int,
) -> list[LinkCandidate]:
    return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.url))[:limit]


def _select_bucket_fair_candidates(
    buckets: list[list[LinkCandidate]],
    limit: int,
) -> list[LinkCandidate]:
    """Use deterministic round-robin selection so one SERP cannot monopolize C."""

    if limit <= 0:
        return []
    ranked_buckets = [
        sorted(bucket, key=lambda candidate: (-candidate.score, candidate.url))
        for bucket in buckets
    ]
    selected: list[LinkCandidate] = []
    rank = 0
    while len(selected) < limit:
        added = False
        for bucket in ranked_buckets:
            if rank < len(bucket):
                selected.append(bucket[rank])
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
        rank += 1
    return selected


def _identity_tokens(company_name: str, official_domain: str = "") -> list[str]:
    stop = {"and", "co", "company", "corp", "corporation", "inc", "llc", "ltd", "the"}
    tokens = [token for token in re.findall(r"[a-z0-9]+", company_name.lower()) if token not in stop]
    domain_slug = official_domain.split(".", 1)[0].lower().removeprefix("www.")
    if domain_slug:
        tokens.append(domain_slug)
    return dedupe_preserving_order([token for token in tokens if len(token) >= 3])


def _dedupe_key(url: str) -> str:
    normalized = (url or "").rstrip("/")
    if is_ats_url(normalized):
        parsed = urlparse(normalized)
        normalized = urlunparse(parsed._replace(query="", fragment=""))
    return normalized.lower()


def _candidate_trace(candidate: LinkCandidate) -> dict:
    return {"url": candidate.url, "score": candidate.score, "reasons": candidate.reasons}


def dedupe_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _search_bonus(url: str, official_domain: str, query_text: str = "") -> int:
    score = 0
    domain = domain_of(url)
    path = urlparse(url).path.lower()
    query_lower = query_text.lower()
    if official_domain and (domain == official_domain or domain.endswith("." + official_domain)):
        score += 80
    if is_ats_url(url):
        score += 80
    if any(marker in domain for marker in ("successfactors", "smartrecruiters", "icims", "workdayjobs")):
        score += 60
    if domain.startswith(("careers.", "jobs.")):
        score += 55
    if any(part in path for part in ("/careers", "/career", "/jobs", "/join", "/openings")):
        score += 45
    if "site:" in query_lower and official_domain and domain.endswith(official_domain):
        score += 25
    return score
