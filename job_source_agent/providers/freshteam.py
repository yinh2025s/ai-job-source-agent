from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

from ..fetch_failure import project_fetch_error
from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_PROVIDER_SUFFIX = ".freshteam.com"
_ASSET_HOST = "s3.amazonaws.com"
_ASSET_PATH = re.compile(
    r"^/files\.freshteam\.com/production/(?P<account>[1-9][0-9]{0,19})/"
    r"attachments/(?P<attachment>[1-9][0-9]{0,19})/original/"
    r"(?P<file>[A-Za-z0-9_-]{1,100}_widget\.js)$"
)
_ASSET_QUERY = re.compile(r"^[1-9][0-9]{0,19}$")
_TENANT = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_RESERVED_TENANTS = frozenset({"api", "app", "assets", "support", "www"})
_UNIQUE_ID = re.compile(r"^[A-Za-z0-9_-]{6,128}$")
_POSITIVE_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_DETAIL_SLUG = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,198}[A-Za-z0-9])?$")
_WIDGET_CONFIG = re.compile(
    r"new\s+freshTeam\.JobWidget\s*\(\s*"
    r"(?:elem|['\"]freshteam-widget['\"])\s*,\s*"
    r"(['\"])(https://[a-z0-9-]+\.freshteam\.com)\1\s*\)"
)
_MAX_PAGE_CHARS = 2_000_000
_MAX_ASSET_CHARS = 256_000
_MAX_INVENTORY_CHARS = 5_000_000
_MAX_JOBS = 2_000
_MAX_FIELD_CHARS = 20_000


class FreshteamAdapter:
    name = "freshteam"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _provider_url_identity(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        identity = _provider_url_identity(url)
        if identity is None:
            return None
        tenant, _unique_id = identity
        return _job_board(tenant)

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        # A declaration is only a fetch candidate. The asset and inventory must
        # both be validated by probe_board before this provider is selected.
        return None

    def probe_board(self, fetcher, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        parsed_page = _safe_https_url(page_url)
        if parsed_page is None:
            return None
        referer = urlunparse(parsed_page._replace(query="", fragment=""))
        asset_url = _declared_widget_asset(page.html)
        if asset_url is None:
            return None
        try:
            asset = fetcher.fetch(asset_url, headers={"Referer": referer})
        except (FetchError, OSError, TimeoutError):
            return None
        if not _same_asset_url(asset.final_url or asset.url, asset_url):
            return None
        tenant = _tenant_from_widget_asset(asset.html)
        if tenant is None:
            return None

        inventory_url = _inventory_url(tenant)
        try:
            inventory = fetcher.fetch(
                inventory_url,
                headers={"Accept": "application/json", "Referer": referer},
            )
        except (FetchError, OSError, TimeoutError):
            return None
        if not _same_inventory_url(inventory.final_url or inventory.url, tenant):
            return None
        parsed = _inventory_candidates(inventory.html, tenant)
        if not isinstance(parsed, list) or not parsed:
            return None
        return _job_board(tenant)

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        tenant = _board_tenant(board)
        if tenant is None:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="invalid Freshteam board locator",
            )

        inventory_url = _inventory_url(tenant)
        try:
            page = fetcher.fetch(
                inventory_url,
                headers={"Accept": "application/json", "Referer": board.url},
            )
        except (FetchError, OSError, TimeoutError) as error:
            reason_code, retryable = _fetch_classification(error)
            return _result(
                board,
                reason_code=reason_code,
                retryable=retryable,
                inventory_complete=False,
                api_urls=[inventory_url],
                error="Freshteam public inventory fetch failed",
            )

        final_url = page.final_url or page.url
        if not _same_inventory_url(final_url, tenant):
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                api_urls=[inventory_url],
                error="Freshteam inventory redirected outside the declared tenant",
                rejected_final_url=final_url,
            )

        parsed = _inventory_candidates(page.html, tenant)
        if parsed == "response_cap_exceeded" or parsed == "row_cap_exceeded":
            return _result(
                board,
                reason_code="FETCH_BUDGET_EXHAUSTED",
                retryable=True,
                inventory_complete=False,
                api_urls=[inventory_url],
                response_source=page.source,
                stop_reason=parsed,
            )
        if not isinstance(parsed, list):
            return _result(
                board,
                reason_code="INVALID_STRUCTURED_DATA",
                inventory_complete=False,
                api_urls=[inventory_url],
                response_source=page.source,
                error="invalid or cross-tenant Freshteam public inventory",
            )

        target = _normalized_title(query.title)
        return _result(
            board,
            candidates=parsed,
            reason_code=None if parsed else "EMPTY_PROVIDER_RESPONSE",
            inventory_complete=True,
            api_urls=[inventory_url],
            response_source=page.source,
            records_seen=len(parsed),
            candidate_count=len(parsed),
            exact_title_found=bool(
                target
                and any(_normalized_title(candidate.title) == target for candidate in parsed)
            ),
        )


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        values = {name.casefold(): value for name, value in attrs}
        source = values.get("src")
        if isinstance(source, str) and _asset_identity(source) is not None:
            self.sources.append(source)


def _declared_widget_asset(html: str) -> str | None:
    if not isinstance(html, str) or len(html) > _MAX_PAGE_CHARS:
        return None
    parser = _ScriptParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    unique = list(dict.fromkeys(parser.sources))
    return unique[0] if len(unique) == 1 else None


def _asset_identity(url: object) -> tuple[str, str, str] | None:
    parsed = _safe_https_url(url)
    if parsed is None or (parsed.hostname or "").casefold() != _ASSET_HOST or parsed.fragment:
        return None
    match = _ASSET_PATH.fullmatch(parsed.path)
    if match is None or _ASSET_QUERY.fullmatch(parsed.query) is None:
        return None
    return match.group("account"), match.group("attachment"), match.group("file")


def _same_asset_url(actual: str, expected: str) -> bool:
    return _asset_identity(actual) == _asset_identity(expected) and actual == expected


def _tenant_from_widget_asset(source: str) -> str | None:
    if not isinstance(source, str) or len(source) > _MAX_ASSET_CHARS:
        return None
    if (
        "https://assets1.freshteam.com/assets/job_widget.js" not in source
        or "freshteam-widget" not in source
    ):
        return None
    origins = [match.group(2) for match in _WIDGET_CONFIG.finditer(source)]
    unique = list(dict.fromkeys(origins))
    if len(unique) != 1:
        return None
    parsed = _safe_https_url(unique[0])
    if parsed is None or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    return _tenant_from_host((parsed.hostname or "").casefold())


def _safe_https_url(url: object):
    if not isinstance(url, str) or len(url) > 8_192:
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    return parsed


def _tenant_from_host(host: str) -> str | None:
    if not host.endswith(_PROVIDER_SUFFIX):
        return None
    tenant = host[: -len(_PROVIDER_SUFFIX)]
    if tenant in _RESERVED_TENANTS or "." in tenant or not _TENANT.fullmatch(tenant):
        return None
    return tenant


def _provider_url_identity(url: str) -> tuple[str, str | None] | None:
    parsed = _safe_https_url(url)
    if parsed is None or parsed.query or parsed.fragment:
        return None
    tenant = _tenant_from_host((parsed.hostname or "").casefold())
    if tenant is None:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if parts == ["jobs"]:
        return tenant, None
    if len(parts) in {2, 3} and parts[0] == "jobs" and _UNIQUE_ID.fullmatch(parts[1]):
        if len(parts) == 3 and _DETAIL_SLUG.fullmatch(parts[2]) is None:
            return None
        return tenant, parts[1]
    return None


def _job_board(tenant: str) -> JobBoard:
    return JobBoard(
        url=f"https://{tenant}{_PROVIDER_SUFFIX}/jobs",
        provider="freshteam",
        identifier=tenant,
        replay_safe=True,
    )


def _board_tenant(board: JobBoard) -> str | None:
    if (
        board.provider != "freshteam"
        or not isinstance(board.identifier, str)
        or not _TENANT.fullmatch(board.identifier)
    ):
        return None
    tenant = board.identifier.casefold()
    return (
        tenant
        if tenant not in _RESERVED_TENANTS and board == _job_board(tenant)
        else None
    )


def _inventory_url(tenant: str) -> str:
    return f"https://{tenant}{_PROVIDER_SUFFIX}/hire/widgets/jobs.json"


def _same_inventory_url(url: str, tenant: str) -> bool:
    parsed = _safe_https_url(url)
    return bool(
        parsed
        and (parsed.hostname or "").casefold() == f"{tenant}{_PROVIDER_SUFFIX}"
        and parsed.path == "/hire/widgets/jobs.json"
        and not parsed.query
        and not parsed.fragment
    )


def _inventory_candidates(raw: str, tenant: str) -> list[JobCandidate] | str | None:
    if not isinstance(raw, str):
        return None
    if len(raw) > _MAX_INVENTORY_CHARS:
        return "response_cap_exceeded"
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    jobs = payload.get("jobs")
    branches = payload.get("branches")
    roles = payload.get("job_roles")
    if (
        not isinstance(jobs, list)
        or not isinstance(branches, list)
        or not isinstance(roles, list)
    ):
        return None
    if len(jobs) > _MAX_JOBS:
        return "row_cap_exceeded"

    branch_locations = _id_name_map(branches, "location")
    role_names = _id_name_map(roles, "name")
    if branch_locations is None or role_names is None:
        return None

    candidates: list[JobCandidate] = []
    seen_ids: set[str] = set()
    seen_internal_ids: set[str] = set()
    for record in jobs:
        if not isinstance(record, dict):
            return None
        internal_id = _positive_id(record.get("id"))
        unique_id = record.get("unique_id")
        title = _field(record.get("title"), required=True)
        branch_id = _positive_id(record.get("branch_id"))
        role_id = _positive_id(record.get("job_role_id"))
        remote = record.get("remote")
        if (
            internal_id is None
            or not isinstance(unique_id, str)
            or _UNIQUE_ID.fullmatch(unique_id) is None
            or title is None
            or branch_id not in branch_locations
            or role_id not in role_names
            or not isinstance(remote, bool)
            or record.get("status") != 2
            or record.get("deleted") is not False
            or internal_id in seen_internal_ids
            or unique_id in seen_ids
        ):
            return None
        location = _field(record.get("preferred_remote_job_locations"), required=False)
        if location is None:
            return None
        if not location:
            location = "Remote" if remote else branch_locations[branch_id]
        elif not remote:
            return None
        seen_internal_ids.add(internal_id)
        seen_ids.add(unique_id)
        candidates.append(
            JobCandidate(
                title=title,
                url=f"https://{tenant}{_PROVIDER_SUFFIX}/jobs/{unique_id}",
                provider="freshteam",
                location=location,
                raw={
                    "id": internal_id,
                    "unique_id": unique_id,
                    "job_role_id": role_id,
                    "branch_id": branch_id,
                },
            )
        )
    return candidates


def _id_name_map(records: list[Any], field: str) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for record in records:
        if not isinstance(record, dict):
            return None
        record_id = _positive_id(record.get("id"))
        value = _field(record.get(field), required=True)
        if record_id is None or value is None or record_id in values:
            return None
        values[record_id] = value
    return values


def _positive_id(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    return text if _POSITIVE_ID.fullmatch(text) else None


def _field(value: object, *, required: bool) -> str | None:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value) > _MAX_FIELD_CHARS:
        return None
    cleaned = " ".join(value.split())
    if required and not cleaned:
        return None
    if any(ord(character) < 32 for character in cleaned):
        return None
    return cleaned


def _fetch_classification(error: Exception) -> tuple[str, bool]:
    if isinstance(error, FetchError):
        projection = project_fetch_error(error)
        reason_code = projection["reason_code"]
        retryable = projection["retryable"]
    else:
        reason_code = classify_fetch_error(str(error))
        retryable = reason_spec(reason_code).retryable
    if reason_code == "FETCH_FAILED":
        return "PROVIDER_FETCH_FAILED", True
    return reason_code, retryable


def _normalized_title(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if value else ""


def _result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None,
    retryable: bool = False,
    inventory_complete: bool,
    error: str | None = None,
    api_urls: list[str] | None = None,
    response_source: str | None = None,
    rejected_final_url: str | None = None,
    stop_reason: str | None = None,
    records_seen: int | None = None,
    candidate_count: int | None = None,
    exact_title_found: bool | None = None,
) -> AdapterResult:
    trace: dict[str, Any] = {
        "adapter": "freshteam",
        "variant": "public_job_widget_inventory",
        "api_urls": api_urls or [],
        "inventory_scope": "full" if inventory_complete else "unknown",
        "inventory_complete": inventory_complete,
    }
    optional = {
        "error": error,
        "response_source": response_source,
        "rejected_final_url": rejected_final_url,
        "stop_reason": stop_reason,
        "records_seen": records_seen,
        "candidate_count": candidate_count,
        "exact_title_found": exact_title_found,
    }
    trace.update({key: value for key, value in optional.items() if value is not None})
    return AdapterResult(
        provider="freshteam",
        board=board,
        candidates=candidates or [],
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope="full" if inventory_complete else "unknown",
        inventory_complete=inventory_complete,
        trace=trace,
    )


ADAPTER = FreshteamAdapter()
