from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_APP_HOST = "app.whitecarrot.io"
_CUSTOM_SUFFIX = ".whitecarrot.ai"
_APP_VERSION = "2.0.33"
_TENANT_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_TITLE_TEST_ID = re.compile(r"^career-job-item-name-[0-9]+$")
_CUSTOM_IDENTIFIER_PREFIX = "host:"


class WhiteCarrotAdapter:
    name = "whitecarrot"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _app_identity(url) is not None or _custom_identity(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        app_identity = _app_identity(url)
        if app_identity is not None:
            tenant, _job_id = app_identity
            return JobBoard(
                url=_app_board_url(tenant),
                provider=self.name,
                identifier=tenant,
            )

        custom_identity = _custom_identity(url)
        if custom_identity is None:
            return None
        hostname, _job_id = custom_identity
        return JobBoard(
            url=f"https://{hostname}/jobs",
            provider=self.name,
            identifier=f"{_CUSTOM_IDENTIFIER_PREFIX}{hostname}",
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        inventory_scope = "title_filtered" if query.title else "full"
        custom_host = _custom_board_host(board)
        if custom_host is not None:
            return self._list_custom_jobs(fetcher, board, custom_host, inventory_scope)

        tenant = _app_board_tenant(board)
        if tenant is None:
            return _result(
                board,
                inventory_scope,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                trace={"error": "invalid WhiteCarrot board locator"},
            )
        return self._list_api_jobs(fetcher, board, tenant, inventory_scope)

    def _list_api_jobs(
        self,
        fetcher,
        board: JobBoard,
        tenant: str,
        inventory_scope: str,
    ) -> AdapterResult:
        api_url = f"https://{_APP_HOST}/api/careers/{tenant}"
        try:
            page = fetcher.fetch(
                api_url,
                headers={
                    "Accept": "application/json",
                    "x-app-version": _APP_VERSION,
                },
            )
        except (FetchError, OSError, TimeoutError) as error:
            return _result(
                board,
                inventory_scope,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                inventory_complete=False,
                trace={"api_urls": [api_url], "error": str(error)},
            )

        final_url = page.final_url or page.url
        if not _is_api_inventory_url(final_url, tenant):
            return _result(
                board,
                inventory_scope,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                trace={
                    "api_urls": [api_url],
                    "error": "WhiteCarrot API redirected outside the tenant inventory",
                    "rejected_final_url": final_url,
                },
            )

        try:
            payload = json.loads(page.html)
        except (json.JSONDecodeError, TypeError, ValueError):
            return _invalid_api_result(board, inventory_scope, api_url, page.source)
        if not isinstance(payload, dict) or not isinstance(payload.get("roles"), list):
            return _invalid_api_result(board, inventory_scope, api_url, page.source)

        listing_host = _verified_public_listing_host(payload.get("publicCareerPageUrl"))
        candidates: list[JobCandidate] = []
        seen_ids: set[str] = set()
        rejected_talent_pools = 0
        for record in payload["roles"]:
            if not isinstance(record, dict):
                return _invalid_api_result(board, inventory_scope, api_url, page.source)
            job_id = _strict_uuid(record.get("id"))
            status = record.get("status")
            if job_id is None or not isinstance(status, str) or job_id in seen_ids:
                return _invalid_api_result(board, inventory_scope, api_url, page.source)
            seen_ids.add(job_id)
            if status != "PUBLISHED":
                continue

            link = record.get("link")
            if _is_profile_builder_url(link):
                rejected_talent_pools += 1
                continue
            title = record.get("roleName")
            if not isinstance(title, str) or not title.strip():
                return _invalid_api_result(board, inventory_scope, api_url, page.source)
            detail_url = _api_candidate_url(link, tenant, job_id, listing_host)
            if detail_url is None:
                return _invalid_api_result(board, inventory_scope, api_url, page.source)
            candidates.append(
                JobCandidate(
                    title=title.strip(),
                    url=detail_url,
                    provider=self.name,
                    location=_location_name(record.get("location")),
                    raw={"id": record["id"], "status": status},
                )
            )

        reason_code = None if candidates else "EMPTY_PROVIDER_RESPONSE"
        return _result(
            board,
            inventory_scope,
            candidates=candidates,
            reason_code=reason_code,
            inventory_complete=True,
            trace={
                "api_urls": [api_url],
                "response_source": page.source,
                "candidate_count": len(candidates),
                "rejected_talent_pool_count": rejected_talent_pools,
            },
        )

    def _list_custom_jobs(
        self,
        fetcher,
        board: JobBoard,
        hostname: str,
        inventory_scope: str,
    ) -> AdapterResult:
        board_url = f"https://{hostname}/jobs"
        try:
            page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _result(
                board,
                inventory_scope,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                inventory_complete=False,
                trace={"board_urls": [board_url], "error": str(error)},
            )

        final_url = page.final_url or page.url
        if not _is_custom_board_url(final_url, hostname):
            return _result(
                board,
                inventory_scope,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                trace={
                    "board_urls": [board_url],
                    "error": "WhiteCarrot board redirected outside its jobs route",
                    "rejected_final_url": final_url,
                },
            )

        parser = _CustomBoardParser()
        try:
            parser.feed(page.html or "")
        except (TypeError, ValueError):
            return _invalid_custom_result(board, inventory_scope, board_url, page.source)

        candidates: list[JobCandidate] = []
        seen_urls: set[str] = set()
        malformed_items = 0
        rejected_talent_pools = 0
        for href, title, has_title_marker in parser.items:
            if not has_title_marker:
                continue
            if _is_profile_builder_url(href):
                rejected_talent_pools += 1
                continue
            detail_url = _custom_candidate_url(href, hostname)
            if detail_url is None or not title:
                malformed_items += 1
                continue
            if detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)
            candidates.append(
                JobCandidate(
                    title=title,
                    url=detail_url,
                    provider=self.name,
                    raw={"id": detail_url.rsplit("/", 1)[-1], "status": "PUBLISHED"},
                )
            )

        strong_ssr = bool(
            parser.title_marker_count
            and re.search(r"(?:__NEXT_DATA__|self\.__next_f|/_next/)", page.html or "")
        )
        inventory_complete = bool(candidates and strong_ssr and not malformed_items)
        reason_code = None if candidates and not malformed_items else "INVALID_STRUCTURED_DATA"
        return _result(
            board,
            inventory_scope,
            candidates=candidates,
            reason_code=reason_code,
            inventory_complete=inventory_complete,
            trace={
                "board_urls": [board_url],
                "response_source": page.source,
                "candidate_count": len(candidates),
                "title_marker_count": parser.title_marker_count,
                "malformed_item_count": malformed_items,
                "rejected_talent_pool_count": rejected_talent_pools,
                "strong_ssr_evidence": strong_ssr,
            },
        )


class _CustomBoardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str, bool]] = []
        self.title_marker_count = 0
        self._href: str | None = None
        self._title_tag: str | None = None
        self._title_parts: list[str] = []
        self._has_title_marker = False

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {key.casefold(): value for key, value in attrs}
        if self._href is None and tag.casefold() == "a" and attributes.get("href"):
            self._href = attributes["href"]
            self._title_tag = None
            self._title_parts = []
            self._has_title_marker = False
            return
        if self._href is None:
            return
        test_id = attributes.get("data-testid")
        if isinstance(test_id, str) and _TITLE_TEST_ID.fullmatch(test_id):
            self.title_marker_count += 1
            self._has_title_marker = True
            self._title_tag = tag.casefold()

    def handle_data(self, data: str) -> None:
        if self._href is not None and self._title_tag is not None:
            self._title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._href is None:
            return
        tag_name = tag.casefold()
        if tag_name == self._title_tag:
            self._title_tag = None
        if tag_name != "a":
            return
        title = " ".join(" ".join(self._title_parts).split())
        self.items.append((self._href, title, self._has_title_marker))
        self._href = None


def _app_identity(url: str) -> tuple[str, str | None] | None:
    parsed = _safe_https_url(url, _APP_HOST, allow_query_fragment=True)
    if parsed is None:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "careers":
        offset = 1
    elif len(parts) >= 3 and parts[:2] == ["share", "careers"]:
        offset = 2
    else:
        return None
    tenant = parts[offset]
    if not _TENANT_PATTERN.fullmatch(tenant):
        return None
    remainder = parts[offset + 1 :]
    if not remainder:
        return tenant, None
    if len(remainder) == 2 and remainder[0] == "job":
        job_id = _strict_uuid(remainder[1])
        return (tenant, job_id) if job_id else None
    return None


def _custom_identity(url: str) -> tuple[str, str | None] | None:
    parsed = _safe_https_url(url, allow_query_fragment=True)
    if parsed is None:
        return None
    hostname = (parsed.hostname or "").casefold()
    label = hostname[: -len(_CUSTOM_SUFFIX)] if hostname.endswith(_CUSTOM_SUFFIX) else ""
    if not label or "." in label or not _TENANT_PATTERN.fullmatch(label):
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if parts == ["jobs"]:
        return hostname, None
    if len(parts) == 2 and parts[0] == "jobs":
        job_id = _strict_uuid(parts[1])
        return (hostname, job_id) if job_id else None
    return None


def _safe_https_url(
    url: Any,
    expected_host: str | None = None,
    *,
    allow_query_fragment: bool = False,
):
    if not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or port not in {None, 443}
        or parsed.params
        or (not allow_query_fragment and (parsed.query or parsed.fragment))
        or (expected_host is not None and hostname != expected_host)
    ):
        return None
    return parsed


def _app_board_url(tenant: str) -> str:
    return f"https://{_APP_HOST}/careers/{tenant}"


def _app_board_tenant(board: JobBoard) -> str | None:
    if not isinstance(board.identifier, str) or not _TENANT_PATTERN.fullmatch(board.identifier):
        return None
    identity = _app_identity(board.url)
    if identity != (board.identifier, None):
        return None
    return board.identifier


def _custom_board_host(board: JobBoard) -> str | None:
    identifier = board.identifier
    if not isinstance(identifier, str) or not identifier.startswith(_CUSTOM_IDENTIFIER_PREFIX):
        return None
    hostname = identifier[len(_CUSTOM_IDENTIFIER_PREFIX) :]
    identity = _custom_identity(board.url)
    if identity != (hostname, None):
        return None
    return hostname


def _is_api_inventory_url(url: str, tenant: str) -> bool:
    parsed = _safe_https_url(url, _APP_HOST)
    return bool(parsed and parsed.path == f"/api/careers/{tenant}")


def _is_custom_board_url(url: str, hostname: str) -> bool:
    parsed = _safe_https_url(url, hostname)
    return bool(parsed and [part for part in parsed.path.split("/") if part] == ["jobs"])


def _strict_uuid(value: Any) -> str | None:
    if not isinstance(value, str) or not _UUID_PATTERN.fullmatch(value):
        return None
    return value.casefold()


def _verified_public_listing_host(value: Any) -> str | None:
    parsed = _safe_https_url(value)
    if parsed is None or parsed.path.rstrip("/") not in {"", "/jobs"}:
        return None
    hostname = (parsed.hostname or "").casefold()
    label = hostname[: -len(_CUSTOM_SUFFIX)] if hostname.endswith(_CUSTOM_SUFFIX) else ""
    if label and "." not in label and _TENANT_PATTERN.fullmatch(label):
        return hostname
    return None


def _api_candidate_url(
    value: Any,
    tenant: str,
    job_id: str,
    listing_host: str | None,
) -> str | None:
    if isinstance(value, str):
        app_identity = _strict_app_identity(value)
        if app_identity == (tenant, job_id):
            return f"{_app_board_url(tenant)}/job/{job_id}"
        custom_identity = _strict_custom_identity(value)
        if custom_identity is not None:
            hostname, linked_job_id = custom_identity
            allowed_hosts = {f"{tenant}{_CUSTOM_SUFFIX}"}
            if listing_host:
                allowed_hosts.add(listing_host)
            if hostname in allowed_hosts and linked_job_id == job_id:
                return f"https://{hostname}/jobs/{job_id}"
    if listing_host:
        return f"https://{listing_host}/jobs/{job_id}"
    return None


def _custom_candidate_url(value: Any, hostname: str) -> str | None:
    absolute = urljoin(f"https://{hostname}/jobs", value) if isinstance(value, str) else None
    identity = _strict_custom_identity(absolute) if absolute else None
    if identity is None or identity[0] != hostname or identity[1] is None:
        return None
    return f"https://{hostname}/jobs/{identity[1]}"


def _is_profile_builder_url(value: Any) -> bool:
    parsed = _safe_https_url(value, _APP_HOST, allow_query_fragment=True)
    if parsed is None:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return bool(parts and parts[0] == "profile-builder")


def _strict_app_identity(value: str) -> tuple[str, str | None] | None:
    parsed = _safe_https_url(value, _APP_HOST)
    return _app_identity(value) if parsed is not None else None


def _strict_custom_identity(value: str) -> tuple[str, str | None] | None:
    parsed = _safe_https_url(value)
    return _custom_identity(value) if parsed is not None else None


def _location_name(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None
    for key in ("locationName", "name", "displayName"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    parts = []
    for key in ("city", "state", "region", "country"):
        item = value.get(key)
        if isinstance(item, str) and item.strip() and item.strip() not in parts:
            parts.append(item.strip())
    return ", ".join(parts) or None


def _invalid_api_result(
    board: JobBoard,
    inventory_scope: str,
    api_url: str,
    response_source: str,
) -> AdapterResult:
    return _result(
        board,
        inventory_scope,
        reason_code="INVALID_STRUCTURED_DATA",
        inventory_complete=False,
        trace={"api_urls": [api_url], "response_source": response_source},
    )


def _invalid_custom_result(
    board: JobBoard,
    inventory_scope: str,
    board_url: str,
    response_source: str,
) -> AdapterResult:
    return _result(
        board,
        inventory_scope,
        reason_code="INVALID_STRUCTURED_DATA",
        inventory_complete=False,
        trace={"board_urls": [board_url], "response_source": response_source},
    )


def _result(
    board: JobBoard,
    inventory_scope: str,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None = None,
    retryable: bool = False,
    inventory_complete: bool,
    trace: dict[str, Any],
) -> AdapterResult:
    trace.update(
        {
            "adapter": "whitecarrot",
            "inventory_scope": inventory_scope,
            "inventory_complete": inventory_complete,
        }
    )
    return AdapterResult(
        provider="whitecarrot",
        board=board,
        candidates=candidates or [],
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope=inventory_scope,
        inventory_complete=inventory_complete,
        trace=trace,
    )


ADAPTER = WhiteCarrotAdapter()
