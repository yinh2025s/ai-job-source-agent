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
    def __init__(self, fetcher: Fetcher, max_results: int = 8) -> None:
        self.fetcher = fetcher
        self.max_results = max_results

    def search(self, company_name: str, company_website_url: str) -> CareerSearchResult:
        query = urlencode({"q": f"{company_name} careers jobs"})
        search_url = f"{SEARCH_ENDPOINT}?{query}"
        trace = {"query_url": search_url, "candidates": [], "error": None}
        try:
            page = self.fetcher.fetch(search_url)
        except FetchError as exc:
            trace["error"] = str(exc)
            return CareerSearchResult([], trace)

        parser = _SearchResultParser()
        parser.feed(page.html)
        raw_urls = parser.urls + re.findall(r"https?://[^\"'<>\s)]+", page.html)
        official_domain = domain_of(company_website_url)
        candidates: list[LinkCandidate] = []
        seen: set[str] = set()
        for raw_url in raw_urls:
            cleaned = clean_search_result_url(raw_url)
            if not cleaned or cleaned.rstrip("/") in seen:
                continue
            seen.add(cleaned.rstrip("/"))
            if _is_blocked(cleaned) or is_resource_url(cleaned):
                continue
            link = RawLink(url=cleaned, text=cleaned, source_url=search_url)
            candidate = score_career_link(link)
            candidate.score += _search_bonus(cleaned, official_domain)
            if candidate.score < 60:
                continue
            candidates.append(candidate)

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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag == "h2":
            self._in_h2 = True
        elif tag == "a" and self._in_h2 and attrs_dict.get("href"):
            self.urls.append(attrs_dict["href"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2":
            self._in_h2 = False


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


def _search_bonus(url: str, official_domain: str) -> int:
    score = 0
    domain = domain_of(url)
    path = urlparse(url).path.lower()
    if official_domain and (domain == official_domain or domain.endswith("." + official_domain)):
        score += 80
    if is_ats_url(url):
        score += 80
    if domain.startswith(("careers.", "jobs.")):
        score += 55
    if any(part in path for part in ("/careers", "/career", "/jobs", "/join", "/openings")):
        score += 45
    return score
