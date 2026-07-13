from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST_SUFFIX = ".breezy.hr"
_TENANT_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_DETAIL_PATH = re.compile(r"^/p/([a-z0-9]{8,64})-[a-z0-9-]+/?$", flags=re.I)


class BreezyAdapter:
    name = "breezy"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        parsed = _parsed_public_url(url)
        if parsed is None:
            return False
        path = parsed[0].path.rstrip("/")
        return not path or _DETAIL_PATH.fullmatch(path) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        parsed = _parsed_public_url(url)
        if parsed is None or not self.recognizes(url):
            return None
        _url, tenant = parsed
        return JobBoard(
            url=f"https://{tenant}{_HOST_SUFFIX}/",
            provider=self.name,
            identifier=tenant,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        tenant = _board_tenant(board)
        if tenant is None:
            return _unsupported(board, "invalid Breezy board tenant")
        try:
            page = fetcher.fetch(board.url)
        except (FetchError, OSError, TimeoutError) as error:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                trace={"adapter": self.name, "board_urls": [board.url], "error": str(error)},
            )
        final = _parsed_public_url(page.final_url or page.url)
        if final is None or final[1] != tenant or final[0].path.rstrip("/"):
            return _unsupported(board, "Breezy board redirected outside the tenant")

        parser = _BreezyJobsParser(tenant)
        try:
            parser.feed(page.html or "")
        except (TypeError, ValueError):
            return _invalid(board, "malformed Breezy HTML")
        if not parser.has_fingerprint:
            return _invalid(board, "missing Breezy public-board fingerprint")
        candidates = [
            JobCandidate(
                title=title,
                url=url,
                provider=self.name,
                location=location,
                raw={"job_id": _DETAIL_PATH.fullmatch(urlparse(url).path).group(1)},
            )
            for title, url, location in parser.jobs
        ]
        target = _normalized(query.title)
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            trace={
                "adapter": self.name,
                "variant": "public_portal_html",
                "board_urls": [board.url],
                "response_source": page.source,
                "candidate_count": len(candidates),
                "exact_title_found": bool(target and any(_normalized(job.title) == target for job in candidates)),
                "inventory_scope": "full",
            },
        )


class _BreezyJobsParser(HTMLParser):
    def __init__(self, tenant: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tenant = tenant
        self.has_fingerprint = False
        self.jobs: list[tuple[str, str, str | None]] = []
        self._href: str | None = None
        self._title_parts: list[str] = []
        self._location_parts: list[str] = []
        self._in_title = False
        self._in_location = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if "breezy-portal" in classes or "bzy-footer" in classes:
            self.has_fingerprint = True
        if tag.casefold() == "a" and self._href is None:
            href = attributes.get("href", "")
            detail = _detail_url(href, self.tenant)
            if detail:
                self._href = detail
                self._title_parts = []
                self._location_parts = []
        if self._href and tag.casefold() == "h2":
            self._in_title = True
        if self._href and tag.casefold() == "li" and "location" in classes:
            self._in_location = True

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._in_location:
            self._location_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "h2":
            self._in_title = False
        if tag == "li" and self._in_location:
            self._in_location = False
        if tag != "a" or self._href is None:
            return
        title = " ".join("".join(self._title_parts).split())
        location = " ".join("".join(self._location_parts).split()) or None
        if title and not any(existing[1] == self._href for existing in self.jobs):
            self.jobs.append((title, self._href, location))
        self._href = None


def _parsed_public_url(url: str):
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
    tenant = (board.identifier or "").casefold()
    expected = f"https://{tenant}{_HOST_SUFFIX}/"
    return tenant if board.provider == "breezy" and _TENANT_PATTERN.fullmatch(tenant) and board.url == expected else None


def _detail_url(href: str, tenant: str) -> str | None:
    url = urljoin(f"https://{tenant}{_HOST_SUFFIX}/", href)
    parsed = _parsed_public_url(url)
    if parsed is None or parsed[1] != tenant or _DETAIL_PATH.fullmatch(parsed[0].path) is None:
        return None
    return f"https://{tenant}{_HOST_SUFFIX}{parsed[0].path.rstrip('/')}"


def _normalized(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _unsupported(board: JobBoard, error: str) -> AdapterResult:
    return AdapterResult(provider="breezy", board=board, reason_code="PROVIDER_VARIANT_UNSUPPORTED", trace={"adapter": "breezy", "error": error})


def _invalid(board: JobBoard, error: str) -> AdapterResult:
    return AdapterResult(provider="breezy", board=board, reason_code="INVALID_STRUCTURED_DATA", trace={"adapter": "breezy", "error": error})


ADAPTER = BreezyAdapter()
