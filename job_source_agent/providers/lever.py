from __future__ import annotations

import json
from urllib.parse import quote, urlparse, urlunparse

from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


class LeverAdapter:
    name = "lever"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        try:
            return (urlparse(url).hostname or "").lower() == "jobs.lever.co"
        except (TypeError, ValueError):
            return False

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        parts = [part for part in urlparse(url).path.split("/") if part]
        if not parts:
            return None
        return JobBoard(
            url=f"https://jobs.lever.co/{parts[0]}",
            provider=self.name,
            identifier=parts[0],
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not board.identifier:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "missing Lever account identifier"},
            )

        api_url = self.api_url(board.identifier)
        page = fetcher.fetch(api_url)
        try:
            data = json.loads(page.html)
        except (json.JSONDecodeError, TypeError):
            return self._invalid_response(board, api_url, page.source)
        if not isinstance(data, list):
            return self._invalid_response(board, api_url, page.source)

        candidates = [candidate for job in data if (candidate := _candidate_from_job(job))]
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            trace={
                "adapter": self.name,
                "api_urls": [api_url],
                "response_source": page.source,
                "posting_count": len(data),
                "candidate_count": len(candidates),
            },
        )

    def _invalid_response(
        self,
        board: JobBoard,
        api_url: str,
        response_source: str,
    ) -> AdapterResult:
        return AdapterResult(
            provider=self.name,
            board=board,
            reason_code="INVALID_STRUCTURED_DATA",
            trace={
                "adapter": self.name,
                "api_urls": [api_url],
                "response_source": response_source,
            },
        )

    @staticmethod
    def api_url(board_identifier: str) -> str:
        identifier = quote(board_identifier.strip(), safe="")
        return f"https://api.lever.co/v0/postings/{identifier}?mode=json"


def _candidate_from_job(job: object) -> JobCandidate | None:
    if not isinstance(job, dict):
        return None
    title = str(job.get("text") or "").strip()
    url = _posting_url(job.get("hostedUrl")) or _posting_url(job.get("applyUrl"))
    if not title or not url:
        return None

    return JobCandidate(
        title=title,
        url=url,
        provider="lever",
        location=_location_name(job),
        raw={
            "id": job.get("id"),
            "hosted_url": _posting_url(job.get("hostedUrl")),
            "apply_url": _posting_url(job.get("applyUrl")),
        },
    )


def _posting_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlparse(value.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return None
        return urlunparse(parsed._replace(fragment=""))
    except ValueError:
        return None


def _location_name(job: dict) -> str | None:
    categories = job.get("categories")
    if not isinstance(categories, dict):
        return None
    location = categories.get("location")
    if isinstance(location, str) and location.strip():
        return location.strip()
    return None


ADAPTER = LeverAdapter()
