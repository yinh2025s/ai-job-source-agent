from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

from .models import LinkCandidate
from .scoring import is_ats_url, is_resource_url, score_career_link
from .web import FetchError, Fetcher, RawLink, domain_of, normalize_url
from .website_resolver import SEARCH_ENDPOINT


BLOCKED_SEARCH_DOMAINS = {
    "bing.com",
    "microsoft.com",
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


class CareerSearchResolver:
    def __init__(self, fetcher: Fetcher, max_results: int = 8, max_queries: int = 5) -> None:
        self.fetcher = fetcher
        self.max_results = max_results
        self.max_queries = max_queries

    def search(self, company_name: str, company_website_url: str) -> CareerSearchResult:
        official_domain = domain_of(company_website_url)
        candidates: list[LinkCandidate] = []
        seen: set[str] = set()
        trace = {"queries": [], "query_url": None, "candidates": [], "error": None, "stopped_reason": None}

        for query_text in build_search_queries(company_name, official_domain)[: self.max_queries]:
            query = urlencode({"q": query_text})
            search_url = f"{SEARCH_ENDPOINT}?{query}"
            query_trace = {"query_url": search_url, "query": query_text, "candidates": [], "error": None}
            trace["queries"].append(query_trace)
            if trace["query_url"] is None:
                trace["query_url"] = search_url
            try:
                page = self.fetcher.fetch(search_url)
            except FetchError as exc:
                query_trace["error"] = str(exc)
                trace["error"] = trace["error"] or str(exc)
                trace["stopped_reason"] = "search_endpoint_fetch_failed"
                break

            parser = _SearchResultParser()
            parser.feed(page.html)
            raw_urls = parser.urls + re.findall(r"https?://[^\"'<>\s)]+", page.html)
            for raw_url in raw_urls:
                cleaned = clean_search_result_url(raw_url)
                if not cleaned or cleaned.rstrip("/") in seen:
                    continue
                seen.add(cleaned.rstrip("/"))
                if _is_blocked(cleaned) or is_resource_url(cleaned):
                    continue
                link = RawLink(url=cleaned, text=cleaned, source_url=search_url, origin="search_result")
                candidate = score_career_link(link)
                candidate.score += _search_bonus(cleaned, official_domain, query_text)
                if candidate.score < 60:
                    continue
                candidates.append(candidate)
                query_trace["candidates"].append(
                    {
                        "url": candidate.url,
                        "score": candidate.score,
                        "reasons": candidate.reasons,
                    }
                )
            if candidates:
                break

        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        trace["candidates"] = [
            {
                "url": candidate.url,
                "score": candidate.score,
                "reasons": candidate.reasons,
            }
            for candidate in candidates[: self.max_results]
        ]
        return CareerSearchResult(candidates[: self.max_results], trace)


class _SearchResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []
        self._in_h2 = False
        self._active_href = ""
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag == "h2":
            self._in_h2 = True
        elif tag == "a" and self._in_h2 and attrs_dict.get("href"):
            self.urls.append(attrs_dict["href"])
        elif tag == "a" and attrs_dict.get("href"):
            self._active_href = attrs_dict["href"]
            self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2":
            self._in_h2 = False
        if tag == "a" and self._active_href:
            text = " ".join("".join(self._active_text).split()).lower()
            href = self._active_href.lower()
            if any(marker in f"{text} {href}" for marker in ("career", "careers", "jobs", "job-openings")):
                self.urls.append(self._active_href)
            self._active_href = ""
            self._active_text = []


def clean_search_result_url(url: str) -> str:
    if url.startswith("/ck/a"):
        parsed = urlparse(url)
        values = parse_qs(parsed.query)
        if values.get("u"):
            url = values["u"][0]
            if url.startswith("a1"):
                url = url[2:]
            url = unquote(url)
    if not url.startswith("http"):
        return ""
    parsed = urlparse(url)
    if parsed.netloc.endswith("bing.com") or parsed.netloc.endswith("microsoft.com"):
        return ""
    return normalize_url(urlunparse(parsed._replace(fragment="")))


def _is_blocked(url: str) -> bool:
    domain = domain_of(url)
    return any(domain == blocked or domain.endswith("." + blocked) for blocked in BLOCKED_SEARCH_DOMAINS)


def build_search_queries(company_name: str, official_domain: str) -> list[str]:
    queries = [
        f"{company_name} careers jobs",
    ]
    if official_domain:
        queries.extend(
            [
                f"site:{official_domain} careers",
                f"site:{official_domain} jobs",
            ]
        )
    queries.extend(
        [
            f"{company_name} careers",
            f"{company_name} jobs",
        ]
    )
    for provider in ("greenhouse", "lever", "workday", "ashby", "smartrecruiters", "icims", "workable"):
        queries.append(f"{company_name} {provider} jobs")
    return dedupe_preserving_order(queries)


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
