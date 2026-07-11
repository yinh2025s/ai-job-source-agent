from __future__ import annotations

import json
import re
from base64 import urlsafe_b64decode
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html import unescape as html_unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from xml.etree import ElementTree as ET

from .web import FetchError, Fetcher, domain_of, normalize_url


SEARCH_ENDPOINT = "https://www.bing.com/search"
DUCKDUCKGO_SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"

BLOCKED_DOMAINS = {
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
    "pitchbook.com",
    "zoominfo.com",
    "rocketreach.co",
    "github.com",
    "bing.com",
    "microsoft.com",
    "static.licdn.com",
    "media.licdn.com",
    "dms.licdn.com",
    "w3.org",
    "schema.org",
    "schemas.live.com",
    "storage.live.com",
    "challenges.cloudflare.com",
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
    def __init__(
        self,
        fetcher: Fetcher,
        overrides_path: str | Path | None = None,
        verify_limit: int = 3,
    ) -> None:
        self.fetcher = fetcher
        self.overrides = self._load_overrides(overrides_path)
        self.verify_limit = verify_limit

    def resolve(self, company_name: str, linkedin_company_url: str | None = None) -> tuple[str | None, dict]:
        normalized_name = normalize_company_key(company_name)
        trace = {"company_name": company_name, "linkedin_company_url": linkedin_company_url, "candidates": []}

        if normalized_name in self.overrides:
            url = normalize_url(self.overrides[normalized_name])
            trace["selected"] = {"url": url, "reason": "override"}
            return url, trace

        guessed_candidates = self._guess_domain_candidates(company_name)

        fast_candidates = dedupe_urls(
            self._linkedin_slug_domain_candidates(linkedin_company_url) + guessed_candidates[:6]
        )
        fast_scored = self._rank_and_verify_candidates(
            fast_candidates,
            company_name,
            linkedin_company_url,
        )
        trace["candidates"].extend(
            {"url": candidate.url, "score": candidate.score, "reasons": candidate.reasons}
            for candidate in fast_scored[:10]
        )
        fast_selected = self._select_verified_candidate(fast_scored, require_fast_confidence=True)
        if fast_selected:
            trace["selected"] = {
                "url": fast_selected.url,
                "score": fast_selected.score,
                "reasons": fast_selected.reasons + ["fast verified domain"],
            }
            return fast_selected.url, trace

        linkedin_candidates = self._linkedin_company_candidates(linkedin_company_url)
        search_candidates = self._search_candidates(company_name)
        all_candidates = dedupe_urls(linkedin_candidates[:5] + search_candidates[:5] + guessed_candidates[:6])
        scored = self._rank_and_verify_candidates(
            all_candidates,
            company_name,
            linkedin_company_url,
        )
        seen_domains = {domain_of(str(item.get("url") or "")) for item in trace["candidates"]}
        trace["candidates"].extend(
            {"url": candidate.url, "score": candidate.score, "reasons": candidate.reasons}
            for candidate in scored[:10]
            if domain_of(candidate.url) not in seen_domains
        )

        selected = self._select_verified_candidate(scored)
        if selected:
            trace["selected"] = {
                "url": selected.url,
                "score": selected.score,
                "reasons": selected.reasons,
            }
            return selected.url, trace

        return None, trace

    def _rank_and_verify_candidates(
        self,
        candidates: list[str],
        company_name: str,
        linkedin_company_url: str | None,
    ) -> list[WebsiteCandidate]:
        base_scored = [
            self._score_candidate(
                candidate,
                company_name,
                linkedin_company_url=linkedin_company_url,
                verify=False,
            )
            for candidate in candidates
        ]
        base_scored.sort(key=lambda candidate: candidate.score, reverse=True)

        verify_count = min(self.verify_limit, len(base_scored))
        to_verify = base_scored[:verify_count]
        if to_verify:
            with ThreadPoolExecutor(max_workers=verify_count, thread_name_prefix="website-verify") as executor:
                verified = list(
                    executor.map(
                        lambda candidate: self._score_candidate(
                            candidate.url,
                            company_name,
                            linkedin_company_url=linkedin_company_url,
                            verify=True,
                        ),
                        to_verify,
                    )
                )
        else:
            verified = []
        refined = verified + base_scored[verify_count:]
        return sorted(refined, key=lambda candidate: candidate.score, reverse=True)

    def _search_candidates(self, company_name: str) -> list[str]:
        query = urlencode({"q": f"{company_name} official website", "setlang": "en-us", "cc": "us"})
        rss_query = urlencode(
            {"q": f"{company_name} official website", "format": "rss", "setlang": "en-us", "cc": "us"}
        )
        urls: list[str] = []
        seen: set[str] = set()
        searches = (
            (f"{SEARCH_ENDPOINT}?{rss_query}", _bing_rss_urls),
            (f"{SEARCH_ENDPOINT}?{query}", _bing_html_urls),
            (f"{DUCKDUCKGO_SEARCH_ENDPOINT}?{query}", _duckduckgo_html_urls),
        )
        for search_url, extract_urls in searches:
            try:
                page = self.fetcher.fetch(search_url)
            except FetchError:
                continue
            raw_urls = extract_urls(page.html)
            for url in raw_urls:
                cleaned = clean_search_url(url)
                if not cleaned or is_blocked_domain(cleaned):
                    continue
                domain = domain_of(cleaned)
                if domain in seen:
                    continue
                seen.add(domain)
                urls.append(cleaned)
            if urls:
                break
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

    def _linkedin_slug_domain_candidates(self, linkedin_company_url: str | None) -> list[str]:
        if not linkedin_company_url:
            return []
        path_parts = [part for part in urlparse(linkedin_company_url).path.split("/") if part]
        if len(path_parts) < 2 or path_parts[0] != "company":
            return []
        slug = re.sub(r"[^a-z0-9-]", "", path_parts[1].lower())
        if not slug:
            return []
        base = re.sub(r"-(inc|llc|ltd|corp|corporation|company|co)$", "", slug)
        base = re.sub(r"(inc|llc|ltd|corp|corporation|company|co|hq)$", "", base)
        compact = base.replace("-", "")
        candidates = [base, compact]
        return [
            f"https://{candidate}.{tld}"
            for candidate in dict.fromkeys(candidates)
            if len(candidate) >= 3
            for tld in ("com", "ai", "io", "co")
        ]

    def _select_verified_candidate(
        self,
        scored: list[WebsiteCandidate],
        require_fast_confidence: bool = False,
    ) -> WebsiteCandidate | None:
        for candidate in scored:
            if candidate.score < 25:
                continue
            if "homepage verified" not in candidate.reasons:
                continue
            if require_fast_confidence and not (
                "preferred .com TLD" in candidate.reasons
                or "LinkedIn company slug matches domain TLD" in candidate.reasons
                or "homepage canonical URL" in candidate.reasons
            ):
                continue
            return candidate
        return None

    def _score_candidate(
        self,
        url: str,
        company_name: str,
        linkedin_company_url: str | None = None,
        verify: bool = True,
    ) -> WebsiteCandidate:
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
        if domain.endswith(".com"):
            score += 8
            reasons.append("preferred .com TLD")

        slug_tld_score = self._score_linkedin_slug_tld_hint(domain, company_tokens, linkedin_company_url)
        if slug_tld_score:
            score += slug_tld_score
            reasons.append("LinkedIn company slug matches domain TLD")

        if not verify:
            reasons.append("domain-only score")
            return WebsiteCandidate(url, score, reasons)

        try:
            page = self.fetcher.fetch(url)
        except FetchError:
            if domain.endswith(".com"):
                score += 10
                reasons.append("preferred .com domain despite fetch failure")
            score -= 20
            reasons.append("homepage fetch failed")
            return WebsiteCandidate(url, score, reasons)

        resolved_url = page.final_url or page.url
        reasons.append("homepage verified")
        canonical_url = _canonical_company_url(page.html, resolved_url, company_tokens)
        if canonical_url:
            resolved_url = canonical_url
            reasons.append("homepage canonical URL")

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

        return WebsiteCandidate(resolved_url, score, reasons)

    def _score_linkedin_slug_tld_hint(
        self,
        domain: str,
        company_tokens: list[str],
        linkedin_company_url: str | None,
    ) -> int:
        if not linkedin_company_url or not company_tokens:
            return 0
        path_parts = [part for part in urlparse(linkedin_company_url).path.split("/") if part]
        if len(path_parts) < 2 or path_parts[0] != "company":
            return 0
        slug = re.sub(r"[^a-z0-9]", "", path_parts[1].lower())
        compact_name = "".join(company_tokens)
        domain_parts = domain.split(".")
        if len(domain_parts) < 2:
            return 0
        domain_label, tld = domain_parts[-2], domain_parts[-1]
        if domain_label == compact_name and slug == f"{compact_name}{tld}":
            return 18
        return 0

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


class _DuckDuckGoResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag == "a" and "result__a" in attrs_dict.get("class", "") and attrs_dict.get("href"):
            self.urls.append(attrs_dict["href"])


def _bing_rss_urls(body: str) -> list[str]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    return [
        link.text.strip()
        for item in root.findall(".//item")
        for link in item.findall("link")
        if link.text and link.text.strip()
    ]


def _bing_html_urls(body: str) -> list[str]:
    parser = _SearchResultParser()
    parser.feed(body)
    return parser.urls + re.findall(r"https?://[^\"'<>\s)]+", body)


def _duckduckgo_html_urls(body: str) -> list[str]:
    parser = _DuckDuckGoResultParser()
    parser.feed(body)
    return parser.urls + re.findall(r"https?://[^\"'<>\s)]+", body)


class _CanonicalLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "link" or self.href:
            return
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        rel_values = {value.lower() for value in attrs_dict.get("rel", "").split()}
        if "canonical" in rel_values and attrs_dict.get("href"):
            self.href = attrs_dict["href"]


def _canonical_company_url(html: str, base_url: str, company_tokens: list[str]) -> str | None:
    parser = _CanonicalLinkParser()
    parser.feed(html[:100000])
    if not parser.href:
        return None
    canonical_url = normalize_url(parser.href, base_url)
    canonical_domain = domain_of(canonical_url)
    if not canonical_domain or is_blocked_domain(canonical_url):
        return None
    if company_tokens and not any(token in canonical_domain for token in company_tokens):
        return None
    return canonical_url


def normalize_company_key(company_name: str) -> str:
    return " ".join(tokenize_company_name(company_name))


def tokenize_company_name(company_name: str) -> list[str]:
    cleaned = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|technology)\b", "", company_name, flags=re.I)
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", cleaned)
        if len(token) >= 3 or token.isdigit()
    ]


def clean_search_url(url: str) -> str:
    url = html_unescape(url)
    parsed = urlparse(url)
    if parsed.path.startswith("/ck/a"):
        values = parse_qs(parsed.query)
        if values.get("u"):
            url = values["u"][0]
            if url.startswith("a1"):
                encoded = url[2:]
                try:
                    url = urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
                except (UnicodeDecodeError, ValueError):
                    return ""
            else:
                url = unquote(url)
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com"):
        redirect_url = parse_qs(parsed.query).get("uddg", [""])[0]
        if redirect_url:
            url = unquote(redirect_url)
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
