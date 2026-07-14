from __future__ import annotations

import base64
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


class CareerSearchResolver:
    def __init__(
        self,
        fetcher: Fetcher,
        max_results: int = 8,
        max_queries: int = 5,
        max_source_fetches: int = 6,
    ) -> None:
        self.fetcher = fetcher
        self.max_results = max(0, max_results)
        self.max_queries = max(0, max_queries)
        self.max_source_fetches = max(0, max_source_fetches)

    def search(
        self,
        company_name: str,
        company_website_url: str,
        *,
        ats_only: bool = False,
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
        }

        configured_queries = (
            build_ats_search_queries(company_name)
            if ats_only
            else build_search_queries(company_name, official_domain)
        )
        effective_query_limit = self.max_queries if ats_only else min(self.max_queries, 3)
        queries = configured_queries[:effective_query_limit]
        trace["configured_query_limit"] = self.max_queries
        trace["effective_query_limit"] = effective_query_limit
        source_fetches = 0
        disabled_sources: set[str] = set()
        for query_text in queries:
            sources = _search_sources(query_text)
            if ats_only:
                sources = sources[:1]
            skip_bing_html = False
            for source in sources:
                if source.name in disabled_sources:
                    trace["source_circuit_skips"].append(
                        {
                            "source": source.name,
                            "reason": SOURCE_CIRCUIT_REASON,
                        }
                    )
                    continue
                if source.name == "bing_html" and skip_bing_html:
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
                    page = self.fetcher.fetch(source.url)
                except FetchError as exc:
                    query_trace["error"] = str(exc)
                    trace["error"] = trace["error"] or str(exc)
                    if exc.retryable is False:
                        disabled_sources.add(source.name)
                        trace["source_circuit_breaks"].append(
                            {
                                "source": source.name,
                                "reason": SOURCE_CIRCUIT_REASON,
                            }
                        )
                    continue

                raw_urls = _parse_search_results(source.name, page.html)
                query_trace["result_count"] = len(raw_urls)
                self._collect_search_candidates(
                    raw_urls,
                    source.url,
                    query_text,
                    company_name,
                    official_domain,
                    candidates,
                    seen,
                    query_trace,
                    ats_only=ats_only,
                )
                if candidates:
                    trace["stopped_reason"] = "search_candidate_found"
                    break
                if source.name == "bing_rss" and raw_urls:
                    skip_bing_html = True
                    query_trace["skipped_sources"] = [
                        {
                            "source": "bing_html",
                            "reason": "rss_returned_results_without_valid_candidate",
                        }
                    ]
            if candidates:
                break
            if trace["source_fetch_budget_exhausted"]:
                break
            if trace["stopped_reason"] == "deadline_exhausted":
                break

        candidates.sort(key=lambda candidate: (-candidate.score, candidate.url))
        selected = candidates[: self.max_results]
        trace["candidates"] = [_candidate_trace(candidate) for candidate in selected]
        if trace["stopped_reason"] is None:
            trace["stopped_reason"] = "no_valid_candidates"
        return CareerSearchResult(selected, trace)

    def _collect_search_candidates(
        self,
        raw_urls: list[str],
        source_url: str,
        query_text: str,
        company_name: str,
        official_domain: str,
        candidates: list[LinkCandidate],
        seen: set[str],
        query_trace: dict,
        *,
        ats_only: bool = False,
    ) -> None:
        for raw_url in raw_urls:
            cleaned = clean_search_result_url(raw_url)
            key = _dedupe_key(cleaned)
            if not cleaned or key in seen:
                continue
            seen.add(key)
            if ats_only and not is_ats_url(cleaned):
                continue
            if not _is_valid_search_result(cleaned, company_name, official_domain):
                continue
            link = RawLink(url=cleaned, text=cleaned, source_url=source_url, origin="search_result")
            candidate = score_career_link(link)
            candidate.score += _search_bonus(cleaned, official_domain, query_text)
            if candidate.score < 60:
                continue
            candidates.append(candidate)
            query_trace["candidates"].append(_candidate_trace(candidate))


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
        self.urls: list[str] = []
        self._in_result_heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        classes = set(attrs_dict.get("class", "").split())
        if tag in {"h2", "h3"}:
            self._in_result_heading = True
        if tag == "a" and self._in_result_heading and attrs_dict.get("href"):
            self.urls.append(attrs_dict["href"])
        elif tag == "li" and "b_algo" in classes:
            self._in_result_heading = False

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h2", "h3"}:
            self._in_result_heading = False


class _DuckDuckGoResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag == "a" and "result__a" in attrs_dict.get("class", "").split():
            if attrs_dict.get("href"):
                self.urls.append(attrs_dict["href"])


def _search_sources(query_text: str) -> list[_SearchSource]:
    query = urlencode({"q": query_text})
    rss_query = urlencode({"q": query_text, "format": "rss"})
    return [
        _SearchSource("bing_rss", f"{BING_SEARCH_ENDPOINT}?{rss_query}"),
        _SearchSource("bing_html", f"{BING_SEARCH_ENDPOINT}?{query}"),
        _SearchSource("duckduckgo_html", f"{DUCKDUCKGO_SEARCH_ENDPOINT}?{query}"),
    ]


def _parse_search_results(source: str, body: str) -> list[str]:
    if source == "bing_rss":
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return []
        return [(item.findtext("link") or "").strip() for item in root.findall(".//item")]
    parser = _DuckDuckGoResultParser() if source == "duckduckgo_html" else _BingResultParser()
    parser.feed(body)
    return parser.urls


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


def _is_valid_search_result(url: str, company_name: str, official_domain: str) -> bool:
    if is_resource_url(url) or _is_blocked(url):
        return False
    domain = domain_of(url)
    if official_domain and (domain == official_domain or domain.endswith("." + official_domain)):
        path = urlparse(url).path.lower()
        return domain.startswith(("careers.", "jobs.")) or bool(
            re.search(
                r"(?:^|[-_/])(careers?|jobs?|join|openings|positions)(?:[-_/]|$)",
                path,
            )
        )
    if not is_ats_url(url):
        return False
    haystack = re.sub(r"[^a-z0-9]+", "", f"{domain}{urlparse(url).path}".lower())
    tokens = _identity_tokens(company_name, official_domain)
    company_tokens = _identity_tokens(company_name)
    compact_company = "".join(company_tokens)
    return bool(tokens) and (
        (bool(compact_company) and compact_company in haystack)
        or all(token in haystack for token in company_tokens)
        or any(token == official_domain.split(".", 1)[0].lower() and token in haystack for token in tokens)
    )


def build_search_queries(company_name: str, official_domain: str) -> list[str]:
    queries = [f"{company_name} careers jobs"]
    if official_domain:
        queries.extend([f"site:{official_domain} careers", f"site:{official_domain} jobs"])
    queries.extend([f"{company_name} careers", f"{company_name} jobs"])
    return dedupe_preserving_order(queries)


def build_ats_search_queries(company_name: str) -> list[str]:
    normalized_company = " ".join(_identity_tokens(company_name)) or company_name
    return [
        f'site:job-boards.greenhouse.io "{normalized_company}" jobs',
        f'site:myworkdayjobs.com "{normalized_company}" jobs',
        f'site:jobs.lever.co "{normalized_company}" jobs',
        f'site:jobs.ashbyhq.com "{normalized_company}" jobs',
        f'site:eightfold.ai "{normalized_company}" jobs',
    ]


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
