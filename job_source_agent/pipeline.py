from __future__ import annotations

from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from .models import CompanyInput, DiscoveryResult, LinkCandidate, dataclass_to_dict
from .opening_matcher import JobOpeningMatcher, detect_provider, score_title_match
from .scoring import (
    is_ats_url,
    is_likely_job_detail,
    is_likely_job_listing_page,
    score_career_link,
    score_job_link,
)
from .web import FetchError, Fetcher, RawLink, domain_of, extract_links, normalize_url


COMMON_CAREER_PATHS = (
    "/careers",
    "/career",
    "/jobs",
    "/jobs/search",
    "/jobs/search-results",
    "/join-us",
    "/join-our-team",
    "/work-with-us",
    "/open-positions",
    "/job-openings",
    "/current-openings",
    "/opportunities",
    "/company/careers",
    "/about/careers",
    "/about-us/careers",
    "/careers/jobs",
    "/careers/listings",
    "/careers/search",
    "/en/careers",
    "/en/jobs",
)


class JobSourceAgent:
    def __init__(
        self,
        fetcher: Fetcher,
        max_candidates: int = 12,
        max_job_pages: int = 8,
        enable_sitemap_discovery: bool = True,
    ) -> None:
        self.fetcher = fetcher
        self.max_candidates = max_candidates
        self.max_job_pages = max_job_pages
        self.enable_sitemap_discovery = enable_sitemap_discovery

    def discover(self, company: CompanyInput) -> DiscoveryResult:
        result = DiscoveryResult(
            company_name=company.company_name,
            company_website_url=normalize_url(company.company_website_url),
            hiring_entity_name=company.hiring_entity_name,
            career_root_url=company.career_root_url,
            linkedin_job_url=company.linkedin_job_url,
            linkedin_company_url=company.linkedin_company_url,
            linkedin_job_title=company.job_title,
            linkedin_job_location=company.job_location,
            trace={
                "source": company.source,
                "linkedin_job_url": company.linkedin_job_url,
                "linkedin_company_url": company.linkedin_company_url,
                "linkedin_job_title": company.job_title,
                "source_trace": company.source_trace,
                "steps": [],
            },
        )
        try:
            if company.career_root_url:
                career_url = normalize_url(company.career_root_url)
                career_trace = {
                    "homepage_url": result.company_website_url,
                    "selected": {
                        "url": career_url,
                        "reason": "career root provided by company identity resolver",
                    },
                }
            else:
                career_url, career_trace = self.find_career_page(result.company_website_url)
            result.career_page_url = career_url
            result.trace["steps"].append({"name": "find_career_page", **career_trace})
            opening_url, job_list_url, opening_trace = self.find_open_position(
                career_url,
                target_title=company.job_title,
                target_location=company.job_location,
            )
            result.open_position_url = opening_url
            result.job_list_page_url = job_list_url
            result.trace["steps"].append({"name": "find_open_position", **opening_trace})
            result.status = "success" if job_list_url else "partial"
        except FetchError as exc:
            result.error = "fetch_failed"
            result.trace["failure_detail"] = str(exc)
        except DiscoveryError as exc:
            result.error = exc.code
            result.trace["failure_detail"] = str(exc)
            if exc.trace:
                result.trace["steps"].append({"name": exc.step_name, **exc.trace})
        return result

    def find_career_page(self, company_website_url: str) -> tuple[str, dict]:
        homepage_url = normalize_url(company_website_url)
        raw_candidates: list[RawLink] = []
        trace = {"homepage_url": homepage_url, "homepage_fetch_error": None, "candidates": [], "candidate_fetch_errors": []}
        try:
            homepage = self.fetcher.fetch(company_website_url)
            homepage_url = homepage.final_url or homepage.url
            raw_candidates = extract_links(homepage)
        except FetchError as exc:
            trace["homepage_fetch_error"] = str(exc)
        raw_candidates.extend(self._common_path_candidates(homepage_url))
        if self.enable_sitemap_discovery:
            sitemap_candidates, sitemap_trace = self._sitemap_candidates(homepage_url)
            raw_candidates.extend(sitemap_candidates)
            trace["sitemap_discovery"] = sitemap_trace
        else:
            trace["sitemap_discovery"] = {"skipped": True}

        scored = sorted(
            [score_career_link(link) for link in raw_candidates],
            key=lambda candidate: candidate.score,
            reverse=True,
        )
        deduped = self._dedupe_candidates(scored)
        trace["homepage_url"] = homepage_url
        trace["candidates"] = dataclass_to_dict(deduped[:10])

        for candidate in deduped[: self.max_candidates]:
            if candidate.score < 50:
                continue
            try:
                page = self.fetcher.fetch(candidate.url)
            except FetchError as exc:
                trace["candidate_fetch_errors"].append({"url": candidate.url, "error": str(exc)})
                continue
            if self._looks_like_career_page(candidate, page.html):
                trace["selected"] = dataclass_to_dict(candidate)
                trace["selected_page_source"] = page.source
                return page.final_url or page.url, trace

        raise DiscoveryError(
            "career_page_not_found",
            "No reliable career page candidate found.",
            step_name="find_career_page",
            trace=trace,
        )

    def find_open_position(
        self,
        career_page_url: str,
        target_title: str | None = None,
        target_location: str | None = None,
    ) -> tuple[str | None, str | None, dict]:
        if self._looks_like_job_detail_url(career_page_url):
            return career_page_url, career_page_url, {
                "career_page_url": career_page_url,
                "job_list_page_url": career_page_url,
                "selected": {
                    "url": career_page_url,
                    "reason": "career page is already a job-detail URL",
                },
            }

        trace = {
            "career_page_url": career_page_url,
            "job_list_page_url": career_page_url,
            "pages_visited": [],
            "candidates": [],
            "fetch_errors": [],
        }

        provider = detect_provider(career_page_url)
        if target_title and provider in {"google_careers", "meta_careers"}:
            match, match_trace = JobOpeningMatcher(self.fetcher).match(
                career_page_url,
                target_title,
                target_location,
            )
            trace["opening_matcher"] = match_trace
            if match:
                trace["selected"] = {
                    "url": match.url,
                    "title": match.title,
                    "score": match.score,
                    "provider": match.provider,
                    "reasons": match.reasons,
                }
                return match.url, match.job_list_page_url or career_page_url, trace
            if match_trace.get("fallback_search_url"):
                trace["job_list_page_url"] = match_trace["fallback_search_url"]
                trace["opening_error"] = "specific_opening_not_found"
                return None, trace["job_list_page_url"], trace

        queue = [career_page_url]
        visited: set[str] = set()
        pages_checked = 0

        while queue and pages_checked < self.max_job_pages:
            page_url = queue.pop(0)
            normalized_page_url = page_url.rstrip("/")
            if normalized_page_url in visited:
                continue
            visited.add(normalized_page_url)
            pages_checked += 1

            try:
                page = self.fetcher.fetch(page_url)
            except FetchError as exc:
                trace["fetch_errors"].append({"url": page_url, "error": str(exc)})
                continue
            actual_page_url = page.final_url or page.url
            scored = sorted(
                [score_job_link(link, actual_page_url) for link in extract_links(page)],
                key=lambda candidate: candidate.score,
                reverse=True,
            )
            deduped = self._dedupe_candidates(scored)
            if actual_page_url != career_page_url and deduped:
                trace["job_list_page_url"] = actual_page_url
            trace["pages_visited"].append(
                {
                    "url": actual_page_url,
                    "source": page.source,
                    "top_candidates": dataclass_to_dict(deduped[:8]),
                }
            )
            trace["candidates"].extend(dataclass_to_dict(deduped[:5]))

            for candidate in deduped[: self.max_candidates]:
                if candidate.score < 55:
                    continue
                if not is_likely_job_detail(candidate):
                    continue
                title_match = None
                if target_title:
                    title_score, title_reasons = score_title_match(candidate.text, target_title)
                    title_match = {"score": title_score, "reasons": title_reasons}
                    if title_score < 45:
                        continue
                try:
                    opened = self.fetcher.fetch(candidate.url)
                except FetchError:
                    continue
                trace["selected"] = dataclass_to_dict(candidate)
                if title_match:
                    trace["selected_title_match"] = title_match
                trace["job_list_page_url"] = actual_page_url
                trace["selected_page_source"] = opened.source
                return opened.final_url or opened.url, actual_page_url, trace

            for candidate in deduped[: self.max_candidates]:
                if is_likely_job_listing_page(candidate) and candidate.url.rstrip("/") not in visited:
                    queue.append(candidate.url)

        match, match_trace = JobOpeningMatcher(self.fetcher).match(
            trace["job_list_page_url"],
            target_title,
            target_location,
        )
        trace["opening_matcher"] = match_trace
        if match:
            trace["selected"] = {
                "url": match.url,
                "title": match.title,
                "score": match.score,
                "provider": match.provider,
                "reasons": match.reasons,
            }
            return match.url, match.job_list_page_url or trace["job_list_page_url"], trace

        trace["opening_error"] = "open_position_not_found"
        return None, trace["job_list_page_url"], trace

    def _common_path_candidates(self, homepage_url: str) -> list[RawLink]:
        parsed = urlparse(homepage_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        candidates = [
            RawLink(url=normalize_url(path, base), text=path.strip("/").replace("-", " "), source_url=homepage_url)
            for path in COMMON_CAREER_PATHS
        ]
        brand_label = self._brand_label_from_host(parsed.netloc)
        if brand_label:
            for path in (f"/join-{brand_label}", f"/en/join-{brand_label}"):
                candidates.append(
                    RawLink(
                        url=normalize_url(path, base),
                        text=f"careers join us {brand_label.replace('-', ' ')}",
                        source_url=homepage_url,
                    )
                )
        root_domain = parsed.netloc.lower().removeprefix("www.")
        for subdomain in ("careers", "jobs"):
            candidates.append(
                RawLink(
                    url=normalize_url(f"https://{subdomain}.{root_domain}"),
                    text="careers jobs",
                    source_url=homepage_url,
                )
            )
        return candidates

    def _sitemap_candidates(self, homepage_url: str) -> tuple[list[RawLink], dict]:
        parsed = urlparse(homepage_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        sitemap_urls = [normalize_url("/sitemap.xml", base), normalize_url("/sitemap_index.xml", base)]
        trace = {"sitemaps_checked": [], "candidate_count": 0}

        try:
            robots = self.fetcher.fetch(normalize_url("/robots.txt", base))
            for line in robots.html.splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemap_urls.append(normalize_url(line.split(":", 1)[1].strip()))
        except FetchError:
            pass

        links: list[RawLink] = []
        seen_sitemaps: set[str] = set()
        pending_sitemaps = list(sitemap_urls)
        while pending_sitemaps:
            sitemap_url = pending_sitemaps.pop(0)
            if sitemap_url in seen_sitemaps:
                continue
            seen_sitemaps.add(sitemap_url)
            try:
                page = self.fetcher.fetch(sitemap_url)
            except FetchError as exc:
                trace["sitemaps_checked"].append({"url": sitemap_url, "error": str(exc)})
                continue

            urls = self._extract_sitemap_locs(page.html)
            trace["sitemaps_checked"].append({"url": sitemap_url, "url_count": len(urls)})
            for url in urls:
                lower_url = url.lower()
                if lower_url.endswith(".xml") and len(seen_sitemaps) < 10:
                    pending_sitemaps.append(normalize_url(url))
                    continue
                if any(
                    token in lower_url
                    for token in ("career", "careers", "jobs", "join-us", "join-our-team", "join-", "openings")
                ):
                    links.append(RawLink(url=normalize_url(url), text=urlparse(url).path, source_url=sitemap_url))

        trace["candidate_count"] = len(links)
        return links, trace

    def _extract_sitemap_locs(self, xml_text: str) -> list[str]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []
        urls: list[str] = []
        for element in root.iter():
            if element.tag.endswith("loc") and element.text:
                urls.append(element.text.strip())
        return urls

    def _dedupe_candidates(self, candidates: list[LinkCandidate]) -> list[LinkCandidate]:
        seen: set[str] = set()
        deduped: list[LinkCandidate] = []
        for candidate in candidates:
            key = candidate.url.rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _looks_like_career_page(self, candidate: LinkCandidate, html: str) -> bool:
        if is_ats_url(candidate.url):
            return True
        text = html[:200000].lower()
        if self._has_ats_link(text):
            return True
        career_signals = (
            "open roles",
            "open positions",
            "current openings",
            "job openings",
            "view open jobs",
            "apply now",
            "join our team",
            "join us",
            "life at",
            "careers",
        )
        generic_job_only = candidate.score < 120 and "career keyword 'jobs'" in " ".join(candidate.reasons)
        return not generic_job_only and any(signal in text for signal in career_signals)

    def _brand_label_from_host(self, host: str) -> str | None:
        label = host.lower().split(":")[0].removeprefix("www.").split(".")[0]
        label = "".join(char if char.isalnum() else "-" for char in label).strip("-")
        return label or None

    def _has_ats_link(self, text: str) -> bool:
        ats_markers = (
            "jobs.lever.co",
            "boards.greenhouse.io",
            "job-boards.greenhouse.io",
            "ashbyhq.com",
            "apply.workable.com",
            "smartrecruiters.com",
            "myworkdayjobs.com",
            "icims.com",
        )
        return any(marker in text for marker in ats_markers)

    def _looks_like_job_detail_url(self, url: str) -> bool:
        if not is_ats_url(url):
            return False
        host = domain_of(url)
        parts = [part for part in urlparse(url).path.split("/") if part]
        if host == "jobs.lever.co":
            return len(parts) >= 2
        if "greenhouse.io" in host:
            return "jobs" in parts and len(parts) >= 3
        return len(parts) >= 2


class DiscoveryError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        step_name: str = "discovery",
        trace: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.step_name = step_name
        self.trace = trace or {}
