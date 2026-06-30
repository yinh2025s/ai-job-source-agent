from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from .web import FetchError, Fetcher, domain_of, normalize_url


SEARCH_ENDPOINT = "https://www.bing.com/search"

BLOCKED_DOMAINS = {
    "linkedin.com",
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
    "pitchbook.com",
    "zoominfo.com",
    "rocketreach.co",
    "github.com",
    "bing.com",
    "microsoft.com",
}

BLOCKED_DOMAIN_PARTS = (
    "linkedin.",
    "greenhouse.io",
    "lever.co",
    "workdayjobs.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
)


@dataclass
class WebsiteCandidate:
    url: str
    score: int
    reasons: list[str] = field(default_factory=list)


class CompanyWebsiteResolver:
    def __init__(self, fetcher: Fetcher, overrides_path: str | Path | None = None) -> None:
        self.fetcher = fetcher
        self.overrides = self._load_overrides(overrides_path)

    def resolve(self, company_name: str, linkedin_company_url: str | None = None) -> tuple[str | None, dict]:
        normalized_name = normalize_company_key(company_name)
        trace = {"company_name": company_name, "linkedin_company_url": linkedin_company_url, "candidates": []}

        if normalized_name in self.overrides:
            url = normalize_url(self.overrides[normalized_name])
            trace["selected"] = {"url": url, "reason": "override"}
            return url, trace

        linkedin_candidates = self._linkedin_company_candidates(linkedin_company_url)
        if linkedin_candidates:
            linkedin_scored = sorted(
                [self._score_candidate(candidate, company_name) for candidate in linkedin_candidates[:5]],
                key=lambda candidate: candidate.score,
                reverse=True,
            )
            trace["candidates"] = [
                {"url": candidate.url, "score": candidate.score, "reasons": candidate.reasons}
                for candidate in linkedin_scored[:5]
            ]
            if linkedin_scored and linkedin_scored[0].score >= 10:
                selected = linkedin_scored[0]
                trace["selected"] = {
                    "url": selected.url,
                    "score": selected.score,
                    "reasons": selected.reasons + ["selected from LinkedIn company page"],
                }
                return selected.url, trace

        search_candidates = self._search_candidates(company_name)
        guessed_candidates = self._guess_domain_candidates(company_name)
        all_candidates = dedupe_urls(linkedin_candidates + search_candidates + guessed_candidates)
        scored = sorted(
            [self._score_candidate(candidate, company_name) for candidate in all_candidates],
            key=lambda candidate: candidate.score,
            reverse=True,
        )
        trace["candidates"] = [
            {"url": candidate.url, "score": candidate.score, "reasons": candidate.reasons}
            for candidate in scored[:10]
        ]

        for candidate in scored:
            if candidate.score < 25:
                continue
            trace["selected"] = {
                "url": candidate.url,
                "score": candidate.score,
                "reasons": candidate.reasons,
            }
            return candidate.url, trace

        return None, trace

    def _search_candidates(self, company_name: str) -> list[str]:
        query = urlencode({"q": f"{company_name} official website"})
        try:
            page = self.fetcher.fetch(f"{SEARCH_ENDPOINT}?{query}")
        except FetchError:
            return []
        parser = _SearchResultParser()
        parser.feed(page.html)
        raw_urls = parser.urls + re.findall(r"https?://[^\"'<>\s)]+", page.html)
        urls: list[str] = []
        seen: set[str] = set()
        for url in raw_urls:
            cleaned = clean_search_url(url)
            if not cleaned or is_blocked_domain(cleaned):
                continue
            domain = domain_of(cleaned)
            if domain in seen:
                continue
            seen.add(domain)
            urls.append(cleaned)
        return urls

    def _linkedin_company_candidates(self, linkedin_company_url: str | None) -> list[str]:
        if not linkedin_company_url:
            return []
        try:
            page = self.fetcher.fetch(linkedin_company_url)
        except FetchError:
            return []
        urls: list[str] = []
        for url in re.findall(r"https?://[^\"'<>\s)\\]+", page.html):
            cleaned = clean_search_url(url)
            if not cleaned or is_blocked_domain(cleaned):
                continue
            urls.append(cleaned)
        return urls

    def _guess_domain_candidates(self, company_name: str) -> list[str]:
        tokens = tokenize_company_name(company_name)
        if not tokens:
            return []
        compact = "".join(tokens)
        dashed = "-".join(tokens)
        prefixes = ["", "www.", "get", "go", "try", "join"]
        tlds = [".com", ".ai", ".io", ".co", ".org", ".tech"]
        bases = [compact]
        if dashed != compact:
            bases.append(dashed)
        urls: list[str] = []
        for base in bases:
            for tld in tlds[:4]:
                urls.append(f"https://{base}{tld}")
            for prefix in prefixes[2:4]:
                urls.append(f"https://{prefix}{base}.com")
        return urls

    def _score_candidate(self, url: str, company_name: str) -> WebsiteCandidate:
        score = 0
        reasons: list[str] = []
        domain = domain_of(url)
        company_tokens = tokenize_company_name(company_name)

        for token in company_tokens:
            if token and token in domain:
                score += 35
                reasons.append(f"company token '{token}' in domain")

        if domain.endswith((".com", ".ai", ".io", ".co", ".org")):
            score += 10
            reasons.append("credible company TLD")

        try:
            page = self.fetcher.fetch(url)
        except FetchError:
            if domain.endswith(".com"):
                score += 10
                reasons.append("preferred .com domain despite fetch failure")
            score -= 20
            reasons.append("homepage fetch failed")
            return WebsiteCandidate(url, score, reasons)

        html_head = page.html[:5000].lower()
        token_in_homepage = False
        for token in company_tokens:
            if token and token in html_head:
                score += 15
                token_in_homepage = True
                reasons.append(f"company token '{token}' in homepage")
        if not token_in_homepage and company_tokens:
            score -= 35
            reasons.append("company token missing from homepage")

        return WebsiteCandidate(page.final_url or page.url, score, reasons)

    def _load_overrides(self, path: str | Path | None) -> dict[str, str]:
        if not path:
            return {}
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {normalize_company_key(key): value for key, value in data.items()}


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


def normalize_company_key(company_name: str) -> str:
    return " ".join(tokenize_company_name(company_name))


def tokenize_company_name(company_name: str) -> list[str]:
    cleaned = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|technology)\b", "", company_name, flags=re.I)
    return [token.lower() for token in re.findall(r"[A-Za-z0-9]+", cleaned) if len(token) >= 3]


def clean_search_url(url: str) -> str:
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
    return normalize_url(f"{parsed.scheme}://{parsed.netloc}")


def is_blocked_domain(url: str) -> bool:
    domain = domain_of(url)
    if any(domain == blocked or domain.endswith("." + blocked) for blocked in BLOCKED_DOMAINS):
        return True
    return any(part in domain for part in BLOCKED_DOMAIN_PARTS)


def dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for url in urls:
        domain = domain_of(url)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        deduped.append(url)
    return deduped
