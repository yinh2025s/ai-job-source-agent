from __future__ import annotations

import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urlencode, urlparse, urlunparse

from .contracts import FetchClient
from .linkedin import parse_visible_external_apply_url, sanitize_public_external_apply_url
from .models import CompanyInput
from .web import FetchError, normalize_url


LINKEDIN_JOBS_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


@dataclass
class LinkedInJobPosting:
    job_id: str
    job_title: str
    company_name: str
    linkedin_job_url: str
    linkedin_company_url: str
    location: str = ""
    external_apply_url: str = ""
    source_trace: dict[str, Any] | None = None


@dataclass(frozen=True)
class LinkedInSearchQuery:
    keywords: str
    location: str = "United States"


class LinkedInJobsDiscoverer:
    def __init__(self, fetcher: FetchClient) -> None:
        self.fetcher = fetcher

    def search(
        self,
        keywords: str,
        location: str = "United States",
        limit: int = 10,
        pages: int = 2,
    ) -> list[LinkedInJobPosting]:
        postings: list[LinkedInJobPosting] = []
        seen_jobs: set[str] = set()

        for page_index in range(max(pages, 1)):
            if len(postings) >= limit:
                break
            url = self._search_url(keywords, location, start=page_index * 25)
            page = self.fetcher.fetch(url)
            for posting in parse_linkedin_job_cards(page.html):
                if posting.job_id in seen_jobs:
                    continue
                seen_jobs.add(posting.job_id)
                postings.append(posting)
                if len(postings) >= limit:
                    break

        return postings

    def collect_benchmark_cohort(
        self,
        queries: Iterable[LinkedInSearchQuery],
        *,
        cohort_size: int = 100,
        per_query_limit: int = 100,
        pages: int = 4,
    ) -> list[LinkedInJobPosting]:
        """Collect an ordered cohort of distinct postings, retaining company repeats."""

        if cohort_size < 0 or per_query_limit < 0:
            raise ValueError("LinkedIn cohort limits must be non-negative")
        cohort: list[LinkedInJobPosting] = []
        seen_jobs: set[str] = set()
        for query_index, query in enumerate(queries):
            if len(cohort) >= cohort_size:
                break
            postings = self.search(
                keywords=query.keywords,
                location=query.location,
                limit=per_query_limit,
                pages=pages,
            )
            for posting in postings:
                if posting.job_id in seen_jobs:
                    continue
                seen_jobs.add(posting.job_id)
                trace = dict(posting.source_trace or {})
                trace["benchmark_collection"] = {
                    "cohort_ordinal": len(cohort),
                    "distinct_by": "linkedin_job_id",
                    "evidence_source": "public_search_card",
                    "query_index": query_index,
                    "keywords": query.keywords,
                    "location": query.location,
                }
                cohort.append(replace(posting, source_trace=trace))
                if len(cohort) >= cohort_size:
                    break
        return cohort

    def _search_url(self, keywords: str, location: str, start: int) -> str:
        query = urlencode({"keywords": keywords, "location": location, "start": start})
        return f"{LINKEDIN_JOBS_ENDPOINT}?{query}"


def linkedin_postings_to_company_inputs(
    postings: list[LinkedInJobPosting],
    *,
    preserve_job_postings: bool = False,
) -> list[CompanyInput]:
    """Convert cards, optionally retaining distinct postings from the same company."""

    companies: list[CompanyInput] = []
    seen_identities: set[str] = set()
    for posting in postings:
        identity = posting.job_id.strip() if preserve_job_postings else posting.company_name.lower().strip()
        if not identity or identity in seen_identities:
            continue
        seen_identities.add(identity)
        source_trace = dict(posting.source_trace or {})
        source_trace["linkedin_posting"] = {
            "availability": "listed",
            "apply_mode": "unknown",
            "evidence_source": "public_search_card",
            "job_url": posting.linkedin_job_url,
        }
        companies.append(
            CompanyInput(
                linkedin_job_url=posting.linkedin_job_url,
                linkedin_company_url=posting.linkedin_company_url,
                external_apply_url=posting.external_apply_url or None,
                company_name=posting.company_name,
                job_title=posting.job_title,
                job_location=posting.location,
                source="linkedin_public_jobs",
                source_trace=source_trace,
            )
        )
    return companies


def enrich_public_external_apply_urls(
    postings: list[LinkedInJobPosting],
    fetcher: FetchClient,
    *,
    max_detail_fetches: int = 100,
) -> list[LinkedInJobPosting]:
    """Bound public detail fetches and retain only visible, sanitized apply links."""

    if max_detail_fetches < 0:
        raise ValueError("max_detail_fetches must be non-negative")
    enriched: list[LinkedInJobPosting] = []
    fetch_count = 0
    for posting in postings:
        trace = dict(posting.source_trace or {})
        detail_trace: dict[str, Any] = {
            "evidence_source": "public_job_detail",
            "job_url": posting.linkedin_job_url,
        }
        external_apply_url = sanitize_public_external_apply_url(posting.external_apply_url)
        if external_apply_url:
            detail_trace.update(status="found", reason="already_present")
        elif not _safe_linkedin_path_url(posting.linkedin_job_url, "jobs/view"):
            detail_trace.update(status="unavailable", reason="unsafe_job_url")
        elif fetch_count >= max_detail_fetches:
            detail_trace.update(status="not_attempted", reason="fetch_limit_reached")
        else:
            fetch_count += 1
            try:
                page = fetcher.fetch(posting.linkedin_job_url)
            except (FetchError, OSError, RuntimeError, ValueError):
                detail_trace.update(status="unavailable", reason="fetch_failed")
            else:
                external_apply_url = parse_visible_external_apply_url(page.html)
                if external_apply_url:
                    detail_trace.update(status="found", reason="visible_external_apply_link")
                else:
                    detail_trace.update(status="unavailable", reason="no_visible_external_apply_link")
        trace["linkedin_job_detail"] = detail_trace
        enriched.append(
            replace(
                posting,
                external_apply_url=external_apply_url,
                source_trace=trace,
            )
        )
    return enriched


def parse_linkedin_job_cards(html: str) -> list[LinkedInJobPosting]:
    parser = _LinkedInJobsParser()
    parser.feed(html)
    return [
        posting
        for posting in parser.postings
        if posting.job_id and posting.job_title and posting.company_name and posting.linkedin_job_url
    ]


class _LinkedInJobsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.postings: list[LinkedInJobPosting] = []
        self._card_depth = 0
        self._card: dict[str, str] | None = None
        self._capture: str | None = None
        self._capture_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        classes = attrs_dict.get("class", "")

        if tag == "div" and "job-search-card" in classes:
            self._card_depth = 1
            self._card = {
                "job_id": attrs_dict.get("data-entity-urn", "").split(":")[-1],
                "job_title": "",
                "company_name": "",
                "linkedin_job_url": "",
                "linkedin_company_url": "",
                "location": "",
                "external_apply_url": "",
            }
            return

        if self._card is None:
            return

        self._card_depth += 1
        if tag == "a" and "base-card__full-link" in classes:
            self._card["linkedin_job_url"] = _safe_linkedin_path_url(attrs_dict.get("href", ""), "jobs/view")
        elif tag == "a" and "hidden-nested-link" in classes:
            self._card["linkedin_company_url"] = _safe_linkedin_path_url(attrs_dict.get("href", ""), "company")
            self._start_capture("company_name")
        elif tag == "h3" and "base-search-card__title" in classes:
            self._start_capture("job_title")
        elif tag == "span" and "job-search-card__location" in classes:
            self._start_capture("location")

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._capture_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._card is None:
            return

        if self._capture and tag in {"a", "h3", "span"}:
            self._card[self._capture] = " ".join("".join(self._capture_text).split())
            self._capture = None
            self._capture_text = []

        self._card_depth -= 1
        if self._card_depth <= 0:
            self.postings.append(LinkedInJobPosting(**self._card))
            self._card = None
            self._card_depth = 0

    def _start_capture(self, key: str) -> None:
        self._capture = key
        self._capture_text = []


def _safe_linkedin_path_url(url: str, path_kind: str) -> str:
    try:
        normalized = normalize_url(url)
        parsed = urlparse(normalized)
        _ = parsed.port
    except (TypeError, ValueError):
        return ""
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or (host != "linkedin.com" and not host.endswith(".linkedin.com")):
        return ""
    if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        return ""
    pattern = r"^/jobs/view/[^/?#]+/?$" if path_kind == "jobs/view" else r"^/company/[^/?#]+/?$"
    if not re.match(pattern, parsed.path):
        return ""
    return urlunparse((parsed.scheme, "www.linkedin.com", parsed.path, "", "", ""))
