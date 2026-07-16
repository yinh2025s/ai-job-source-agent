from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import unquote, urljoin, urlparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST_SUFFIX = ".applytojob.com"
_WIDGET_HOST = "app.jazz.co"
_TENANT_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_WIDGET_PATH = re.compile(r"^/widgets/basic/create/([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)/?$")
_CURRENT_DETAIL_PATH = re.compile(
    r"^/apply/([A-Za-z0-9_-]{4,64})/([A-Za-z0-9](?:[A-Za-z0-9-]{0,198}[A-Za-z0-9])?)/?$"
)
_LEGACY_DETAIL_PATH = re.compile(r"^/apply/jobs/details/([A-Za-z0-9_-]{4,64})/?$")
_BOARD_PATHS = {"/apply": "current", "/apply/jobs": "legacy"}


class JazzHRAdapter:
    name = "jazzhr"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        widget_tenant = _widget_tenant(url)
        if widget_tenant is not None:
            return True
        parsed_tenant = _parsed_tenant_url(url)
        if parsed_tenant is None:
            return False
        parsed, _tenant = parsed_tenant
        path = unquote(parsed.path).rstrip("/")
        return (
            path in _BOARD_PATHS
            or _CURRENT_DETAIL_PATH.fullmatch(path) is not None
            or _LEGACY_DETAIL_PATH.fullmatch(path) is not None
        )

    def identify_board(self, url: str) -> JobBoard | None:
        widget_tenant = _widget_tenant(url)
        if widget_tenant is not None:
            return _job_board(widget_tenant, "current")

        parsed_tenant = _parsed_tenant_url(url)
        if parsed_tenant is None or not self.recognizes(url):
            return None
        parsed, tenant = parsed_tenant
        path = unquote(parsed.path).rstrip("/")
        variant = (
            "legacy"
            if path == "/apply/jobs" or _LEGACY_DETAIL_PATH.fullmatch(path)
            else "current"
        )
        return _job_board(tenant, variant)

    def identify_board_from_page(self, page) -> JobBoard | None:
        parser = _JazzHRBootstrapParser()
        try:
            parser.feed(page.html or "")
        except (TypeError, ValueError):
            return None
        tenants = set(parser.tenants)
        if len(tenants) != 1:
            return None
        return _job_board(tenants.pop(), "current")

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        board_identity = _board_identity(board)
        if board_identity is None:
            return _unsupported(board, "invalid JazzHR board tenant or route")
        tenant, variant = board_identity
        board_url = board.url

        try:
            page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                trace={"adapter": self.name, "board_urls": [board_url], "error": str(error)},
            )

        final_url = page.final_url or page.url
        if not _is_exact_tenant_board_url(final_url, tenant, variant):
            return _unsupported(
                board,
                "JazzHR board redirected away from the declared tenant route",
                final_url,
            )

        parser = _JazzHRJobsParser()
        try:
            parser.feed(page.html or "")
        except (TypeError, ValueError):
            return _invalid_response(board, board_url, "malformed JazzHR HTML")

        if not parser.is_fingerprinted(variant):
            return _invalid_response(
                board,
                board_url,
                f"missing JazzHR {variant} public-board fingerprint",
            )

        candidates: list[JobCandidate] = []
        seen: set[str] = set()
        rejected_links = 0
        for title, href in parser.job_links:
            detail = _detail_identity(href, tenant, board_url)
            if detail is None:
                rejected_links += 1
                continue
            detail_url, job_id = detail
            if detail_url in seen:
                continue
            seen.add(detail_url)
            candidates.append(
                JobCandidate(
                    title=title,
                    url=detail_url,
                    provider=self.name,
                    raw={"job_id": job_id},
                )
            )

        target = _normalized_title(query.title)
        inventory_complete = rejected_links == 0
        inventory_scope = "full" if inventory_complete else "partial"
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            inventory_scope=inventory_scope,
            inventory_complete=inventory_complete,
            trace={
                "adapter": self.name,
                "variant": f"public_{variant}_html",
                "board_urls": [board_url],
                "response_source": page.source,
                "candidate_count": len(candidates),
                "public_link_count": len(parser.job_links),
                "rejected_link_count": rejected_links,
                "exact_title_found": bool(
                    target
                    and any(_normalized_title(candidate.title) == target for candidate in candidates)
                ),
                "inventory_scope": inventory_scope,
                "inventory_complete": inventory_complete,
            },
        )


class _JazzHRJobsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_legacy_main = False
        self.has_legacy_container = False
        self.has_jobs_form = False
        self.has_jobs_table = False
        self.has_current_root = False
        self.has_current_board_list = False
        self.has_current_jobs_list = False
        self.has_jazzhr_brand = False
        self.job_links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._current_heading_depth = 0

    def is_fingerprinted(self, variant: str) -> bool:
        if variant == "legacy":
            return (
                self.has_legacy_main
                and self.has_legacy_container
                and self.has_jobs_form
                and self.has_jobs_table
                and self.has_jazzhr_brand
            )
        return (
            self.has_current_root
            and self.has_current_board_list
            and self.has_current_jobs_list
            and self.has_jazzhr_brand
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        element_id = attributes.get("id", "")
        classes = set(attributes.get("class", "").split())

        if element_id == "resumator_main_wrapper":
            self.has_legacy_main = True
        if element_id == "resumator_container_body":
            self.has_legacy_container = True
        if tag == "form" and attributes.get("action", "").rstrip("/") == "/apply/jobs":
            self.has_jobs_form = True
        if tag == "table" and element_id == "jobs_table":
            self.has_jobs_table = True
        if tag == "body" and {"resumator-jobboard-home", "jobboard"}.issubset(classes):
            self.has_current_root = True
        if "job-board-list" in classes:
            self.has_current_board_list = True
        if "jobs-list" in classes:
            self.has_current_jobs_list = True
        if element_id in {"resumator-logo", "resumator_powered_by"}:
            self.has_jazzhr_brand = True

        if tag == "h3" and "list-group-item-heading" in classes:
            self._current_heading_depth = 1
        elif self._current_heading_depth:
            self._current_heading_depth += 1

        is_legacy_link = tag == "a" and "job_title_link" in classes
        is_current_link = tag == "a" and self._current_heading_depth > 0
        if is_legacy_link or is_current_link:
            self._href = attributes.get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "a" and self._href is not None:
            title = " ".join("".join(self._text).split())
            if title:
                self.job_links.append((title, self._href))
            self._href = None
            self._text = []
        if self._current_heading_depth:
            self._current_heading_depth -= 1


class _JazzHRBootstrapParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tenants: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        attributes = {key.casefold(): value or "" for key, value in attrs}
        tenant = _widget_tenant(attributes.get("src", ""))
        if tenant is not None:
            self.tenants.append(tenant)


def _parsed_tenant_url(url: str):
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
        or not host.endswith(_HOST_SUFFIX)
    ):
        return None
    tenant = host[: -len(_HOST_SUFFIX)]
    if not _TENANT_PATTERN.fullmatch(tenant):
        return None
    return parsed, tenant


def _widget_tenant(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != _WIDGET_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        return None
    match = _WIDGET_PATH.fullmatch(unquote(parsed.path))
    return match.group(1).casefold() if match else None


def _job_board(tenant: str, variant: str) -> JobBoard:
    path = "/apply/" if variant == "current" else "/apply/jobs/"
    return JobBoard(
        url=f"https://{tenant}{_HOST_SUFFIX}{path}",
        provider="jazzhr",
        identifier=tenant,
    )


def _board_identity(board: JobBoard) -> tuple[str, str] | None:
    if board.provider != "jazzhr" or not board.identifier:
        return None
    tenant = board.identifier.casefold()
    if not _TENANT_PATTERN.fullmatch(tenant):
        return None
    for variant in ("current", "legacy"):
        if board.url == _job_board(tenant, variant).url:
            return tenant, variant
    return None


def _is_exact_tenant_board_url(url: str, tenant: str, variant: str) -> bool:
    parsed_tenant = _parsed_tenant_url(url)
    if parsed_tenant is None:
        return False
    parsed, actual_tenant = parsed_tenant
    expected_path = "/apply" if variant == "current" else "/apply/jobs"
    return (
        actual_tenant == tenant
        and unquote(parsed.path).rstrip("/") == expected_path
        and not parsed.query
        and not parsed.fragment
    )


def _detail_identity(href: str, tenant: str, base_url: str) -> tuple[str, str] | None:
    try:
        url = urljoin(base_url, href)
    except (TypeError, ValueError):
        return None
    parsed_tenant = _parsed_tenant_url(url)
    if parsed_tenant is None:
        return None
    parsed, actual_tenant = parsed_tenant
    path = unquote(parsed.path).rstrip("/")
    match = _CURRENT_DETAIL_PATH.fullmatch(path) or _LEGACY_DETAIL_PATH.fullmatch(path)
    if actual_tenant != tenant or match is None:
        return None
    return f"https://{tenant}{_HOST_SUFFIX}{path}", match.group(1)


def _normalized_title(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if value else ""


def _unsupported(board: JobBoard, error: str, rejected_url: str | None = None) -> AdapterResult:
    trace = {"adapter": "jazzhr", "error": error}
    if rejected_url:
        trace["rejected_final_url"] = rejected_url
    return AdapterResult(
        provider="jazzhr",
        board=board,
        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
        trace=trace,
    )


def _invalid_response(board: JobBoard, board_url: str, error: str) -> AdapterResult:
    return AdapterResult(
        provider="jazzhr",
        board=board,
        reason_code="INVALID_STRUCTURED_DATA",
        trace={"adapter": "jazzhr", "board_urls": [board_url], "error": error},
    )


ADAPTER = JazzHRAdapter()
