from __future__ import annotations

import json
from urllib.parse import quote, unquote, urlparse

from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


class AshbyAdapter:
    name = "ashby"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        try:
            return (urlparse(url).hostname or "").lower() == "jobs.ashbyhq.com"
        except ValueError:
            return False

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        parts = [unquote(part) for part in urlparse(url).path.split("/") if part]
        if not parts:
            return None
        return JobBoard(url=url, provider=self.name, identifier=parts[0])

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not board.identifier:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "missing Ashby board identifier"},
            )

        api_url = self.api_url(board.identifier)
        page = fetcher.fetch(api_url)
        try:
            data = json.loads(page.html)
        except (json.JSONDecodeError, TypeError):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="INVALID_STRUCTURED_DATA",
                trace={"adapter": self.name, "api_urls": [api_url]},
            )

        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="INVALID_STRUCTURED_DATA",
                trace={"adapter": self.name, "api_urls": [api_url]},
            )

        candidates = [candidate for job in jobs if (candidate := _candidate(job)) is not None]
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            trace={
                "adapter": self.name,
                "api_urls": [api_url],
                "response_source": page.source,
                "candidate_count": len(candidates),
            },
        )

    @staticmethod
    def api_url(board_identifier: str) -> str:
        identifier = quote(board_identifier, safe="")
        return f"https://api.ashbyhq.com/posting-api/job-board/{identifier}"


def _candidate(job: object) -> JobCandidate | None:
    if not isinstance(job, dict):
        return None
    title = job.get("title")
    job_url = job.get("jobUrl")
    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(job_url, str) or not job_url.strip():
        return None
    return JobCandidate(
        title=title.strip(),
        url=job_url.strip(),
        provider="ashby",
        location=_location_name(job.get("location")),
        raw={"id": job.get("id")},
    )


def _location_name(location: object) -> str | None:
    if isinstance(location, str) and location.strip():
        return location.strip()
    if isinstance(location, dict):
        name = location.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


ADAPTER = AshbyAdapter()
