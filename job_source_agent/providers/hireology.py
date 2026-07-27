from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlparse

from ..provider_candidates import ProviderPublishedEmployerEvidence
from ..reasons import reason_spec
from ..web import FetchError, Page
from .base import (
    AdapterResult,
    JobBoard,
    JobCandidate,
    JobQuery,
    provider_fetch_reason,
)


_PUBLIC_HOST = "careers.hireology.com"
_API_HOST = "api.hireology.com"
_V1_API_PATH_PREFIX = "/v1/careers/"
_API_PATH_PREFIX = "/v2/public/careers/"
_DETAIL_API_PATH_PREFIX = "/v2/public/careers/jobs/"
_TENANT = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")
_OPENING_ID = re.compile(r"^[1-9][0-9]{0,18}$")
_MAX_PAGES = 10
_PAGE_SIZE = 100
_MAX_RESPONSE_CHARS = 8_000_000
_MAX_RECORDS = _MAX_PAGES * _PAGE_SIZE
_MAX_FIELD_CHARS = 20_000
_OFFICIAL_ASSET_HOSTS = frozenset(
    {
        "app.hireology.com",
        "assets.hireology.com",
        "careers.hireology.com",
    }
)


class HireologyAdapter:
    name = "hireology"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _public_url_identity(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        identity = _public_url_identity(url)
        if identity is None:
            return None
        tenant, _opening_id = identity
        return _board(tenant)

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        if _safe_https_url(page_url) is None:
            return None
        evidence = _custom_page_evidence(page.html)
        if evidence is None:
            return None
        return _board(evidence)

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        root = _board_root(board)
        if root is None:
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                inventory_complete=False,
                error="invalid Hireology inventory root",
            )

        v1_result = self._list_jobs_v1(fetcher, board, query, root)
        if v1_result is not None:
            return v1_result

        target_title = _normalized_title(query.title)
        candidates: list[JobCandidate] = []
        employer_evidence: list[ProviderPublishedEmployerEvidence] = []
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        api_urls: list[str] = []
        expected_count: int | None = None
        records_seen = 0
        exact_title_found = False
        inventory_complete = False
        stop_reason = "page_cap_reached"

        for page_number in range(1, _MAX_PAGES + 1):
            api_url = _api_url(root, page_number)
            api_urls.append(api_url)
            try:
                page = fetcher.fetch(
                    api_url,
                    headers={"Accept": "application/json", "Referer": board.url},
                )
            except (FetchError, OSError, TimeoutError) as error:
                code = provider_fetch_reason(error)
                return _result(
                    board,
                    candidates=candidates,
                    employer_evidence=employer_evidence,
                    reason_code=code,
                    retryable=reason_spec(code).retryable,
                    inventory_complete=False,
                    api_urls=api_urls,
                    records_seen=records_seen,
                    exact_title_found=exact_title_found,
                    stop_reason="fetch_failed",
                    error=str(error),
                )

            final_url = page.final_url or page.url
            if _api_identity(final_url) != (root, page_number, _PAGE_SIZE):
                return _result(
                    board,
                    candidates=candidates,
                    employer_evidence=employer_evidence,
                    reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                    inventory_complete=False,
                    api_urls=api_urls,
                    records_seen=records_seen,
                    exact_title_found=exact_title_found,
                    stop_reason="api_final_url_drift",
                    rejected_final_url=final_url,
                )

            parsed = _inventory_page(page.html, root, page_number)
            if isinstance(parsed, str):
                bounded = parsed in {"response_cap_exceeded", "record_cap_exceeded"}
                return _result(
                    board,
                    candidates=candidates,
                    employer_evidence=employer_evidence,
                    reason_code=(
                        "FETCH_BUDGET_EXHAUSTED"
                        if bounded
                        else "INVALID_STRUCTURED_DATA"
                    ),
                    retryable=bounded,
                    inventory_complete=False,
                    api_urls=api_urls,
                    records_seen=records_seen,
                    exact_title_found=exact_title_found,
                    stop_reason=parsed,
                    response_source=page.source,
                )

            records, count = parsed
            if expected_count is None:
                expected_count = count
            elif count != expected_count:
                return _result(
                    board,
                    candidates=candidates,
                    employer_evidence=employer_evidence,
                    reason_code="INVALID_STRUCTURED_DATA",
                    inventory_complete=False,
                    api_urls=api_urls,
                    records_seen=records_seen,
                    exact_title_found=exact_title_found,
                    stop_reason="count_drift",
                    response_source=page.source,
                )

            for record in records:
                parsed_record = _candidate(record, root, api_url)
                if parsed_record is None:
                    return _result(
                        board,
                        candidates=candidates,
                        employer_evidence=employer_evidence,
                        reason_code="INVALID_STRUCTURED_DATA",
                        inventory_complete=False,
                        api_urls=api_urls,
                        records_seen=records_seen,
                        exact_title_found=exact_title_found,
                        stop_reason="invalid_job_record",
                        response_source=page.source,
                    )
                opening_id, candidate, evidence = parsed_record
                if opening_id in seen_ids or candidate.url in seen_urls:
                    return _result(
                        board,
                        candidates=candidates,
                        employer_evidence=employer_evidence,
                        reason_code="INVALID_STRUCTURED_DATA",
                        inventory_complete=False,
                        api_urls=api_urls,
                        records_seen=records_seen,
                        exact_title_found=exact_title_found,
                        stop_reason="duplicate_job",
                        response_source=page.source,
                    )
                seen_ids.add(opening_id)
                seen_urls.add(candidate.url)
                records_seen += 1
                if records_seen > _MAX_RECORDS:
                    return _result(
                        board,
                        candidates=candidates,
                        employer_evidence=employer_evidence,
                        reason_code="FETCH_BUDGET_EXHAUSTED",
                        retryable=True,
                        inventory_complete=False,
                        api_urls=api_urls,
                        records_seen=records_seen,
                        exact_title_found=exact_title_found,
                        stop_reason="record_cap_exceeded",
                        response_source=page.source,
                    )
                candidates.append(candidate)
                if evidence is not None:
                    employer_evidence.append(evidence)
                if (
                    target_title
                    and _normalized_title(candidate.title) == target_title
                ):
                    exact_title_found = True

            if records_seen >= expected_count:
                inventory_complete = True
                stop_reason = "complete"
                break
            if not records:
                return _result(
                    board,
                    candidates=candidates,
                    employer_evidence=employer_evidence,
                    reason_code="INVALID_STRUCTURED_DATA",
                    inventory_complete=False,
                    api_urls=api_urls,
                    records_seen=records_seen,
                    exact_title_found=exact_title_found,
                    stop_reason="premature_empty_page",
                    response_source=page.source,
                )
        reason_code = None if candidates else "EMPTY_PROVIDER_RESPONSE"
        return _result(
            board,
            candidates=candidates,
            employer_evidence=employer_evidence,
            reason_code=reason_code,
            inventory_complete=inventory_complete,
            api_urls=api_urls,
            records_seen=records_seen,
            expected_count=expected_count,
            exact_title_found=exact_title_found,
            stop_reason=stop_reason,
        )

    def _list_jobs_v1(
        self,
        fetcher,
        board: JobBoard,
        query: JobQuery,
        root: str,
    ) -> AdapterResult | None:
        inventory_url = _v1_api_url(root)
        try:
            page = fetcher.fetch(
                inventory_url,
                headers={"Accept": "application/json", "Referer": board.url},
            )
        except (FetchError, OSError, TimeoutError):
            return None
        if _v1_api_identity(page.final_url or page.url) != root:
            return None
        records = _v1_inventory(page.html)
        if records is None:
            return None

        candidates: list[JobCandidate] = []
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        for record in records:
            parsed = _v1_candidate(record, root)
            if parsed is None:
                return None
            opening_id, candidate = parsed
            if opening_id in seen_ids or candidate.url in seen_urls:
                return None
            seen_ids.add(opening_id)
            seen_urls.add(candidate.url)
            candidates.append(candidate)

        target_title = _normalized_title(query.title)
        api_urls = [inventory_url]
        employer_evidence: list[ProviderPublishedEmployerEvidence] = []
        detail_errors: list[dict[str, str]] = []
        for index, candidate in enumerate(candidates):
            if not target_title or _normalized_title(candidate.title) != target_title:
                continue
            opening_id = str(candidate.raw["id"])
            detail_api_url = _detail_api_url(opening_id)
            api_urls.append(detail_api_url)
            try:
                detail_page = fetcher.fetch(
                    detail_api_url,
                    headers={"Accept": "application/json", "Referer": board.url},
                )
            except (FetchError, OSError, TimeoutError) as error:
                detail_errors.append(
                    {
                        "url": detail_api_url,
                        "reason_code": provider_fetch_reason(error),
                    }
                )
                continue
            if _detail_api_identity(
                detail_page.final_url or detail_page.url
            ) != opening_id:
                detail_errors.append(
                    {
                        "url": detail_api_url,
                        "reason_code": "PROVIDER_VARIANT_UNSUPPORTED",
                    }
                )
                continue
            detailed = _detail_record(detail_page.html, root, detail_api_url)
            if detailed is None or detailed[0] != opening_id:
                detail_errors.append(
                    {
                        "url": detail_api_url,
                        "reason_code": "INVALID_STRUCTURED_DATA",
                    }
                )
                continue
            _opening_id, detailed_candidate, evidence = detailed
            candidates[index] = detailed_candidate
            if evidence is not None:
                employer_evidence.append(evidence)

        return _result(
            board,
            candidates=candidates,
            employer_evidence=employer_evidence,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            inventory_complete=True,
            api_urls=api_urls,
            records_seen=len(candidates),
            expected_count=len(candidates),
            exact_title_found=bool(
                target_title
                and any(
                    _normalized_title(candidate.title) == target_title
                    for candidate in candidates
                )
            ),
            stop_reason="complete",
            variant="public_careers_api_v1_with_v2_detail",
            detail_errors=detail_errors,
        )


class _EvidenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonical_urls: list[str] = []
        self.asset_urls: list[str] = []
        self.inline_scripts: list[str] = []
        self._inside_script = False
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): value for name, value in attrs}
        lowered = tag.casefold()
        if lowered == "link":
            rel = values.get("rel") or ""
            href = values.get("href")
            if (
                isinstance(href, str)
                and "canonical" in rel.casefold().split()
            ):
                self.canonical_urls.append(href)
        if lowered != "script":
            return
        source = values.get("src")
        if isinstance(source, str):
            self.asset_urls.append(source)
        else:
            self._inside_script = True
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._inside_script:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._inside_script:
            self.inline_scripts.append("".join(self._script_parts))
            self._inside_script = False
            self._script_parts = []


def _custom_page_evidence(html: object) -> str | None:
    if not isinstance(html, str) or not html or len(html) > _MAX_RESPONSE_CHARS:
        return None
    parser = _EvidenceParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None

    canonical_tenants = {
        identity[0]
        for url in parser.canonical_urls
        if (identity := _public_url_identity(url)) is not None
    }
    api_tenants = {
        tenant
        for script in parser.inline_scripts
        for url in _provider_api_urls_in_text(script)
        if (tenant := _page_api_tenant(url)) is not None
    }
    official_asset = any(_is_official_asset(url) for url in parser.asset_urls)
    tenants = canonical_tenants | api_tenants
    if len(tenants) != 1:
        return None
    tenant = next(iter(tenants))
    if canonical_tenants and api_tenants and canonical_tenants != api_tenants:
        return None
    if not api_tenants and not official_asset:
        return None
    return tenant


def _provider_api_urls_in_text(value: str) -> list[str]:
    if len(value) > _MAX_RESPONSE_CHARS:
        return []
    pattern = re.compile(
        r"https://api\.hireology\.com/"
        r"(?:v1/careers/[a-z0-9][a-z0-9-]{0,99}|"
        r"v2/public/careers/[a-z0-9][a-z0-9-]{0,99}"
        r"\?[^\"'<>\s]{1,200})"
    )
    return [match.group(0) for match in pattern.finditer(value)]


def _page_api_tenant(url: object) -> str | None:
    parsed = _safe_https_url(url)
    if parsed is None or (parsed.hostname or "").casefold() != _API_HOST:
        return None
    v1_prefix = "/v1/careers/"
    if parsed.path.startswith(v1_prefix) and not parsed.query and not parsed.fragment:
        tenant = unquote(parsed.path[len(v1_prefix) :])
        if tenant and "/" not in tenant and _TENANT.fullmatch(tenant):
            return tenant
        return None
    identity = _api_identity(url)
    return identity[0] if identity is not None else None


def _is_official_asset(url: object) -> bool:
    parsed = _safe_https_url(url)
    return bool(
        parsed is not None
        and (parsed.hostname or "").casefold() in _OFFICIAL_ASSET_HOSTS
        and not parsed.query
        and not parsed.fragment
    )


def _public_url_identity(url: object) -> tuple[str, str | None] | None:
    parsed = _safe_https_url(url)
    if (
        parsed is None
        or (parsed.hostname or "").casefold() != _PUBLIC_HOST
        or parsed.query
        or parsed.fragment
    ):
        return None
    if "%" in parsed.path or "//" in parsed.path:
        return None
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) == 1 and _TENANT.fullmatch(parts[0]):
        return parts[0], None
    if (
        len(parts) == 3
        and _TENANT.fullmatch(parts[0])
        and _OPENING_ID.fullmatch(parts[1])
        and parts[2] == "description"
    ):
        return parts[0], parts[1]
    return None


def _safe_https_url(url: object):
    if not isinstance(url, str) or not url or len(url) > 8_192:
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


def _board(tenant: str) -> JobBoard:
    return JobBoard(
        url=f"https://{_PUBLIC_HOST}/{quote(tenant, safe='-')}",
        provider="hireology",
        identifier=tenant,
        replay_safe=True,
    )


def _board_root(board: JobBoard) -> str | None:
    if (
        board.provider != "hireology"
        or not isinstance(board.identifier, str)
        or _TENANT.fullmatch(board.identifier) is None
    ):
        return None
    identified = _public_url_identity(board.url)
    if identified != (board.identifier, None):
        return None
    return board.identifier


def _api_url(root: str, page: int) -> str:
    return (
        f"https://{_API_HOST}{_API_PATH_PREFIX}{quote(root, safe='-')}"
        f"?page_size={_PAGE_SIZE}&page={page}"
    )


def _v1_api_url(root: str) -> str:
    return f"https://{_API_HOST}{_V1_API_PATH_PREFIX}{quote(root, safe='-')}"


def _v1_api_identity(url: object) -> str | None:
    parsed = _safe_https_url(url)
    if (
        parsed is None
        or (parsed.hostname or "").casefold() != _API_HOST
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(_V1_API_PATH_PREFIX)
    ):
        return None
    root = unquote(parsed.path[len(_V1_API_PATH_PREFIX) :])
    if not root or "/" in root or _TENANT.fullmatch(root) is None:
        return None
    return root


def _detail_api_url(opening_id: str) -> str:
    return f"https://{_API_HOST}{_DETAIL_API_PATH_PREFIX}{opening_id}"


def _detail_api_identity(url: object) -> str | None:
    parsed = _safe_https_url(url)
    if (
        parsed is None
        or (parsed.hostname or "").casefold() != _API_HOST
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(_DETAIL_API_PATH_PREFIX)
    ):
        return None
    opening_id = unquote(parsed.path[len(_DETAIL_API_PATH_PREFIX) :])
    if "/" in opening_id or _OPENING_ID.fullmatch(opening_id) is None:
        return None
    return opening_id


def _api_identity(url: object) -> tuple[str, int, int] | None:
    parsed = _safe_https_url(url)
    if (
        parsed is None
        or (parsed.hostname or "").casefold() != _API_HOST
        or parsed.fragment
        or not parsed.path.startswith(_API_PATH_PREFIX)
    ):
        return None
    root = unquote(parsed.path[len(_API_PATH_PREFIX) :])
    if not root or "/" in root or _TENANT.fullmatch(root) is None:
        return None
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    if len(query) != 2 or {key for key, _value in query} != {"page", "page_size"}:
        return None
    values = dict(query)
    if not values["page"].isdigit() or not values["page_size"].isdigit():
        return None
    page = int(values["page"])
    page_size = int(values["page_size"])
    if page < 1 or page_size != _PAGE_SIZE:
        return None
    return root, page, page_size


def _v1_inventory(raw: object) -> list[dict[str, Any]] | None:
    if not isinstance(raw, str) or len(raw) > _MAX_RESPONSE_CHARS:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"data"}:
        return None
    records = payload["data"]
    if (
        not isinstance(records, list)
        or len(records) > _MAX_RECORDS
        or any(not isinstance(record, dict) for record in records)
    ):
        return None
    return records


def _v1_candidate(
    record: dict[str, Any],
    inventory_root: str,
) -> tuple[str, JobCandidate] | None:
    if set(record) != {"attributes", "id", "type"} or record.get("type") != "careers":
        return None
    attributes = record.get("attributes")
    if not isinstance(attributes, dict):
        return None
    record_id = _positive_id(record.get("id"))
    attribute_id = _positive_id(attributes.get("id"))
    title = _public_text(attributes.get("name"))
    status = _public_text(attributes.get("status"))
    detail_url = attributes.get("career-site-url")
    if (
        record_id is None
        or attribute_id != record_id
        or title is None
        or status is None
        or status.casefold() != "open"
        or not isinstance(detail_url, str)
    ):
        return None
    detail_identity = _public_url_identity(detail_url)
    if detail_identity is None or detail_identity[1] is None:
        return None
    child_tenant, opening_id = detail_identity
    location = _v1_location(attributes.get("locations"), attributes.get("remote"))
    if location is None and attributes.get("locations") not in (None, []):
        return None
    opening_url = (
        f"https://{_PUBLIC_HOST}/{inventory_root}/{opening_id}/description"
    )
    return opening_id, JobCandidate(
        title=title,
        url=opening_url,
        provider="hireology",
        location=location,
        raw={
            "id": opening_id,
            "status": "Open",
            "inventory_root": inventory_root,
            "opening_tenant": child_tenant,
            "provider_returned_career_site_url": (
                f"https://{_PUBLIC_HOST}/{child_tenant}/{opening_id}/description"
            ),
            "v1_record_id": record_id,
            "hiring_organization_name": None,
        },
    )


def _detail_record(
    raw: object,
    inventory_root: str,
    api_url: str,
) -> tuple[str, JobCandidate, ProviderPublishedEmployerEvidence | None] | None:
    if not isinstance(raw, str) or len(raw) > _MAX_RESPONSE_CHARS:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"data"}:
        return None
    record = payload["data"]
    if not isinstance(record, dict):
        return None
    return _candidate(record, inventory_root, api_url)


def _inventory_page(
    raw: object,
    root: str,
    expected_page: int,
) -> tuple[list[dict[str, Any]], int] | str:
    if not isinstance(raw, str) or len(raw) > _MAX_RESPONSE_CHARS:
        return "response_cap_exceeded"
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return "invalid_json"
    if not isinstance(payload, dict) or set(payload) != {
        "data",
        "count",
        "page",
        "page_size",
    }:
        return "invalid_envelope"
    records = payload.get("data")
    count = _nonnegative_int(payload.get("count"))
    page = _positive_int(payload.get("page"))
    page_size = _positive_int(payload.get("page_size"))
    if (
        not isinstance(records, list)
        or len(records) > _PAGE_SIZE
        or count is None
        or page != expected_page
        or page_size != _PAGE_SIZE
        or count < len(records)
    ):
        return "invalid_pagination"
    if count > _MAX_RECORDS:
        return "record_cap_exceeded"
    if any(not isinstance(record, dict) for record in records):
        return "invalid_records"
    return records, count


def _candidate(
    record: dict[str, Any],
    inventory_root: str,
    api_url: str,
) -> tuple[str, JobCandidate, ProviderPublishedEmployerEvidence | None] | None:
    opening_id = _positive_id(record.get("id"))
    title = _public_text(record.get("name"))
    status = _public_text(record.get("status"))
    detail_url = record.get("career_site_url")
    if (
        opening_id is None
        or title is None
        or status is None
        or not isinstance(detail_url, str)
    ):
        return None
    detail_identity = _public_url_identity(detail_url)
    if detail_identity is None or detail_identity[1] != opening_id:
        return None
    child_tenant = detail_identity[0]
    expected_path = f"/{child_tenant}/{opening_id}/description"
    if record.get("career_site_path") != expected_path:
        return None
    if status.casefold() != "open":
        return None

    location = _location(record.get("locations"), record.get("remote"))
    if location is None and record.get("locations") not in (None, []):
        return None
    organization = record.get("organization")
    if organization is not None and not isinstance(organization, dict):
        return None
    organization_name = _organization_name(organization)
    if isinstance(organization, dict) and organization_name is None:
        return None
    canonical_child_url = (
        f"https://{_PUBLIC_HOST}/{child_tenant}/{opening_id}/description"
    )
    opening_url = (
        f"https://{_PUBLIC_HOST}/{inventory_root}/{opening_id}/description"
    )
    raw = {
        "id": opening_id,
        "status": "Open",
        "inventory_root": inventory_root,
        "opening_tenant": child_tenant,
        "provider_returned_career_site_url": canonical_child_url,
        "hiring_organization_name": organization_name,
    }
    candidate = JobCandidate(
        title=title,
        url=opening_url,
        provider="hireology",
        location=location,
        raw=raw,
    )
    evidence = None
    if organization_name is not None:
        try:
            evidence = ProviderPublishedEmployerEvidence(
                employer_name=organization_name,
                descriptor_terms=(),
                evidence_url=api_url,
                opening_url=candidate.url,
                extraction_method="hireology_organization",
            )
        except (TypeError, ValueError):
            return None
    return opening_id, candidate, evidence


def _organization_name(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    return _public_text(value.get("name"))


def _location(locations: object, remote: object) -> str | None:
    if locations is None:
        locations = []
    if not isinstance(locations, list) or len(locations) > 100:
        return None
    values: list[str] = []
    if remote is True:
        values.append("Remote")
    elif remote is not False and remote is not None:
        return None
    for item in locations:
        if not isinstance(item, dict):
            return None
        city = _public_text(item.get("city"))
        state = _public_text(item.get("state"))
        if city and state:
            location = f"{city}, {state}"
        else:
            location = city or state
        if location and location not in values:
            values.append(location)
    return "; ".join(values) if values else None


def _v1_location(locations: object, remote: object) -> str | None:
    if locations is None:
        locations = []
    if not isinstance(locations, list) or len(locations) > 100:
        return None
    values: list[str] = []
    if remote is True:
        values.append("Remote")
    elif remote is not False and remote is not None:
        return None
    for item in locations:
        location = _public_text(item)
        if location and location not in values:
            values.append(location)
        elif location is None:
            return None
    return "; ".join(values) if values else None


def _public_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > _MAX_FIELD_CHARS:
        return None
    return normalized


def _positive_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        return None
    return text if _OPENING_ID.fullmatch(text) else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _normalized_title(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    employer_evidence: list[ProviderPublishedEmployerEvidence] | None = None,
    reason_code: str | None,
    retryable: bool = False,
    inventory_complete: bool,
    api_urls: list[str] | None = None,
    error: str | None = None,
    rejected_final_url: str | None = None,
    response_source: str | None = None,
    records_seen: int | None = None,
    expected_count: int | None = None,
    exact_title_found: bool | None = None,
    stop_reason: str | None = None,
    variant: str = "public_careers_api_v2",
    detail_errors: list[dict[str, str]] | None = None,
) -> AdapterResult:
    trace: dict[str, Any] = {
        "adapter": "hireology",
        "variant": variant,
        "api_urls": api_urls or [],
        "inventory_root": board.identifier,
        "inventory_scope": "full" if inventory_complete else "partial",
        "inventory_complete": inventory_complete,
    }
    optional = {
        "error": error,
        "rejected_final_url": rejected_final_url,
        "response_source": response_source,
        "records_seen": records_seen,
        "expected_count": expected_count,
        "exact_title_found": exact_title_found,
        "stop_reason": stop_reason,
        "detail_errors": detail_errors or None,
    }
    trace.update({key: value for key, value in optional.items() if value is not None})
    return AdapterResult(
        provider="hireology",
        board=board,
        candidates=candidates or [],
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope="full" if inventory_complete else "partial",
        inventory_complete=inventory_complete,
        employer_evidence=tuple(employer_evidence or ()),
        trace=trace,
    )


ADAPTER = HireologyAdapter()
