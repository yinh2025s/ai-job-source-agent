from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

from ..web import FetchError
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOSTS = {"metacareers.com", "www.metacareers.com"}
_CANONICAL_HOST = "www.metacareers.com"
_BOARD_PATH = "/jobsearch/"
_DETAIL_PATH = re.compile(r"^/profile/job_details/(?P<job_id>[0-9]+)/?$")
_LIST_PATH = re.compile(r"^/(?:jobs|jobsearch)/?$")


class MetaCareersAdapter:
    name = "meta_careers"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        parsed = _safe_meta_url(url)
        if parsed is None:
            return False
        return (
            _LIST_PATH.fullmatch(parsed.path) is not None
            or _DETAIL_PATH.fullmatch(parsed.path) is not None
        )

    def identify_board(self, url: str) -> JobBoard | None:
        if not self.recognizes(url):
            return None
        return JobBoard(
            url=f"https://{_CANONICAL_HOST}{_BOARD_PATH}",
            provider=self.name,
            identifier="meta",
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not _is_canonical_board(board):
            return _result(
                board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "invalid Meta Careers board"},
            )

        search_url = board.url
        if query.title:
            search_url = f"{board.url}?{urlencode({'q': query.title})}"
        responses: list[dict] = []
        for attempt in range(2):
            try:
                page = fetcher.fetch(search_url)
            except (FetchError, OSError, TimeoutError) as error:
                return _result(
                    board,
                    reason_code="PROVIDER_FETCH_FAILED",
                    retryable=True,
                    trace={
                        "adapter": self.name,
                        "board_urls": [search_url],
                        "attempt_count": attempt + 1,
                        "responses": responses,
                        "error_type": type(error).__name__,
                    },
                )

            final_url = page.final_url or page.url
            if not _is_canonical_search_url(final_url):
                return _result(
                    board,
                    reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                    trace={
                        "adapter": self.name,
                        "board_urls": [search_url],
                        "attempt_count": attempt + 1,
                        "error": "Meta Careers search response URL rejected",
                    },
                )

            try:
                candidates, public_link_count, rejected_link_count = _extract_candidates(
                    page.html or "",
                    final_url,
                )
            except (TypeError, ValueError):
                return _unsupported_response(
                    board,
                    search_url,
                    "malformed rendered HTML",
                    attempt_count=attempt + 1,
                )
            responses.append(
                {
                    "attempt": attempt + 1,
                    "response_source": page.source,
                    "public_link_count": public_link_count,
                    "rejected_link_count": rejected_link_count,
                }
            )

            if candidates:
                return _result(
                    board,
                    candidates=candidates,
                    inventory_scope="visible_page",
                    inventory_complete=False,
                    trace={
                        "adapter": self.name,
                        "variant": "anonymous_rendered_search",
                        "board_urls": [final_url],
                        "response_source": page.source,
                        "attempt_count": attempt + 1,
                        "responses": responses,
                        "candidate_count": len(candidates),
                        "public_link_count": public_link_count,
                        "rejected_link_count": rejected_link_count,
                        "inventory_scope": "visible_page",
                        "inventory_complete": False,
                    },
                )
            if "browser" not in page.source.casefold():
                break

        return _unsupported_response(
            board,
            search_url,
            "missing rendered Meta job detail evidence",
            attempt_count=len(responses),
            responses=responses,
            public_link_count=sum(item["public_link_count"] for item in responses),
            rejected_link_count=sum(item["rejected_link_count"] for item in responses),
        )


class _MetaJobsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.job_links: list[tuple[str, str, str]] = []
        self._href: str | None = None
        self._depth = 0
        self._heading_depth = 0
        self._title_text: list[str] = []
        self._associated_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._href is not None:
            self._depth += 1
            if tag.casefold() in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self._heading_depth += 1
            return
        if tag.casefold() != "a":
            return
        attributes = {key.casefold(): value or "" for key, value in attrs}
        self._href = attributes.get("href")
        self._depth = 0
        self._heading_depth = 0
        self._title_text = []
        self._associated_text = []

    def handle_data(self, data: str) -> None:
        if self._href is None:
            return
        if self._depth == 0 or self._heading_depth:
            self._title_text.append(data)
        else:
            self._associated_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._href is None:
            return
        if tag.casefold() == "a" and self._depth == 0:
            self.job_links.append(
                (
                    self._href,
                    _clean_text("".join(self._title_text)),
                    _clean_text("".join(self._associated_text)),
                )
            )
            self._href = None
            self._heading_depth = 0
            self._title_text = []
            self._associated_text = []
            return
        if tag.casefold() in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_depth = max(0, self._heading_depth - 1)
        if self._depth > 0:
            self._depth -= 1


def _extract_candidates(
    html: str,
    base_url: str,
) -> tuple[list[JobCandidate], int, int]:
    parser = _MetaJobsParser()
    parser.feed(html)
    candidates: list[JobCandidate] = []
    seen: set[str] = set()
    rejected_link_count = 0
    for href, title, location in parser.job_links:
        detail = _canonical_detail_url(href, base_url)
        if detail is None or not title:
            rejected_link_count += 1
            continue
        detail_url, job_id = detail
        if detail_url in seen:
            continue
        seen.add(detail_url)
        candidates.append(
            JobCandidate(
                title=title,
                url=detail_url,
                provider="meta_careers",
                location=location or None,
                raw={"job_id": job_id},
            )
        )
    return candidates, len(parser.job_links), rejected_link_count


def _safe_meta_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (AttributeError, TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or host not in _HOSTS
    ):
        return None
    return parsed


def _is_canonical_board(board: JobBoard) -> bool:
    return (
        board.provider == "meta_careers"
        and board.identifier == "meta"
        and board.url == f"https://{_CANONICAL_HOST}{_BOARD_PATH}"
    )


def _is_canonical_search_url(url: str) -> bool:
    parsed = _safe_meta_url(url)
    return (
        parsed is not None
        and (parsed.hostname or "").casefold() == _CANONICAL_HOST
        and parsed.path == _BOARD_PATH
    )


def _canonical_detail_url(href: str, base_url: str) -> tuple[str, str] | None:
    try:
        resolved = urljoin(base_url, href)
    except (AttributeError, TypeError, ValueError):
        return None
    parsed = _safe_meta_url(resolved)
    if parsed is None:
        return None
    match = _DETAIL_PATH.fullmatch(parsed.path)
    if match is None:
        return None
    job_id = match.group("job_id")
    path = f"/profile/job_details/{job_id}"
    return urlunparse(("https", _CANONICAL_HOST, path, "", "", "")), job_id


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _unsupported_response(
    board: JobBoard,
    board_url: str,
    error: str,
    **trace_values,
) -> AdapterResult:
    return _result(
        board,
        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
        trace={
            "adapter": "meta_careers",
            "board_urls": [board_url],
            "error": error,
            **trace_values,
        },
    )


def _result(
    board: JobBoard,
    *,
    candidates: list[JobCandidate] | None = None,
    reason_code: str | None = None,
    retryable: bool = False,
    trace: dict | None = None,
    inventory_scope: str = "unknown",
    inventory_complete: bool = False,
) -> AdapterResult:
    return AdapterResult(
        provider="meta_careers",
        board=board,
        candidates=candidates or [],
        reason_code=reason_code,
        retryable=retryable,
        inventory_scope=inventory_scope,
        inventory_complete=inventory_complete,
        trace=trace or {},
    )


ADAPTER = MetaCareersAdapter()
