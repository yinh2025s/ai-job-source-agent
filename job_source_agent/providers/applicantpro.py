from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from urllib.parse import unquote, urlencode, urlparse

from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_LEGACY_SUFFIX = ".applicantpro.com"
_CANONICAL_HOST = "www.applicantpro.com"
_TENANT = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_CANONICAL_PATH = re.compile(r"^/openings/(?P<tenant>[a-z0-9-]+)/jobs/?$", re.I)
_DETAIL_PATH = re.compile(r"^/jobs/(?P<job_id>[0-9]{1,20})(?P<html>\.html)?/?$")
_ROUTE_DATA = re.compile(r"\bmountingData\.courierCurrentRouteData\s*=")
_JOB_LISTINGS = re.compile(r"\[\s*['\"]JobListings['\"]\s*\]")
_COMPONENT_FIELD = re.compile(
    r"\b(?P<name>organizationId|domainId|domainName|subdomainName)\s*:\s*"
    r"(?:(?P<quote>['\"])(?P<string>[^'\"\\]{1,255})(?P=quote)|(?P<number>[0-9]{1,20}))"
)
_MAX_HTML_CHARS = 2_000_000
_MAX_INVENTORY_CHARS = 5_000_000
_MAX_JOBS = 2_000


class ApplicantProAdapter:
    name = "applicantpro"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _url_tenant(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        tenant = _url_tenant(url)
        return _job_board(tenant) if tenant is not None else None

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        tenant = _board_tenant(board)
        if tenant is None:
            return _unsupported(board, "invalid ApplicantPro board identity")

        board_url = _canonical_board_url(tenant)
        try:
            page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, error, board_url=board_url)

        final_url = page.final_url or page.url
        if _listing_response_tenant(final_url) != tenant:
            if _is_login_url(final_url, tenant):
                return _incomplete(
                    board,
                    "LOGIN_REQUIRED",
                    "ApplicantPro board redirected to login",
                    board_url=board_url,
                    response_source=page.source,
                )
            return _unsupported(
                board,
                "ApplicantPro board redirected outside the declared tenant",
                board_url=board_url,
                response_source=page.source,
                rejected_url=final_url,
            )

        shell_reason = _shell_reason(page.html)
        if shell_reason is not None:
            return _incomplete(
                board,
                shell_reason,
                "ApplicantPro public board returned a blocked page",
                board_url=board_url,
                response_source=page.source,
                failure_class="login_required" if shell_reason == "LOGIN_REQUIRED" else "http_forbidden",
            )

        identity = _page_identity(page.html, tenant)
        if identity is None:
            failure_class = "javascript_required" if _javascript_shell(page.html) else "invalid_page_identity"
            return _unsupported(
                board,
                "missing or contradictory ApplicantPro public page identity",
                board_url=board_url,
                response_source=page.source,
                failure_class=failure_class,
            )

        career_site_name, organization_id, domain_id = identity
        identity_trace = {
            "tenant": tenant,
            "career_site_name": career_site_name,
            "organization_id": organization_id,
            "domain_id": domain_id,
        }
        inventory_url = _inventory_url(tenant, domain_id)
        try:
            inventory_page = fetcher.fetch(
                inventory_url,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": board_url,
                },
            )
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(
                board,
                error,
                board_url=board_url,
                response_source=page.source,
                identity=identity_trace,
                api_url=inventory_url,
            )

        response_url = inventory_page.final_url or inventory_page.url
        if not _same_inventory_url(response_url, inventory_url):
            return _unsupported(
                board,
                "ApplicantPro inventory redirected outside the verified tenant endpoint",
                board_url=board_url,
                response_source=inventory_page.source,
                rejected_url=response_url,
            )

        parsed = _parse_inventory(inventory_page.html, tenant, domain_id)
        if parsed is None:
            return _incomplete(
                board,
                "INVALID_STRUCTURED_DATA",
                "invalid or contradictory ApplicantPro public inventory",
                board_url=board_url,
                response_source=inventory_page.source,
                identity=identity_trace,
                api_url=inventory_url,
            )
        jobs, total = parsed
        candidates = [
            candidate
            for job in jobs
            if (candidate := _job_candidate(job, tenant, domain_id)) is not None
        ]
        if len(candidates) != len(jobs):
            return _incomplete(
                board,
                "INVALID_STRUCTURED_DATA",
                "ApplicantPro inventory contained an invalid or cross-tenant opening",
                board_url=board_url,
                response_source=inventory_page.source,
                identity=identity_trace,
                api_url=inventory_url,
            )

        target = _normalized_title(query.title)
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code="EMPTY_PROVIDER_RESPONSE" if not candidates else None,
            inventory_scope="full",
            inventory_complete=True,
            trace={
                "adapter": self.name,
                "variant": "applicantpro_public_inventory",
                "board_urls": [board_url],
                "api_urls": [inventory_url],
                "response_source": inventory_page.source,
                "identity": identity_trace,
                "records_seen": len(jobs),
                "total": total,
                "candidate_count": len(candidates),
                "exact_title_found": bool(
                    target
                    and any(_normalized_title(candidate.title) == target for candidate in candidates)
                ),
                "inventory_scope": "full",
                "inventory_complete": True,
            },
        )


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.bodies: list[str] = []
        self._in_script = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "script":
            self._in_script = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._in_script:
            self.bodies.append("".join(self._parts))
            self._in_script = False
            self._parts = []


def _safe_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None
    return parsed, host


def _legacy_tenant(host: str) -> str | None:
    if not host.endswith(_LEGACY_SUFFIX) or host == _CANONICAL_HOST:
        return None
    tenant = host[: -len(_LEGACY_SUFFIX)]
    return tenant if _TENANT.fullmatch(tenant) else None


def _normalized_path(path: str) -> str:
    return "/" + "/".join(part for part in unquote(path).split("/") if part)


def _url_tenant(url: str) -> str | None:
    parsed_host = _safe_url(url)
    if parsed_host is None:
        return None
    parsed, host = parsed_host
    path = _normalized_path(parsed.path)
    if host == _CANONICAL_HOST:
        match = _CANONICAL_PATH.fullmatch(path)
        tenant = match.group("tenant").casefold() if match else None
        return tenant if tenant and _TENANT.fullmatch(tenant) else None
    tenant = _legacy_tenant(host)
    if tenant is None:
        return None
    if path == "/" and not parsed.query:
        return tenant
    if path.casefold() in {"/jobs", "/iframe"}:
        return tenant
    return tenant if _DETAIL_PATH.fullmatch(path) else None


def _listing_response_tenant(url: str) -> str | None:
    parsed_host = _safe_url(url)
    if parsed_host is None:
        return None
    parsed, host = parsed_host
    if parsed.query:
        return None
    path = _normalized_path(parsed.path)
    if host == _CANONICAL_HOST:
        match = _CANONICAL_PATH.fullmatch(path)
        return match.group("tenant").casefold() if match else None
    tenant = _legacy_tenant(host)
    return tenant if tenant and path.casefold() in {"/jobs", "/iframe"} else None


def _canonical_board_url(tenant: str) -> str:
    return f"https://{_CANONICAL_HOST}/openings/{tenant}/jobs"


def _job_board(tenant: str) -> JobBoard:
    return JobBoard(
        url=_canonical_board_url(tenant),
        provider="applicantpro",
        identifier=tenant,
    )


def _board_tenant(board: JobBoard) -> str | None:
    if (
        board.provider != "applicantpro"
        or not isinstance(board.identifier, str)
        or not _TENANT.fullmatch(board.identifier)
    ):
        return None
    tenant = board.identifier.casefold()
    return tenant if board.url == _canonical_board_url(tenant) else None


def _page_identity(html: str, tenant: str) -> tuple[str, str, str] | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    parser = _ScriptParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None

    route_values: list[tuple[str, str, str]] = []
    component_values: list[tuple[str, str, str, str]] = []
    decoder = json.JSONDecoder()
    for script in parser.bodies:
        for match in _ROUTE_DATA.finditer(script):
            try:
                value, _end = decoder.raw_decode(script[match.end() :].lstrip())
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            route = _route_identity(value)
            if route is not None:
                route_values.append(route)
        if _JOB_LISTINGS.search(script):
            fields: dict[str, str] = {}
            for match in _COMPONENT_FIELD.finditer(script):
                value = match.group("string") or match.group("number")
                previous = fields.setdefault(match.group("name"), value)
                if previous != value:
                    return None
            required = {"organizationId", "domainId", "domainName", "subdomainName"}
            if set(fields) == required:
                component_values.append(
                    (
                        fields["organizationId"],
                        fields["domainId"],
                        fields["domainName"].casefold(),
                        fields["subdomainName"].casefold(),
                    )
                )

    if len(set(route_values)) != 1 or len(set(component_values)) != 1:
        return None
    route = route_values[0]
    component = component_values[0]
    if component != (route[1], route[2], "applicantpro.com", tenant):
        return None
    return route


def _route_identity(value: object) -> tuple[str, str, str] | None:
    if not isinstance(value, dict):
        return None
    career_site_name = value.get("career_site_name")
    organization_id = value.get("organization_id")
    domain_id = value.get("domain_id")
    if not isinstance(career_site_name, str) or not career_site_name.strip():
        return None
    if not _positive_identifier(organization_id) or not _positive_identifier(domain_id):
        return None
    return career_site_name.strip(), str(organization_id), str(domain_id)


def _positive_identifier(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return False
    text = str(value)
    return text.isdigit() and text != "0" and len(text) <= 20


def _inventory_url(tenant: str, domain_id: str) -> str:
    query = urlencode({"getParams": json.dumps({"isInternal": 0}, separators=(",", ":"))})
    return f"https://{tenant}{_LEGACY_SUFFIX}/core/jobs/{domain_id}?{query}"


def _same_inventory_url(actual_url: str, expected_url: str) -> bool:
    try:
        actual = urlparse(actual_url)
        expected = urlparse(expected_url)
        return actual == expected and actual.port in {None, 443}
    except (TypeError, ValueError):
        return False


def _parse_inventory(raw: str, tenant: str, domain_id: str) -> tuple[list[dict], int] | None:
    if not isinstance(raw, str) or len(raw) > _MAX_INVENTORY_CHARS:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return None
    jobs = data["jobs"]
    total = data.get("jobCount", data.get("total", len(jobs)))
    if (
        len(jobs) > _MAX_JOBS
        or any(not isinstance(job, dict) for job in jobs)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total != len(jobs)
    ):
        return None
    declared_domain_id = data.get("domainId")
    declared_tenant = data.get("subdomainName")
    if declared_domain_id is not None and str(declared_domain_id) != domain_id:
        return None
    if declared_tenant is not None and (
        not isinstance(declared_tenant, str) or declared_tenant.casefold() != tenant
    ):
        return None
    return jobs, total


def _job_candidate(job: dict, tenant: str, domain_id: str) -> JobCandidate | None:
    raw_id = job.get("id")
    title = job.get("title")
    url = job.get("jobUrl") or job.get("url")
    if (
        not _positive_identifier(raw_id)
        or not isinstance(title, str)
        or not title.strip()
        or len(title) > 500
        or not isinstance(url, str)
    ):
        return None
    parsed_host = _safe_url(url)
    if parsed_host is None:
        return None
    parsed, host = parsed_host
    match = _DETAIL_PATH.fullmatch(parsed.path)
    if (
        _legacy_tenant(host) != tenant
        or parsed.query
        or match is None
        or str(raw_id) != match.group("job_id")
    ):
        return None
    location = job.get("jobLocation") or job.get("location")
    if location is not None and (not isinstance(location, str) or len(location) > 500):
        return None
    suffix = ".html" if match.group("html") else ""
    return JobCandidate(
        title=title.strip(),
        url=f"https://{tenant}{_LEGACY_SUFFIX}/jobs/{raw_id}{suffix}",
        provider="applicantpro",
        location=location.strip() if isinstance(location, str) and location.strip() else None,
        raw={"job_id": str(raw_id), "domain_id": domain_id},
    )


def _shell_reason(html: str) -> str | None:
    text = " ".join((html or "").casefold().split())
    if any(marker in text for marker in ("403 forbidden", "access denied", "request forbidden")):
        return "HTTP_FORBIDDEN"
    if any(marker in text for marker in ("<title>login", 'type="password"', "sign in to continue")):
        return "LOGIN_REQUIRED"
    return None


def _javascript_shell(html: str) -> bool:
    text = " ".join((html or "").casefold().split())
    return any(
        marker in text
        for marker in (
            "javascript is required",
            "enable javascript to view",
            "please enable javascript",
        )
    )


def _is_login_url(url: str, tenant: str) -> bool:
    parsed_host = _safe_url(url)
    if parsed_host is None:
        return False
    parsed, host = parsed_host
    return (
        _legacy_tenant(host) == tenant
        and _normalized_path(parsed.path).casefold().startswith("/account")
    )


def _normalized_title(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if value else ""


def _fetch_failure(
    board: JobBoard,
    error: Exception,
    **trace_fields,
) -> AdapterResult:
    reason_code = classify_fetch_error(str(error))
    if reason_code == "FETCH_FAILED":
        reason_code = "PROVIDER_FETCH_FAILED"
    return _incomplete(
        board,
        reason_code,
        str(error),
        retryable=reason_spec(reason_code).retryable,
        **trace_fields,
    )


def _incomplete(
    board: JobBoard,
    reason_code: str,
    error: str,
    *,
    retryable: bool = False,
    board_url: str | None = None,
    response_source: str | None = None,
    identity: dict[str, str] | None = None,
    api_url: str | None = None,
    failure_class: str | None = None,
) -> AdapterResult:
    trace = {
        "adapter": "applicantpro",
        "variant": "applicantpro_public_inventory",
        "board_urls": [board_url] if board_url else [],
        "api_urls": [api_url] if api_url else [],
        "error": error,
        "inventory_scope": "unknown",
        "inventory_complete": False,
    }
    if response_source is not None:
        trace["response_source"] = response_source
    if identity is not None:
        trace["identity"] = identity
    if failure_class is not None:
        trace["failure_class"] = failure_class
    return AdapterResult(
        provider="applicantpro",
        board=board,
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope="unknown",
        inventory_complete=False,
        trace=trace,
    )


def _unsupported(
    board: JobBoard,
    error: str,
    *,
    board_url: str | None = None,
    response_source: str | None = None,
    rejected_url: str | None = None,
    failure_class: str | None = None,
) -> AdapterResult:
    result = _incomplete(
        board,
        "PROVIDER_VARIANT_UNSUPPORTED",
        error,
        board_url=board_url,
        response_source=response_source,
        failure_class=failure_class,
    )
    if rejected_url is not None:
        result.trace["rejected_final_url"] = rejected_url
    return result


ADAPTER = ApplicantProAdapter()
