from __future__ import annotations

import json
from urllib.parse import quote, urljoin, urlparse

from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


class SmartRecruitersAdapter:
    name = "smartrecruiters"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        try:
            return (urlparse(url).hostname or "").lower() == "jobs.smartrecruiters.com"
        except ValueError:
            return False

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
                trace={"adapter": self.name, "error": "missing SmartRecruiters company identifier"},
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

        postings = data.get("content", []) if isinstance(data, dict) else []
        candidates = []
        for posting in postings if isinstance(postings, list) else []:
            if not isinstance(posting, dict):
                continue
            title = str(posting.get("name") or "").strip()
            detail_url = _detail_url(posting, board)
            if not title or not detail_url:
                continue
            candidates.append(
                JobCandidate(
                    title=title,
                    url=detail_url,
                    provider=self.name,
                    location=_location_name(posting),
                    raw={"id": posting.get("id")},
                )
            )

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
    def api_url(company_identifier: str) -> str:
        company = quote(company_identifier, safe="-._~")
        return f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100"


def _detail_url(posting: dict, board: JobBoard) -> str:
    actions = posting.get("actions")
    if isinstance(actions, dict) and actions.get("details"):
        return urljoin(board.url, str(actions["details"]).strip())
    if posting.get("ref"):
        return urljoin(board.url, str(posting["ref"]).strip())
    if posting.get("id") and board.identifier:
        return (
            f"https://jobs.smartrecruiters.com/{quote(board.identifier, safe='-._~')}/"
            f"{quote(str(posting['id']).strip(), safe='-._~')}"
        )
    return ""


def _location_name(posting: dict) -> str | None:
    location = posting.get("location")
    if not isinstance(location, dict):
        return None
    if location.get("fullLocation"):
        return str(location["fullLocation"]).strip() or None

    parts = []
    for key in ("city", "region", "country"):
        value = str(location.get(key) or "").strip()
        if value and value.casefold() not in {part.casefold() for part in parts}:
            parts.append(value)
    return ", ".join(parts) or None


ADAPTER = SmartRecruitersAdapter()
