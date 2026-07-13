from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import quote, urlencode, urljoin, urlparse

from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_PORTAL_ID = re.compile(r"^[0-9]{1,12}$")
_DETAIL_PATH = re.compile(
    r"^/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)/JobDetail/[^/]+/([0-9]{1,20})/?$",
    re.IGNORECASE,
)


class AvatureAdapter:
    name = "avature"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        # Avature is normally hosted on a customer-owned domain. A URL alone is
        # not sufficient evidence; discovery uses identify_board_from_page.
        return False

    def identify_board(self, url: str) -> JobBoard | None:
        return None

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        config = _portal_config(page)
        if config is None:
            return None
        host, language, portal = config
        return JobBoard(
            url=_search_url(host, language, portal),
            provider=self.name,
            identifier=f"{host}|{language}|{portal}",
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        config = _board_config(board)
        if config is None:
            return _unsupported(board, "invalid Avature portal identifier")
        host, language, portal = config
        params = {"sort": "relevancy"}
        if query.title:
            params["search"] = query.title.strip()
        search_url = f"{_search_url(host, language, portal)}?{urlencode(params)}"

        try:
            page = fetcher.fetch(search_url)
        except (FetchError, OSError, TimeoutError) as error:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_FETCH_FAILED",
                retryable=True,
                trace={"adapter": self.name, "board_urls": [board.url], "api_urls": [search_url], "error": str(error)},
            )

        final_url = page.final_url or page.url
        if not _is_portal_search_url(final_url, host, language, portal):
            return _unsupported(board, "Avature search redirected outside the portal", final_url)
        if _portal_config(page) != config:
            return _invalid(board, search_url, "missing or mismatched Avature portal fingerprint")

        parser = _AvatureParser()
        try:
            parser.feed(page.html or "")
        except (TypeError, ValueError):
            return _invalid(board, search_url, "malformed Avature search HTML")

        candidates: list[JobCandidate] = []
        seen: set[str] = set()
        rejected_links = 0
        for title, href in parser.links:
            detail = _detail_url(href, host, language, portal)
            if detail is None:
                rejected_links += 1
                continue
            if detail in seen:
                continue
            seen.add(detail)
            job_id = _DETAIL_PATH.fullmatch(urlparse(detail).path).group(3)
            candidates.append(
                JobCandidate(
                    title=title,
                    url=detail,
                    provider=self.name,
                    raw={"job_id": job_id},
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
                "variant": "customer_portal_search_html",
                "board_urls": [board.url],
                "api_urls": [search_url],
                "response_source": page.source,
                "candidate_count": len(candidates),
                "public_link_count": len(parser.links),
                "rejected_link_count": rejected_links,
                "exact_title_found": bool(
                    target
                    and any(_normalized_title(candidate.title) == target for candidate in candidates)
                ),
                "inventory_scope": "title_filtered" if query.title else "first_page",
            },
        )


class _AvatureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        href = attributes.get("href", "")
        if tag.casefold() == "a" and "/jobdetail/" in href.casefold():
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self._href is None:
            return
        title = " ".join("".join(self._text).split())
        if title:
            self.links.append((title, self._href))
        self._href = None
        self._text = []


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "meta":
            return
        attributes = {key.casefold(): value or "" for key, value in attrs}
        name = attributes.get("name", "").casefold()
        if name.startswith("avature.portal."):
            self.meta[name] = attributes.get("content", "")


def _portal_config(page: Page) -> tuple[str, str, str] | None:
    parsed = _safe_https_url(page.final_url or page.url)
    if parsed is None:
        return None
    parser = _MetaParser()
    try:
        parser.feed(page.html or "")
    except (TypeError, ValueError):
        return None
    portal_id = parser.meta.get("avature.portal.id", "")
    language = parser.meta.get("avature.portal.lang", "")
    portal = parser.meta.get("avature.portal.urlpath", "")
    if (
        not _PORTAL_ID.fullmatch(portal_id)
        or not _SEGMENT.fullmatch(language)
        or not _SEGMENT.fullmatch(portal)
    ):
        return None
    parts = [part for part in parsed.path.split("/") if part]
    path_matches = (
        len(parts) >= 2
        and parts[0].casefold() == language.casefold()
        and parts[1].casefold() == portal.casefold()
    )
    host = (parsed.hostname or "").casefold()
    if not path_matches and not _html_confirms_search_route(page.html or "", host, language, portal):
        return None
    return host, language, portal


def _board_config(board: JobBoard) -> tuple[str, str, str] | None:
    if board.provider != "avature" or not board.identifier:
        return None
    parts = board.identifier.split("|")
    if len(parts) != 3:
        return None
    host, language, portal = parts
    if _safe_https_url(f"https://{host}/") is None:
        return None
    if not _SEGMENT.fullmatch(language) or not _SEGMENT.fullmatch(portal):
        return None
    expected = _search_url(host, language, portal)
    return (host, language, portal) if board.url == expected else None


def _search_url(host: str, language: str, portal: str) -> str:
    return f"https://{host}/{quote(language, safe='-_')}/{quote(portal, safe='-_')}/SearchJobs"


def _html_confirms_search_route(html: str, host: str, language: str, portal: str) -> bool:
    expected = re.escape(_search_url(host, language, portal))
    return re.search(
        rf"(?:searchJobsPage\s*=\s*[\"']|(?:action|href)\s*=\s*[\"']){expected}(?:[?\"'])",
        html[:300000],
        flags=re.IGNORECASE,
    ) is not None


def _is_portal_search_url(url: str, host: str, language: str, portal: str) -> bool:
    parsed = _safe_https_url(url)
    if parsed is None or (parsed.hostname or "").casefold() != host:
        return False
    expected = f"/{language}/{portal}/SearchJobs".casefold()
    return parsed.path.rstrip("/").casefold() == expected.casefold()


def _detail_url(href: str, host: str, language: str, portal: str) -> str | None:
    parsed = _safe_https_url(urljoin(f"https://{host}/{language}/{portal}/", href))
    if parsed is None or (parsed.hostname or "").casefold() != host:
        return None
    match = _DETAIL_PATH.fullmatch(parsed.path)
    if (
        match is None
        or match.group(1).casefold() != language.casefold()
        or match.group(2).casefold() != portal.casefold()
    ):
        return None
    return f"https://{host}{parsed.path.rstrip('/')}"


def _safe_https_url(url: str):
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


def _normalized_title(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold())) if value else ""


def _unsupported(board: JobBoard, error: str, rejected_url: str | None = None) -> AdapterResult:
    trace = {"adapter": "avature", "error": error}
    if rejected_url:
        trace["rejected_final_url"] = rejected_url
    return AdapterResult(
        provider="avature",
        board=board,
        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
        trace=trace,
    )


def _invalid(board: JobBoard, search_url: str, error: str) -> AdapterResult:
    return AdapterResult(
        provider="avature",
        board=board,
        reason_code="INVALID_STRUCTURED_DATA",
        trace={"adapter": "avature", "api_urls": [search_url], "error": error},
    )


ADAPTER = AvatureAdapter()
