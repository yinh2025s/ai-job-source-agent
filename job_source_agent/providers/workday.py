from __future__ import annotations

import json
import re
from urllib.parse import urlparse, urlunparse

from ..web import FetchError, safe_normalize_url
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_WORKDAY_HOST_SUFFIXES = (".myworkdayjobs.com", ".workdayjobs.com")
_LOCALE_PATTERN = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
_AUXILIARY_ROUTE_PARTS = frozenset(
    {"introduceyourself", "login", "sign-in", "my-profile", "talent-community"}
)
_PAGE_SIZE = 20
_MAX_PAGES = 5


class WorkdayAdapter:
    name = "workday"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
        except (TypeError, ValueError):
            return False
        return _is_safe_workday_origin(parsed) and any(
            host.endswith(suffix) for suffix in _WORKDAY_HOST_SUFFIXES
        )

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None

        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        tenant = (parsed.hostname or "").split(".", 1)[0]
        site, board_parts = _site_and_board_parts(parts, tenant)
        if (
            not tenant
            or not site
            or not board_parts
            or site.casefold() in _AUXILIARY_ROUTE_PARTS
            or not _is_board_route(parts[len(board_parts) :])
        ):
            return None

        board_path = "/" + "/".join(board_parts)
        board_url = urlunparse((parsed.scheme or "https", parsed.netloc, board_path, "", "", ""))
        return JobBoard(
            url=board_url,
            provider=self.name,
            identifier=f"{tenant}/{site}",
            replay_safe=True,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        inventory_scope = "title_filtered" if query.title else "full"
        identifiers = _split_identifier(board.identifier)
        if identifiers is None:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_scope=inventory_scope,
                inventory_complete=False,
                trace={
                    "adapter": self.name,
                    "error": "missing Workday tenant/site identifier",
                    "inventory_scope": inventory_scope,
                    "inventory_complete": False,
                },
            )

        tenant, site = identifiers
        board_host = (urlparse(board.url).hostname or "").casefold()
        if not board_host.startswith(f"{tenant.casefold()}."):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_scope=inventory_scope,
                inventory_complete=False,
                trace={
                    "adapter": self.name,
                    "error": "Workday board tenant did not match host",
                    "inventory_scope": inventory_scope,
                    "inventory_complete": False,
                },
            )
        api_url = self.api_url(board.url, tenant, site)
        candidates = []
        api_urls = []
        response_source = None
        total = None
        errors: list[dict[str, str]] = []
        failure_reason_code: str | None = None
        inventory_complete = False
        records_seen = 0
        for page_number in range(_MAX_PAGES):
            offset = page_number * _PAGE_SIZE
            payload = {
                "appliedFacets": {},
                "limit": _PAGE_SIZE,
                "offset": offset,
                "searchText": query.title or "",
            }
            api_urls.append(api_url)
            try:
                page = fetcher.fetch(
                    api_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Origin": _origin(board.url),
                        "Referer": board.url,
                    },
                )
            except (FetchError, OSError, TimeoutError) as error:
                errors.append({"url": api_url, "error": str(error)})
                failure_reason_code = "PROVIDER_FETCH_FAILED"
                break
            response_source = response_source or page.source
            response_url = page.final_url or page.url
            if not _is_same_workday_host(response_url, board_host):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                    inventory_scope=inventory_scope,
                    inventory_complete=False,
                    trace={
                        "adapter": self.name,
                        "error": "Workday API redirected outside board host",
                        "inventory_scope": inventory_scope,
                        "inventory_complete": False,
                    },
                )
            try:
                data = json.loads(page.html)
            except (json.JSONDecodeError, TypeError):
                errors.append({"url": api_url, "error": "invalid Workday JSON response"})
                failure_reason_code = "INVALID_STRUCTURED_DATA"
                break

            postings = data.get("jobPostings") if isinstance(data, dict) else None
            if not isinstance(postings, list):
                errors.append({"url": api_url, "error": "missing Workday jobPostings list"})
                failure_reason_code = "INVALID_STRUCTURED_DATA"
                break
            records_seen += len(postings)
            page_total = _nonnegative_int(data.get("total"))
            if page_total is not None:
                total = max(total or 0, page_total)
            for job in postings:
                if not isinstance(job, dict):
                    continue
                title = str(job.get("title") or "")
                detail_url = _detail_url(job, board.url, board_host)
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
            if not postings or (total is None and len(postings) < _PAGE_SIZE):
                inventory_complete = True
                break
            if total is not None and records_seen >= total:
                inventory_complete = True
                break

        reason_code = None if candidates else failure_reason_code or "EMPTY_PROVIDER_RESPONSE"
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=reason_code,
            retryable=reason_code == "PROVIDER_FETCH_FAILED",
            inventory_scope=inventory_scope,
            inventory_complete=inventory_complete,
            trace={
                "adapter": self.name,
                "api_urls": api_urls,
                "response_source": response_source,
                "candidate_count": len(candidates),
                "page_count": len(api_urls),
                "total": total,
                "tenant": tenant,
                "site": site,
                "errors": errors,
                "inventory_scope": inventory_scope,
                "inventory_complete": inventory_complete,
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


def _is_board_route(route_parts: list[str]) -> bool:
    folded = [part.casefold() for part in route_parts]
    if any(part in _AUXILIARY_ROUTE_PARTS for part in folded):
        return False
    if not folded or folded == ["jobs"]:
        return True
    return len(folded) >= 2 and folded[0] == "job"


def _split_identifier(identifier: str | None) -> tuple[str, str] | None:
    if not identifier or "/" not in identifier:
        return None
    tenant, site = identifier.split("/", 1)
    if not tenant or not site:
        return None
    return tenant, site


def _detail_url(job: dict, board_url: str, expected_host: str) -> str | None:
    external_path = str(job.get("externalPath") or "").strip()
    if not external_path:
        return None
    try:
        if external_path.startswith(("http://", "https://")):
            candidate = safe_normalize_url(external_path)
        else:
            parsed = urlparse(board_url)
            if external_path.startswith("/job/") or external_path == "/job":
                candidate = f"{board_url.rstrip('/')}{external_path}"
            elif external_path.startswith("/"):
                candidate = urlunparse((parsed.scheme, parsed.netloc, external_path, "", "", ""))
            else:
                candidate = f"{board_url.rstrip('/')}/{external_path}"
            candidate = safe_normalize_url(candidate)
    except (TypeError, ValueError):
        return None
    return candidate if candidate and _is_same_workday_host(candidate, expected_host) else None


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def _is_safe_workday_origin(parsed) -> bool:
    try:
        port = parsed.port
    except ValueError:
        return False
    standard_port = port is None or (parsed.scheme == "https" and port == 443) or (
        parsed.scheme == "http" and port == 80
    )
    return (
        parsed.scheme in {"http", "https"}
        and parsed.username is None
        and parsed.password is None
        and standard_port
        and bool(parsed.hostname)
    )


def _is_same_workday_host(url: str, expected_host: str) -> bool:
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return False
    return _is_safe_workday_origin(parsed) and (parsed.hostname or "").casefold() == expected_host


def _location(job: dict) -> str | None:
    location = job.get("locationsText") or job.get("location")
    return str(location) if location else None


def _nonnegative_int(value) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


ADAPTER = WorkdayAdapter()
