from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST_SUFFIX = ".bamboohr.com"
_TENANT_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_JOB_ID_PATTERN = re.compile(r"^[1-9][0-9]*$")
_URL_FIELDS = ("jobOpeningUrl", "jobUrl", "url")


class BambooHRAdapter:
    name = "bamboohr"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        parsed_tenant = _parsed_tenant_url(url)
        if parsed_tenant is None:
            return False
        parsed, _tenant = parsed_tenant
        path_parts = [part.lower() for part in parsed.path.split("/") if part]
        return bool(path_parts) and path_parts[0] == "careers"

    def identify_board(self, url: str) -> JobBoard | None:
        parsed_tenant = _parsed_tenant_url(url)
        if parsed_tenant is None or not self.recognizes(url):
            return None
        _parsed, tenant = parsed_tenant
        return JobBoard(
            url=f"https://{tenant}{_HOST_SUFFIX}/careers",
            provider=self.name,
            identifier=tenant,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        tenant = _board_tenant(board)
        if tenant is None:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "invalid BambooHR board tenant"},
            )

        api_url = f"https://{tenant}{_HOST_SUFFIX}/careers/list"
        try:
            page = fetcher.fetch(api_url)
        except (FetchError, OSError, TimeoutError) as error:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                trace={"adapter": self.name, "api_urls": [api_url], "error": str(error)},
            )

        final_url = page.final_url or page.url
        if not _is_tenant_api_url(final_url, tenant):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={
                    "adapter": self.name,
                    "api_urls": [api_url],
                    "rejected_final_url": final_url,
                },
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

        records = data.get("result") if isinstance(data, dict) else None
        if not isinstance(records, list):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="INVALID_STRUCTURED_DATA",
                trace={"adapter": self.name, "api_urls": [api_url]},
            )

        candidates: list[JobCandidate] = []
        seen_urls: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                continue
            candidate = self._candidate(record, tenant)
            if candidate is None or candidate.url in seen_urls:
                continue
            seen_urls.add(candidate.url)
            candidates.append(candidate)
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

    def _candidate(self, record: dict[str, Any], tenant: str) -> JobCandidate | None:
        title = str(record.get("jobOpeningName") or "").strip()
        job_id = _normalized_job_id(record.get("id"))
        if not title or job_id is None:
            return None
        detail_url = _candidate_url(record, tenant, job_id)
        if detail_url is None:
            return None
        return JobCandidate(
            title=title,
            url=detail_url,
            provider=self.name,
            location=(
                _location_name(record.get("location"))
                or _location_name(record.get("atsLocation"))
            ),
            raw=dict(record),
        )


def _parsed_tenant_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    scheme = parsed.scheme.casefold()
    standard_port = port is None or (scheme == "https" and port == 443) or (
        scheme == "http" and port == 80
    )
    hostname = (parsed.hostname or "").casefold()
    if (
        scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or not standard_port
        or not hostname.endswith(_HOST_SUFFIX)
    ):
        return None
    tenant = hostname[: -len(_HOST_SUFFIX)]
    if not _TENANT_PATTERN.fullmatch(tenant):
        return None
    return parsed, tenant


def _board_tenant(board: JobBoard) -> str | None:
    parsed_tenant = _parsed_tenant_url(board.url)
    if parsed_tenant is None:
        return None
    parsed, tenant = parsed_tenant
    path_parts = [part.casefold() for part in parsed.path.split("/") if part]
    identifier = (board.identifier or "").casefold()
    if path_parts != ["careers"] or identifier != tenant:
        return None
    return tenant


def _is_tenant_api_url(url: str, tenant: str) -> bool:
    parsed_tenant = _parsed_tenant_url(url)
    if parsed_tenant is None:
        return False
    parsed, actual_tenant = parsed_tenant
    path_parts = [part.casefold() for part in parsed.path.split("/") if part]
    return actual_tenant == tenant and path_parts == ["careers", "list"]


def _normalized_job_id(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    job_id = str(value or "").strip()
    if not _JOB_ID_PATTERN.fullmatch(job_id):
        return None
    return str(int(job_id))


def _candidate_url(record: dict[str, Any], tenant: str, job_id: str) -> str | None:
    canonical = f"https://{tenant}{_HOST_SUFFIX}/careers/{job_id}"
    explicit = next(
        (record.get(field) for field in _URL_FIELDS if record.get(field) is not None),
        None,
    )
    if explicit is None:
        return canonical
    raw_url = str(explicit).strip()
    if not raw_url:
        return None
    try:
        resolved = urljoin(canonical, raw_url)
    except (TypeError, ValueError):
        return None
    parsed_tenant = _parsed_tenant_url(resolved)
    if parsed_tenant is None:
        return None
    parsed, actual_tenant = parsed_tenant
    path_parts = [part for part in parsed.path.split("/") if part]
    if (
        actual_tenant != tenant
        or len(path_parts) != 2
        or path_parts[0].casefold() != "careers"
        or path_parts[1] != job_id
    ):
        return None
    return canonical


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
