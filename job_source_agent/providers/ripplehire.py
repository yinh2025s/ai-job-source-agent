from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qs, urlencode, urlparse
from xml.etree import ElementTree

from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST_SUFFIX = ".ripplehire.com"
_JOB_ID = re.compile(r"^[0-9]{1,20}$")
_PUBLIC_TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,256}$")
_MAX_PAGES = 5
_PAGE_SIZE = 50


class RippleHireAdapter:
    name = "ripplehire"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        parsed = _safe_url(url)
        if parsed is None or not _is_ripplehire_host(parsed.hostname or ""):
            return False
        path = parsed.path.rstrip("/")
        return path == "/ripplehire/careers" or path == "/candidate"

    def identify_board(self, url: str) -> JobBoard | None:
        parsed = _safe_url(url)
        if parsed is None or not self.recognizes(url):
            return None
        host = (parsed.hostname or "").casefold()
        return JobBoard(
            url=f"https://{host}/ripplehire/careers",
            provider=self.name,
            identifier=host,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not _is_valid_board(board):
            return _unsupported(board, "invalid RippleHire board")

        board_url = f"https://{board.identifier}/ripplehire/careers"
        try:
            shell = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, board_url, [], error)

        final_url = shell.final_url or shell.url
        final = _safe_url(final_url)
        if final is None or (final.hostname or "").casefold() != board.identifier:
            return _unsupported(board, "RippleHire portal redirected outside the tenant", final_url)

        token, source = _portal_config(shell)
        if token is None or source != "CAREERSITE":
            return _invalid_response(board, board_url, [], "missing public portal configuration")

        api_url = f"https://{board.identifier}/candidate/candidatejobsearch"
        candidates: list[JobCandidate] = []
        seen: set[str] = set()
        api_urls: list[str] = []
        pages_fetched = 0
        total_found: int | None = None
        target = _normalized_title(query.title)

        for page_index in range(_MAX_PAGES):
            payload = _search_payload(token, source, query, page_index)
            api_urls.append(api_url)
            try:
                response = fetcher.fetch(
                    api_url,
                    data=urlencode({"careerSiteUrlParams": json.dumps(payload), "lang": "en"}).encode(),
                    headers={
                        "Accept": "application/xml,text/xml,*/*",
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "Referer": board_url,
                    },
                )
            except (FetchError, OSError, TimeoutError) as error:
                if candidates:
                    break
                return _fetch_failure(board, board_url, api_urls, error)

            response_url = _safe_url(response.final_url or response.url)
            if (
                response_url is None
                or (response_url.hostname or "").casefold() != board.identifier
                or response_url.path.rstrip("/") != "/candidate/candidatejobsearch"
            ):
                return _unsupported(
                    board,
                    "RippleHire API redirected outside the tenant",
                    response.final_url or response.url,
                )

            try:
                root = ElementTree.fromstring(response.html)
            except (ElementTree.ParseError, TypeError):
                return _invalid_response(board, board_url, api_urls, "invalid RippleHire job response")
            if root.tag != "JobPageVO":
                return _invalid_response(board, board_url, api_urls, "unexpected RippleHire response root")

            records = root.findall("./jobVoList/jobVoList")
            pages_fetched += 1
            total_found = _nonnegative_int(root.findtext("totalJobCount"), total_found)
            for record in records:
                candidate = _candidate(record, board.identifier or "", token, source)
                if candidate is None or candidate.url in seen:
                    continue
                seen.add(candidate.url)
                candidates.append(candidate)

            if target and any(_normalized_title(item.title) == target for item in candidates):
                break
            if not records or len(records) < _PAGE_SIZE:
                break
            if total_found is not None and (page_index + 1) * _PAGE_SIZE >= total_found:
                break

        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            trace={
                "adapter": self.name,
                "variant": "public_candidate_session_api",
                "board_urls": [board_url],
                "api_urls": api_urls,
                "response_source": shell.source,
                "candidate_count": len(candidates),
                "pages_fetched": pages_fetched,
                "total_found": total_found,
                "inventory_scope": "title_filtered" if query.title else "full",
            },
        )


class _PortalConfigParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "input":
            return
        attributes = {key.casefold(): value or "" for key, value in attrs}
        key = attributes.get("id") or attributes.get("name")
        if key:
            self.values[key.casefold()] = attributes.get("value", "")


def _portal_config(page: Page) -> tuple[str | None, str | None]:
    parser = _PortalConfigParser()
    parser.feed(page.html)
    final_query = parse_qs(urlparse(page.final_url or page.url).query)
    html_token = parser.values.get("token")
    query_token = (final_query.get("token") or [None])[0]
    if html_token and query_token and html_token != query_token:
        return None, None
    token = html_token or query_token
    if token == "[REDACTED]" and page.source != "live":
        token = "snapshot-redacted-token"
    if not isinstance(token, str) or not _PUBLIC_TOKEN.fullmatch(token):
        return None, None
    source = parser.values.get("source") or (final_query.get("source") or [None])[0]
    return token, source


def _search_payload(token: str, source: str, query: JobQuery, page: int) -> dict:
    return {
        "page": page,
        "search": _provider_search_text(query.title),
        "token": token,
        "source": source,
        "pagesize": _PAGE_SIZE,
        "location": (query.location or "").strip(),
        "exp": "",
        "department": "",
        "businessunit": "",
        "bu": "",
        "state": "",
        "acc": "",
        "function": "",
    }


def _provider_search_text(title: str | None) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", title or "")
    return max(tokens, key=lambda token: (len(token), token.casefold())) if tokens else "*:*"


def _candidate(record, host: str, token: str, source: str) -> JobCandidate | None:
    title = (record.findtext("jobTitle") or "").strip()
    job_id = (record.findtext("jobSeq") or "").strip()
    if not title or not _JOB_ID.fullmatch(job_id):
        return None
    query = urlencode({"token": token, "source": source})
    location = (record.findtext("locations") or record.findtext("jobLocation") or "").strip()
    return JobCandidate(
        title=title,
        url=f"https://{host}/candidate/?{query}#detail/job/{job_id}",
        provider="ripplehire",
        location=location or None,
        raw={"job_id": job_id, "job_code": record.findtext("jobCode")},
    )


def _safe_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    return parsed


def _is_ripplehire_host(host: str) -> bool:
    normalized = host.casefold()
    return normalized.endswith(_HOST_SUFFIX) and normalized != _HOST_SUFFIX.lstrip(".")


def _is_valid_board(board: JobBoard) -> bool:
    return (
        board.provider == "ripplehire"
        and bool(board.identifier)
        and _is_ripplehire_host(board.identifier or "")
        and board.url == f"https://{board.identifier}/ripplehire/careers"
    )


def _nonnegative_int(value: str | None, fallback: int | None) -> int | None:
    try:
        parsed = int(value or "")
    except ValueError:
        return fallback
    return parsed if parsed >= 0 else fallback


def _normalized_title(title: str | None) -> str:
    return " ".join((title or "").casefold().split())


def _unsupported(board: JobBoard, error: str, rejected_url: str | None = None) -> AdapterResult:
    trace = {"adapter": "ripplehire", "error": error}
    if rejected_url:
        trace["rejected_final_url"] = rejected_url
    return AdapterResult(
        provider="ripplehire",
        board=board,
        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
        trace=trace,
    )


def _fetch_failure(board: JobBoard, board_url: str, api_urls: list[str], error) -> AdapterResult:
    return AdapterResult(
        provider="ripplehire",
        board=board,
        reason_code="PROVIDER_FETCH_FAILED",
        retryable=True,
        trace={
            "adapter": "ripplehire",
            "board_urls": [board_url],
            "api_urls": api_urls,
            "error": str(error),
        },
    )


def _invalid_response(
    board: JobBoard,
    board_url: str,
    api_urls: list[str],
    error: str,
) -> AdapterResult:
    return AdapterResult(
        provider="ripplehire",
        board=board,
        reason_code="INVALID_STRUCTURED_DATA",
        trace={
            "adapter": "ripplehire",
            "board_urls": [board_url],
            "api_urls": api_urls,
            "error": error,
        },
    )


ADAPTER = RippleHireAdapter()
