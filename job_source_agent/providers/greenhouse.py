from __future__ import annotations

import json
from urllib.parse import urlparse

from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


class GreenhouseAdapter:
    name = "greenhouse"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return "greenhouse.io" in (urlparse(url).hostname or "").lower()

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        parts = [part for part in urlparse(url).path.split("/") if part]
        if not parts:
            return None
        return JobBoard(url=url, provider=self.name, identifier=parts[0])

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not board.identifier:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "missing Greenhouse board identifier"},
            )
        api_url = self.api_url(board.identifier)
        page = fetcher.fetch(api_url)
        try:
            data = json.loads(page.html)
        except json.JSONDecodeError:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="INVALID_STRUCTURED_DATA",
                trace={"adapter": self.name, "api_urls": [api_url]},
            )

        candidates = [
            JobCandidate(
                title=str(job.get("title") or ""),
                url=str(job.get("absolute_url") or ""),
                provider=self.name,
                location=_location_name(job),
                raw={"id": job.get("id")},
            )
            for job in data.get("jobs", [])
            if job.get("title") and job.get("absolute_url")
        ]
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
        return f"https://boards-api.greenhouse.io/v1/boards/{board_identifier}/jobs?content=true"


def _location_name(job: dict) -> str | None:
    location = job.get("location")
    if isinstance(location, dict) and location.get("name"):
        return str(location["name"])
    return None
