from __future__ import annotations

from html.parser import HTMLParser
import json
from urllib.parse import urlencode, urlparse, urlunparse

from ..reasons import classify_fetch_error, reason_spec
from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobQuery


_WIDGET_SCRIPT_URL = "https://jobsapi.ceipal.com/APISource/widget.js"
_CAREER_API_URL = "https://careerapi.ceipal.com/careerPortalWidget/"
_REFERER_HOST = "https://jobsapi.ceipal.com/"
_MAX_HTML_CHARS = 2_000_000
_INVENTORY_SCOPE = "unknown"


class CeipalAdapter:
    name = "ceipal"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        # CEIPAL widgets live on first-party pages and require DOM evidence.
        return False

    def identify_board(self, url: str) -> JobBoard | None:
        return None

    def identify_board_from_page(self, page: Page) -> JobBoard | None:
        page_url = page.final_url or page.url
        parsed = _safe_first_party_url(page_url)
        if parsed is None:
            return None
        tenant = _widget_tenant(page.html)
        if tenant is None:
            return None
        api_key, career_portal_id = tenant
        origin = _origin(parsed)
        return JobBoard(
            url=urlunparse(parsed._replace(query="", fragment="")),
            provider=self.name,
            identifier=_identifier(origin, api_key, career_portal_id),
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        identity = _board_identity(board)
        if identity is None:
            return _unsupported(board, "invalid CEIPAL board")
        api_key, career_portal_id = identity
        request_url = _career_api_url(api_key, career_portal_id)
        try:
            response = fetcher.fetch(
                request_url,
                headers={
                    "Accept": "application/json",
                    "X-Referer-Host": _REFERER_HOST,
                },
            )
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, error)

        response_url = response.final_url or response.url
        if response_url != request_url:
            return _unsupported(
                board,
                "CEIPAL API response URL did not match the frozen widget endpoint",
                response_source=response.source,
            )
        try:
            payload = json.loads(response.html)
        except (json.JSONDecodeError, TypeError, ValueError):
            return _unsupported(
                board,
                "unrecognized CEIPAL response schema",
                response_source=response.source,
            )

        if (
            isinstance(payload, dict)
            and payload.get("status") == 400
            and payload.get("message") == "Bot access is not allowed"
        ):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="BOT_PROTECTION",
                retryable=False,
                inventory_scope=_INVENTORY_SCOPE,
                inventory_complete=False,
                trace={
                    "adapter": self.name,
                    "variant": "career_portal_widget_detection_only",
                    "endpoint": _CAREER_API_URL,
                    "response_source": response.source,
                    "http_status": 400,
                    "inventory_scope": _INVENTORY_SCOPE,
                    "inventory_complete": False,
                },
            )

        # No successful CEIPAL inventory schema has been frozen and verified yet.
        return _unsupported(
            board,
            "unrecognized CEIPAL response schema",
            response_source=response.source,
        )


class _WidgetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tenants: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        values = {name.casefold(): value for name, value in attrs}
        if values.get("src") != _WIDGET_SCRIPT_URL:
            return
        api_key = (values.get("data-ceipal-api-key") or "").strip()
        career_portal_id = (values.get("data-ceipal-career-portal-id") or "").strip()
        if api_key and career_portal_id:
            self.tenants.append((api_key, career_portal_id))


def _widget_tenant(html: str) -> tuple[str, str] | None:
    if not isinstance(html, str) or len(html) > _MAX_HTML_CHARS:
        return None
    parser = _WidgetParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return None
    unique = list(dict.fromkeys(parser.tenants))
    return unique[0] if len(unique) == 1 else None


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


def _origin(parsed) -> str:
    host = (parsed.hostname or "").casefold()
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}"


def _identifier(origin: str, api_key: str, career_portal_id: str) -> str:
    return json.dumps(
        {
            "api_key": api_key,
            "career_portal_id": career_portal_id,
            "origin": origin,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _board_identity(board: JobBoard) -> tuple[str, str] | None:
    if board.provider != "ceipal" or not board.identifier:
        return None
    parsed = _safe_first_party_url(board.url)
    if parsed is None or parsed.query or parsed.fragment:
        return None
    try:
        value = json.loads(board.identifier)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != {"api_key", "career_portal_id", "origin"}:
        return None
    api_key = value.get("api_key")
    career_portal_id = value.get("career_portal_id")
    if (
        not isinstance(api_key, str)
        or not api_key.strip()
        or api_key != api_key.strip()
        or not isinstance(career_portal_id, str)
        or not career_portal_id.strip()
        or career_portal_id != career_portal_id.strip()
        or value.get("origin") != _origin(parsed)
    ):
        return None
    return api_key, career_portal_id


def _career_api_url(api_key: str, career_portal_id: str) -> str:
    query = urlencode(
        [
            ("themeid", ""),
            ("bgcolor", ""),
            ("job_id", ""),
            ("apikey", api_key),
            ("cp_id", career_portal_id),
        ]
    )
    return f"{_CAREER_API_URL}?{query}"


def _unsupported(
    board: JobBoard,
    error: str,
    *,
    response_source: str | None = None,
) -> AdapterResult:
    trace = {
        "adapter": "ceipal",
        "variant": "career_portal_widget_detection_only",
        "endpoint": _CAREER_API_URL,
        "error": error,
        "inventory_scope": _INVENTORY_SCOPE,
        "inventory_complete": False,
    }
    if response_source is not None:
        trace["response_source"] = response_source
    return AdapterResult(
        provider="ceipal",
        board=board,
        reason_code="PROVIDER_VARIANT_UNSUPPORTED",
        retryable=False,
        inventory_scope=_INVENTORY_SCOPE,
        inventory_complete=False,
        trace=trace,
    )


def _fetch_failure(board: JobBoard, error: Exception) -> AdapterResult:
    detail = str(error)
    reason_code = classify_fetch_error(detail)
    if reason_code == "FETCH_FAILED":
        reason_code = "PROVIDER_FETCH_FAILED"
    return AdapterResult(
        provider="ceipal",
        board=board,
        reason_code=reason_code,
        retryable=reason_spec(reason_code).retryable,
        inventory_scope=_INVENTORY_SCOPE,
        inventory_complete=False,
        trace={
            "adapter": "ceipal",
            "variant": "career_portal_widget_detection_only",
            "endpoint": _CAREER_API_URL,
            "error": f"CEIPAL request failed: {reason_code}",
            "inventory_scope": _INVENTORY_SCOPE,
            "inventory_complete": False,
        },
    )


ADAPTER = CeipalAdapter()
