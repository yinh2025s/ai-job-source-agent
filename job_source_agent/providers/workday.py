from __future__ import annotations

import json
import re
from urllib.parse import urlparse, urlunparse

from ..web import safe_normalize_url
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_WORKDAY_HOST_SUFFIXES = (".myworkdayjobs.com", ".workdayjobs.com")
_LOCALE_PATTERN = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


class WorkdayAdapter:
    name = "workday"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            return False
        return any(host.endswith(suffix) for suffix in _WORKDAY_HOST_SUFFIXES)

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None

        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        tenant = (parsed.hostname or "").split(".", 1)[0]
        site, board_parts = _site_and_board_parts(parts, tenant)
        if not tenant or not site or not board_parts:
            return None

        board_path = "/" + "/".join(board_parts)
        board_url = urlunparse((parsed.scheme or "https", parsed.netloc, board_path, "", "", ""))
        return JobBoard(
            url=board_url,
            provider=self.name,
            identifier=f"{tenant}/{site}",
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identifiers = _split_identifier(board.identifier)
        if identifiers is None:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "missing Workday tenant/site identifier"},
            )

        tenant, site = identifiers
        api_url = self.api_url(board.url, tenant, site)
        payload = {
            "appliedFacets": {},
            "limit": 50,
            "offset": 0,
            "searchText": query.title or "",
        }
        page = fetcher.fetch(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            data = json.loads(page.html)
        except (json.JSONDecodeError, TypeError):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="INVALID_STRUCTURED_DATA",
                trace={"adapter": self.name, "api_urls": [api_url]},
            )

        postings = data.get("jobPostings") if isinstance(data, dict) else None
        if not isinstance(postings, list):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="INVALID_STRUCTURED_DATA",
                trace={"adapter": self.name, "api_urls": [api_url]},
            )
        candidates = []
        for job in postings:
            if not isinstance(job, dict):
                continue
            title = str(job.get("title") or "")
            detail_url = _detail_url(job, board.url)
            if not title or not detail_url:
                continue
            candidates.append(
                JobCandidate(
                    title=title,
                    url=detail_url,
                    provider=self.name,
                    location=_location(job),
                    raw={
                        key: job.get(key)
                        for key in ("bulletFields", "externalPath", "postedOn")
                        if key in job
                    },
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
                "tenant": tenant,
                "site": site,
            },
        )

    @staticmethod
    def api_url(board_url: str, tenant: str, site: str) -> str:
        parsed = urlparse(board_url)
        return urlunparse(
            (
                parsed.scheme or "https",
                parsed.netloc,
                f"/wday/cxs/{tenant}/{site}/jobs",
                "",
                "",
                "",
            )
        )


def _site_and_board_parts(parts: list[str], tenant: str) -> tuple[str | None, list[str]]:
    if len(parts) >= 5 and parts[:2] == ["wday", "cxs"]:
        return parts[3], parts[:4]

    if len(parts) >= 3 and parts[0] == "recruiting" and parts[1].lower() == tenant.lower():
        return parts[2], parts[:3]

    if not parts:
        return None, []
    site_index = 1 if len(parts) >= 2 and _LOCALE_PATTERN.fullmatch(parts[0]) else 0
    site = parts[site_index]
    return site, parts[: site_index + 1]


def _split_identifier(identifier: str | None) -> tuple[str, str] | None:
    if not identifier or "/" not in identifier:
        return None
    tenant, site = identifier.split("/", 1)
    if not tenant or not site:
        return None
    return tenant, site


def _detail_url(job: dict, board_url: str) -> str | None:
    external_path = str(job.get("externalPath") or "").strip()
    if not external_path:
        return None
    if external_path.startswith(("http://", "https://")):
        return safe_normalize_url(external_path)

    parsed = urlparse(board_url)
    if external_path.startswith("/job/") or external_path == "/job":
        candidate = f"{board_url.rstrip('/')}{external_path}"
    elif external_path.startswith("/"):
        candidate = urlunparse((parsed.scheme, parsed.netloc, external_path, "", "", ""))
    else:
        candidate = f"{board_url.rstrip('/')}/{external_path}"
    return safe_normalize_url(candidate)


def _location(job: dict) -> str | None:
    location = job.get("locationsText") or job.get("location")
    return str(location) if location else None


ADAPTER = WorkdayAdapter()
