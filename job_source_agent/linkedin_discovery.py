from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlencode

from .models import CompanyInput
from .web import Fetcher, normalize_url


LINKEDIN_JOBS_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


@dataclass
class LinkedInJobPosting:
    job_id: str
    job_title: str
    company_name: str
    linkedin_job_url: str
    linkedin_company_url: str
    location: str = ""


class LinkedInJobsDiscoverer:
    def __init__(self, fetcher: Fetcher) -> None:
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

    def _search_url(self, keywords: str, location: str, start: int) -> str:
        query = urlencode({"keywords": keywords, "location": location, "start": start})
        return f"{LINKEDIN_JOBS_ENDPOINT}?{query}"


def linkedin_postings_to_company_inputs(postings: list[LinkedInJobPosting]) -> list[CompanyInput]:
    companies: list[CompanyInput] = []
    seen_companies: set[str] = set()
    for posting in postings:
        company_key = posting.company_name.lower().strip()
        if not company_key or company_key in seen_companies:
            continue
        seen_companies.add(company_key)
        companies.append(
            CompanyInput(
                linkedin_job_url=posting.linkedin_job_url,
                linkedin_company_url=posting.linkedin_company_url,
                company_name=posting.company_name,
                job_title=posting.job_title,
                job_location=posting.location,
                source="linkedin_public_jobs",
            )
        )
    return companies


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
            }
            return

        if self._card is None:
            return

        self._card_depth += 1
        if tag == "a" and "base-card__full-link" in classes:
            self._card["linkedin_job_url"] = normalize_url(attrs_dict.get("href", ""))
        elif tag == "a" and "hidden-nested-link" in classes:
            self._card["linkedin_company_url"] = normalize_url(attrs_dict.get("href", ""))
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
