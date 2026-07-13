from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import unquote, urljoin, urlparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST_SUFFIX = ".applytojob.com"
_TENANT_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_DETAIL_PATH = re.compile(r"^/apply/jobs/details/([A-Za-z0-9_-]{4,64})/?$")


class JazzHRAdapter:
    name = "jazzhr"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        parsed_tenant = _parsed_tenant_url(url)
        if parsed_tenant is None:
            return False
        parsed, _tenant = parsed_tenant
        path = parsed.path.rstrip("/")
        return path == "/apply/jobs" or _DETAIL_PATH.fullmatch(path) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        parsed_tenant = _parsed_tenant_url(url)
        if parsed_tenant is None or not self.recognizes(url):
            return None
        _parsed, tenant = parsed_tenant
        return JobBoard(
            url=f"https://{tenant}{_HOST_SUFFIX}/apply/jobs/",
            provider=self.name,
            identifier=tenant,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        tenant = _board_tenant(board)
        if tenant is None:
            return _unsupported(board, "invalid JazzHR board tenant")

        board_url = f"https://{tenant}{_HOST_SUFFIX}/apply/jobs/"
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
        if not _is_tenant_board_url(final_url, tenant):
            return _unsupported(
                board,
                "JazzHR board redirected outside the tenant",
                final_url,
            )

        parser = _JazzHRJobsParser()
        try:
            parser.feed(page.html or "")
        except (TypeError, ValueError):
            return _invalid_response(board, board_url, "malformed JazzHR HTML")

        fingerprinted = parser.has_resumator_root and parser.has_jobs_form
        if not fingerprinted:
            return _invalid_response(board, board_url, "missing JazzHR public-board fingerprint")

        candidates: list[JobCandidate] = []
        seen: set[str] = set()
        rejected_links = 0
        for title, href in parser.job_links:
            detail_url = _detail_url(href, tenant)
            if detail_url is None:
                rejected_links += 1
                continue
            if detail_url in seen:
                continue
            seen.add(detail_url)
            candidates.append(
                JobCandidate(
                    title=title,
                    url=detail_url,
                    provider=self.name,
                    raw={"job_id": _DETAIL_PATH.fullmatch(urlparse(detail_url).path).group(1)},
                )
            )

        target = _normalized_title(query.title)
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            trace={
                "adapter": self.name,
                "variant": "public_jobs_html",
                "board_urls": [board_url],
                "response_source": page.source,
                "candidate_count": len(candidates),
                "public_link_count": len(parser.job_links),
                "rejected_link_count": rejected_links,
                "exact_title_found": bool(
                    target
                    and any(_normalized_title(candidate.title) == target for candidate in candidates)
                ),
                "inventory_scope": "full",
            },
        )


class _JazzHRJobsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_resumator_root = False
        self.has_jobs_form = False
        self.job_links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        element_id = attributes.get("id", "")
        if element_id in {"resumator_main_wrapper", "resumator_container_body"}:
            self.has_resumator_root = True
        if tag.casefold() == "form" and attributes.get("action", "").rstrip("/") == "/apply/jobs":
            self.has_jobs_form = True
        classes = set(attributes.get("class", "").split())
        if tag.casefold() == "a" and "job_title_link" in classes:
            self._href = attributes.get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self._href is None:
            return
        title = " ".join("".join(self._text).split())
        if title:
            self.job_links.append((title, self._href))
        self._href = None
        self._text = []


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


def _board_tenant(board: JobBoard) -> str | None:
    if board.provider != "jazzhr" or not board.identifier:
        return None
    tenant = board.identifier.casefold()
    if not _TENANT_PATTERN.fullmatch(tenant):
        return None
    expected = f"https://{tenant}{_HOST_SUFFIX}/apply/jobs/"
    return tenant if board.url == expected else None


def _is_tenant_board_url(url: str, tenant: str) -> bool:
    parsed_tenant = _parsed_tenant_url(url)
    if parsed_tenant is None:
        return False
    parsed, actual_tenant = parsed_tenant
    return actual_tenant == tenant and parsed.path.rstrip("/") == "/apply/jobs"


def _detail_url(href: str, tenant: str) -> str | None:
    base = f"https://{tenant}{_HOST_SUFFIX}/apply/jobs/"
    try:
        url = urljoin(base, href)
    except (TypeError, ValueError):
        return None
    parsed_tenant = _parsed_tenant_url(url)
    if parsed_tenant is None:
        return None
    parsed, actual_tenant = parsed_tenant
    path = unquote(parsed.path).rstrip("/")
    if actual_tenant != tenant or _DETAIL_PATH.fullmatch(path) is None:
        return None
    return f"https://{tenant}{_HOST_SUFFIX}{path}"


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
