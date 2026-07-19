from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, quote, unquote, urlparse, urlunparse

from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


class LeverAdapter:
    name = "lever"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            port = parsed.port
        except (TypeError, ValueError):
            return False
        if (
            parsed.scheme.casefold() != "https"
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.fragment
        ):
            return False
        host = (parsed.hostname or "").casefold()
        if host == "jobs.lever.co":
            return _tenant_from_jobs_path(parsed.path) is not None
        if host == "api.lever.co":
            return (
                _tenant_from_api_path(parsed.path) is not None
                and _safe_api_query(parsed.query)
            )
        return False

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        parsed = urlparse(url)
        tenant = (
            _tenant_from_api_path(parsed.path)
            if (parsed.hostname or "").casefold() == "api.lever.co"
            else _tenant_from_jobs_path(parsed.path)
        )
        if tenant is None:
            return None
        return JobBoard(
            url=f"https://jobs.lever.co/{tenant}",
            provider=self.name,
            identifier=tenant,
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


_TENANT = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?$")


def _tenant_from_jobs_path(path: str) -> str | None:
    parts = [unquote(part) for part in path.split("/") if part]
    return parts[0] if parts and _TENANT.fullmatch(parts[0]) else None


def _tenant_from_api_path(path: str) -> str | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if len(parts) != 3 or [part.casefold() for part in parts[:2]] != ["v0", "postings"]:
        return None
    return parts[2] if _TENANT.fullmatch(parts[2]) else None


def _safe_api_query(query: str) -> bool:
    try:
        values = parse_qsl(query, keep_blank_values=True)
    except ValueError:
        return False
    return not values or values == [("mode", "json")]
