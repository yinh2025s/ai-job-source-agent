from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from urllib.parse import urlencode, urlparse

from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobQuery


_MAX_HTML_CHARS = 2_000_000
_TALEMETRY_ASSET = "/pack/talemetry_careersites/"
_SEARCH_PATH = "/search/jobs.json"
_PATH_CONFIG = re.compile(r"\b(?:CareerSite\.Path\.configure|window\.csns\.paths)\b")
_TALEMETRY_GLOBAL = re.compile(r"\bwindow\.talemetry\b")
_SEARCH_ROUTE = re.compile(r"[\"']search_jobs_json[\"']\s*:\s*[\"']/search/jobs\.json[\"']")
_JOB_ROUTE_CONFIG = re.compile(r"[\"']job[\"']\s*:\s*[\"']/jobs/:id[\"']")
_CAREER_SITE_ID = re.compile(
    r"[\"']careerSite[\"']\s*:\s*\{[^{}]*[\"']id[\"']\s*:\s*[\"']([^\"']+)[\"']",
    re.DOTALL,
)
_CF_CHALLENGE_MARKERS = (
    "cf-chl-",
    "cloudflare ray id",
    "challenges.cloudflare.com",
    "just a moment...",
    "cf-mitigated",
)


class TalemetryAdapter:
    name = "talemetry"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        # Career Sites tenants use customer-owned hosts and require page evidence.
        return False

    def identify_board(self, url: str) -> JobBoard | None:
        return None

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        parsed = _safe_first_party_url(page.final_url or page.url)
        if parsed is None:
            return None
        fingerprint = _fingerprint(page.html)
        if fingerprint is None:
            return None
        host = (parsed.hostname or "").casefold()
        career_site_id = fingerprint[0]
        return JobBoard(
            url=f"https://{_url_host(host)}/",
            provider=self.name,
            identifier=_identifier(host, career_site_id),
            replay_safe=True,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identity = _board_identity(board)
        if identity is None:
            return _unsupported(board, "invalid Talemetry Career Sites board")
        host, _career_site_id = identity
        endpoint = f"https://{_url_host(host)}{_SEARCH_PATH}"
        request_url = endpoint
        if query.title:
            request_url = f"{endpoint}?{urlencode({'q': query.title})}"

        try:
            response = fetcher.fetch(request_url, headers={"Accept": "application/json"})
        except FetchError as error:
            return _fetch_failure(board, request_url, error)
        except (OSError, TimeoutError) as error:
            return _fetch_failure(board, request_url, error)

        if not _is_expected_response(response.final_url or response.url, host, request_url):
            return _unsupported(
                board,
                "Talemetry search response URL did not match the requested same-origin endpoint",
                request_url=request_url,
                response_source=response.source,
            )
        if _is_cloudflare_challenge(response.html):
            return _incomplete(
                board,
                request_url,
                "BOT_PROTECTION",
                "Cloudflare challenge response",
                response_source=response.source,
            )

        try:
            json.loads(response.html)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        # No successful inventory schema has been observed and frozen.
        return _unsupported(
            board,
            "unrecognized Talemetry search response schema",
            request_url=request_url,
            response_source=response.source,
        )


class _ScriptEvidenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.script_sources: list[str] = []
        self.script_bodies: list[str] = []
        self._in_script = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        self._in_script = True
        self._parts = []
        source = dict(attrs).get("src")
        if source:
            self.script_sources.append(source)

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script" or not self._in_script:
            return
        self.script_bodies.append("".join(self._parts))
        self._in_script = False
        self._parts = []


def _fingerprint(html: str) -> tuple[str | None] | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    parser = _ScriptEvidenceParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    script = "\n".join(parser.script_bodies)
    has_talemetry = any(
        _TALEMETRY_ASSET in source.casefold() for source in parser.script_sources
    )
    has_talemetry = has_talemetry or bool(_TALEMETRY_GLOBAL.search(script))
    if not (
        has_talemetry
        and _PATH_CONFIG.search(script)
        and _SEARCH_ROUTE.search(script)
        and _JOB_ROUTE_CONFIG.search(script)
    ):
        return None
    matches = list(
        dict.fromkeys(
            match.strip()
            for match in _CAREER_SITE_ID.findall(script)
            if match.strip()
        )
    )
    if len(matches) > 1:
        return None
    return (matches[0] if matches else None,)


def _safe_first_party_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    return parsed


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _identifier(host: str, career_site_id: str | None) -> str:
    value = {"host": host}
    if career_site_id:
        value["career_site_id"] = career_site_id
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _board_identity(board: JobBoard) -> tuple[str, str | None] | None:
    if board.provider != "talemetry" or not board.identifier:
        return None
    parsed = _safe_first_party_url(board.url)
    if parsed is None or parsed.path != "/" or parsed.query or parsed.fragment:
        return None
    try:
        value = json.loads(board.identifier)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(value, dict) or not set(value).issubset({"host", "career_site_id"}):
        return None
    if set(value) not in ({"host"}, {"host", "career_site_id"}):
        return None
    host = (parsed.hostname or "").casefold()
    career_site_id = value.get("career_site_id")
    if value.get("host") != host or (
        career_site_id is not None
        and (
            not isinstance(career_site_id, str)
            or not career_site_id.strip()
            or career_site_id != career_site_id.strip()
        )
    ):
        return None
    return host, career_site_id


def _is_expected_response(url: str, host: str, request_url: str) -> bool:
    parsed = _safe_first_party_url(url)
    expected = urlparse(request_url)
    return (
        parsed is not None
        and (parsed.hostname or "").casefold() == host
        and parsed.path == _SEARCH_PATH
        and parsed.query == expected.query
        and not parsed.fragment
    )


def _is_cloudflare_challenge(body: str) -> bool:
    text = (body or "").casefold()
    return any(marker in text for marker in _CF_CHALLENGE_MARKERS)


def _fetch_failure(
    board: JobBoard,
    request_url: str,
    error: Exception,
) -> AdapterResult:
    reason_code = classify_fetch_error(str(error))
    if reason_code == "FETCH_FAILED":
        reason_code = "PROVIDER_FETCH_FAILED"
    return _incomplete(
        board,
        request_url,
        reason_code,
        str(error),
        retryable=reason_spec(reason_code).retryable,
    )


def _incomplete(
    board: JobBoard,
    request_url: str,
    reason_code: str,
    error: str,
    *,
    retryable: bool = False,
    response_source: str | None = None,
) -> AdapterResult:
    trace = {
        "adapter": "talemetry",
        "variant": "career_sites_detection_only",
        "api_urls": [request_url],
        "error": error,
        "inventory_scope": "unknown",
        "inventory_complete": False,
    }
    if response_source is not None:
        trace["response_source"] = response_source
    return AdapterResult(
        provider="talemetry",
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
    request_url: str | None = None,
    response_source: str | None = None,
) -> AdapterResult:
    trace = {
        "adapter": "talemetry",
        "variant": "career_sites_detection_only",
        "api_urls": [request_url] if request_url else [],
        "error": error,
        "inventory_scope": "unknown",
        "inventory_complete": False,
    }
    if response_source is not None:
        trace["response_source"] = response_source
    return AdapterResult(
        provider="talemetry",
        board=board,
        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
        retryable=False,
        inventory_scope="unknown",
        inventory_complete=False,
        trace=trace,
    )


ADAPTER = TalemetryAdapter()
