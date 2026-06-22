from __future__ import annotations

from urllib.parse import urlparse

from .models import CompanyInput, DiscoveryResult, LinkCandidate, dataclass_to_dict
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
    "/join-us",
    "/work-with-us",
    "/open-positions",
    "/job-openings",
    "/company/careers",
    "/about/careers",
    "/en/careers",
    "/en/jobs",
)


class JobSourceAgent:
    def __init__(self, fetcher: Fetcher, max_candidates: int = 12, max_job_pages: int = 8) -> None:
        self.fetcher = fetcher
        self.max_candidates = max_candidates
        self.max_job_pages = max_job_pages

    def discover(self, company: CompanyInput) -> DiscoveryResult:
        result = DiscoveryResult(
            company_name=company.company_name,
            company_website_url=normalize_url(company.company_website_url),
            trace={"linkedin_job_url": company.linkedin_job_url, "steps": []},
        )
        try:
            career_url, career_trace = self.find_career_page(result.company_website_url)
            result.career_page_url = career_url
            result.trace["steps"].append({"name": "find_career_page", **career_trace})
            opening_url, opening_trace = self.find_open_position(career_url)
            result.open_position_url = opening_url
            result.trace["steps"].append({"name": "find_open_position", **opening_trace})
            result.status = "success"
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
        homepage = self.fetcher.fetch(company_website_url)
        homepage_url = homepage.final_url or homepage.url
        raw_candidates = extract_links(homepage)
        raw_candidates.extend(self._common_path_candidates(homepage_url))

        scored = sorted(
            [score_career_link(link) for link in raw_candidates],
            key=lambda candidate: candidate.score,
            reverse=True,
        )
        deduped = self._dedupe_candidates(scored)
        trace = {"homepage_url": homepage_url, "candidates": dataclass_to_dict(deduped[:10])}

        for candidate in deduped[: self.max_candidates]:
            if candidate.score < 50:
                continue
            try:
                page = self.fetcher.fetch(candidate.url)
            except FetchError:
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

    def find_open_position(self, career_page_url: str) -> tuple[str, dict]:
        if self._looks_like_job_detail_url(career_page_url):
            return career_page_url, {
                "career_page_url": career_page_url,
                "selected": {
                    "url": career_page_url,
                    "reason": "career page is already a job-detail URL",
                },
            }

        trace = {"career_page_url": career_page_url, "pages_visited": [], "candidates": []}
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

            page = self.fetcher.fetch(page_url)
            actual_page_url = page.final_url or page.url
            scored = sorted(
                [score_job_link(link, actual_page_url) for link in extract_links(page)],
                key=lambda candidate: candidate.score,
                reverse=True,
            )
            deduped = self._dedupe_candidates(scored)
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
                try:
                    opened = self.fetcher.fetch(candidate.url)
                except FetchError:
                    continue
                trace["selected"] = dataclass_to_dict(candidate)
                trace["selected_page_source"] = opened.source
                return opened.final_url or opened.url, trace

            for candidate in deduped[: self.max_candidates]:
                if is_likely_job_listing_page(candidate) and candidate.url.rstrip("/") not in visited:
                    queue.append(candidate.url)

        raise DiscoveryError(
            "open_position_not_found",
            "No reliable open position URL found.",
            step_name="find_open_position",
            trace=trace,
        )

    def _common_path_candidates(self, homepage_url: str) -> list[RawLink]:
        parsed = urlparse(homepage_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        return [
            RawLink(url=normalize_url(path, base), text=path.strip("/").replace("-", " "), source_url=homepage_url)
            for path in COMMON_CAREER_PATHS
        ]

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
        text = html[:20000].lower()
        career_signals = ("open roles", "open positions", "jobs", "careers", "join our team")
        return candidate.score >= 80 or any(signal in text for signal in career_signals)

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
