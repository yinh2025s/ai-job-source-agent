from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


class BambooHRAdapter:
    name = "bamboohr"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            hostname = (parsed.hostname or "").lower()
        except ValueError:
            return False
        tenant = hostname.removesuffix(".bamboohr.com")
        path_parts = [part.lower() for part in parsed.path.split("/") if part]
        return (
            hostname.endswith(".bamboohr.com")
            and bool(tenant)
            and "." not in tenant
            and bool(path_parts)
            and path_parts[0] == "careers"
        )

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        tenant = hostname.removesuffix(".bamboohr.com")
        board_url = f"{parsed.scheme or 'https'}://{parsed.netloc}/careers"
        return JobBoard(url=board_url, provider=self.name, identifier=tenant)

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        api_url = self.api_url(board.url)
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

        records = data.get("result") if isinstance(data, dict) else None
        if not isinstance(records, list):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="INVALID_STRUCTURED_DATA",
                trace={"adapter": self.name, "api_urls": [api_url]},
            )

        candidates = [
            candidate
            for record in records
            if isinstance(record, dict)
            for candidate in [self._candidate(record, board)]
            if candidate is not None
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
    def api_url(board_url: str) -> str:
        parsed = urlparse(board_url)
        return f"{parsed.scheme or 'https'}://{parsed.netloc}/careers/list"

    def _candidate(self, record: dict[str, Any], board: JobBoard) -> JobCandidate | None:
        title = str(record.get("jobOpeningName") or "").strip()
        job_id = str(record.get("id") or "").strip()
        if not title or not job_id:
            return None
        parsed = urlparse(board.url)
        detail_url = f"{parsed.scheme or 'https'}://{parsed.netloc}/careers/{job_id}"
        return JobCandidate(
            title=title,
            url=detail_url,
            provider=self.name,
            location=_location_name(record.get("location")),
            raw=dict(record),
        )


def _location_name(location: Any) -> str | None:
    if isinstance(location, str):
        return location.strip() or None
    if not isinstance(location, dict):
        return None
    for key in ("locationName", "name"):
        value = str(location.get(key) or "").strip()
        if value:
            return value
    parts = [
        str(location.get(key) or "").strip()
        for key in ("city", "state", "country")
    ]
    normalized = ", ".join(part for part in parts if part)
    return normalized or None


ADAPTER = BambooHRAdapter()
