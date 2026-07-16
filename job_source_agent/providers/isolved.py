from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from urllib.parse import unquote, urlparse

from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError
from .base import AdapterResult, JobBoard, JobQuery


_HOST_SUFFIX = ".isolvedhire.com"
_TENANT = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ROUTE_DATA = re.compile(r"\bmountingData\.courierCurrentRouteData\s*=")
_JOB_LISTINGS = re.compile(r"\[\s*['\"]JobListings['\"]\s*\]")
_COMPONENT_FIELD = re.compile(
    r"\b(?P<name>organizationId|domainId|domainName|subdomainName)\s*:\s*"
    r"(?:(?P<quote>['\"])(?P<string>[^'\"\\]{1,255})(?P=quote)|(?P<number>[0-9]{1,20}))"
)
_MAX_HTML_CHARS = 2_000_000


class ISolvedAdapter:
    name = "isolved"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _url_tenant(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        tenant = _url_tenant(url)
        if tenant is None:
            return None
        return _job_board(tenant)

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        tenant = _board_tenant(board)
        if tenant is None:
            return _unsupported(board, "invalid iSolved Applicant Pro board identity")

        board_url = _board_url(tenant)
        try:
            page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            reason_code = classify_fetch_error(str(error))
            if reason_code == "FETCH_FAILED":
                reason_code = "PROVIDER_FETCH_FAILED"
            return _incomplete(
                board,
                reason_code,
                str(error),
                retryable=reason_spec(reason_code).retryable,
                board_url=board_url,
            )

        final_tenant = _exact_board_tenant(page.final_url or page.url)
        if final_tenant != tenant:
            return _unsupported(
                board,
                "iSolved board redirected outside the canonical tenant jobs route",
                board_url=board_url,
                response_source=page.source,
            )

        identity = _page_identity(page.html, tenant)
        if identity is None:
            return _unsupported(
                board,
                "missing or contradictory iSolved Applicant Pro public page identity",
                board_url=board_url,
                response_source=page.source,
            )

        career_site_name, organization_id, domain_id = identity
        # The frozen module only re-exports an unfrozen chunk. Without that chunk,
        # the anonymous inventory transport and response schema are not proven.
        return _incomplete(
            board,
            "PROVIDER_VARIANT_UNSUPPORTED",
            "anonymous iSolved inventory transport was not present in frozen evidence",
            board_url=board_url,
            response_source=page.source,
            identity={
                "tenant": tenant,
                "career_site_name": career_site_name,
                "organization_id": organization_id,
                "domain_id": domain_id,
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
        parsed.scheme != "https"
        or not host.endswith(_HOST_SUFFIX)
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None
    tenant = host[: -len(_HOST_SUFFIX)]
    if not _TENANT.fullmatch(tenant):
        return None
    return parsed, tenant


def _normalized_path(path: str) -> str:
    return "/" + "/".join(part for part in unquote(path).split("/") if part)


def _url_tenant(url: str) -> str | None:
    parsed_tenant = _safe_url(url)
    if parsed_tenant is None:
        return None
    parsed, tenant = parsed_tenant
    return tenant if _normalized_path(parsed.path).casefold() == "/jobs" else None


def _exact_board_tenant(url: str) -> str | None:
    parsed_tenant = _safe_url(url)
    if parsed_tenant is None:
        return None
    parsed, tenant = parsed_tenant
    if parsed.query or _normalized_path(parsed.path).casefold() != "/jobs":
        return None
    return tenant


def _board_url(tenant: str) -> str:
    return f"https://{tenant}{_HOST_SUFFIX}/jobs/"


def _job_board(tenant: str) -> JobBoard:
    return JobBoard(url=_board_url(tenant), provider="isolved", identifier=tenant)


def _board_tenant(board: JobBoard) -> str | None:
    if (
        board.provider != "isolved"
        or not isinstance(board.identifier, str)
        or not _TENANT.fullmatch(board.identifier)
    ):
        return None
    tenant = _exact_board_tenant(board.url)
    return tenant if tenant == board.identifier else None


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
            payload = script[match.end() :].lstrip()
            try:
                value, _end = decoder.raw_decode(payload)
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
    if component != (route[1], route[2], "isolvedhire.com", tenant):
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


def _incomplete(
    board: JobBoard,
    reason_code: str,
    error: str,
    *,
    retryable: bool = False,
    board_url: str | None = None,
    response_source: str | None = None,
    identity: dict[str, str] | None = None,
) -> AdapterResult:
    trace = {
        "adapter": "isolved",
        "variant": "applicant_pro_detection_only",
        "board_urls": [board_url] if board_url else [],
        "api_urls": [],
        "error": error,
        "inventory_scope": "unknown",
        "inventory_complete": False,
    }
    if response_source is not None:
        trace["response_source"] = response_source
    if identity is not None:
        trace["identity"] = identity
    return AdapterResult(
        provider="isolved",
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
) -> AdapterResult:
    return _incomplete(
        board,
        "PROVIDER_VARIANT_UNSUPPORTED",
        error,
        board_url=board_url,
        response_source=response_source,
    )


ADAPTER = ISolvedAdapter()

