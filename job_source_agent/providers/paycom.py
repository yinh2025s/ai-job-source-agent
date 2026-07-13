from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, quote, urlparse

from ..web import FetchError, Page
from .base import AdapterResult, JobBoard, JobCandidate, JobQuery


_HOST_SUFFIX = "paycomonline.net"
_PORTAL_HOST = "www.paycomonline.net"
_CLIENT_KEY = re.compile(r"^[A-Fa-f0-9]{32}$")
_JOB_ID = re.compile(r"^[0-9]{1,20}$")
_MAX_PAGES = 5
_PAGE_SIZE = 20
_MAX_CONFIG_CHARS = 200_000


class PaycomAdapter:
    name = "paycom"
    supports_listing = True

    def recognizes(self, url: str) -> bool:
        return _client_key_from_url(url) is not None

    def identify_board(self, url: str) -> JobBoard | None:
        client_key = _client_key_from_url(url)
        if client_key is None:
            return None
        return JobBoard(
            url=_board_url(client_key),
            provider=self.name,
            identifier=client_key,
        )

    def list_jobs(self, fetcher, board: JobBoard, query: JobQuery) -> AdapterResult:
        if not _is_valid_board(board):
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={"adapter": self.name, "error": "invalid Paycom board"},
            )

        board_url = _board_url(board.identifier or "")
        try:
            portal_page = fetcher.fetch(board_url)
        except (FetchError, OSError, TimeoutError) as error:
            return _fetch_failure(board, [board_url], [], error)

        final_url = portal_page.final_url or portal_page.url
        if _client_key_from_url(final_url) != board.identifier:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                trace={
                    "adapter": self.name,
                    "board_urls": [board_url],
                    "error": "Paycom portal redirected outside the tenant",
                    "rejected_final_url": final_url,
                },
            )

        session_token, service_url = _portal_config(portal_page.html)
        if not session_token or not service_url:
            return AdapterResult(
                provider=self.name,
                board=board,
                reason_code="INVALID_STRUCTURED_DATA",
                trace={
                    "adapter": self.name,
                    "board_urls": [board_url],
                    "response_source": portal_page.source,
                    "error": "missing safe Paycom public portal configuration",
                },
            )

        api_url = service_url.rstrip("/") + "/api/ats/job-posting-previews/search"
        candidates: list[JobCandidate] = []
        seen: set[str] = set()
        api_urls: list[str] = []
        total_found: int | None = None
        pages_fetched = 0
        target = _normalized_title(query.title)
        inventory_scope = "title_filtered" if query.title else "full"
        inventory_complete = False
        for page_index in range(_MAX_PAGES):
            payload = _search_payload(query, page_index * _PAGE_SIZE)
            api_urls.append(api_url)
            try:
                response = fetcher.fetch(
                    api_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": session_token,
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Locale": "en",
                        "Referer": board_url,
                    },
                )
            except (FetchError, OSError, TimeoutError) as error:
                if candidates:
                    break
                return _fetch_failure(board, [board_url], api_urls, error)

            response_url = response.final_url or response.url
            if not _is_expected_api_url(response_url, service_url):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    candidates=candidates,
                    reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                    trace={
                        "adapter": self.name,
                        "board_urls": [board_url],
                        "api_urls": api_urls,
                        "error": "Paycom API redirected outside the configured service",
                        "rejected_final_url": response_url,
                    },
                )

            try:
                body = json.loads(response.html)
            except (json.JSONDecodeError, TypeError):
                return _invalid_response(board, board_url, api_urls)
            records = body.get("jobPostingPreviews") if isinstance(body, dict) else None
            if not isinstance(records, list):
                return _invalid_response(board, board_url, api_urls)

            pages_fetched += 1
            count = body.get("jobPostingPreviewsCount")
            total_found = count if isinstance(count, int) and count >= 0 else total_found
            for record in records:
                candidate = _candidate(record, board.identifier or "")
                if candidate is None or candidate.url in seen:
                    continue
                seen.add(candidate.url)
                candidates.append(candidate)

            consumed = page_index * _PAGE_SIZE + len(records)
            if total_found is not None and consumed >= total_found:
                inventory_complete = True
                break
            if total_found is None and (not records or len(records) < _PAGE_SIZE):
                inventory_complete = True
                break
            if not records:
                break
            if target and any(_normalized_title(item.title) == target for item in candidates):
                break

        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=candidates,
            reason_code=None if candidates else "EMPTY_PROVIDER_RESPONSE",
            inventory_scope=inventory_scope,
            inventory_complete=inventory_complete,
            trace={
                "adapter": self.name,
                "variant": "public_portal_session_api",
                "board_urls": [board_url],
                "api_urls": api_urls,
                "response_source": portal_page.source,
                "candidate_count": len(candidates),
                "pages_fetched": pages_fetched,
                "total_found": total_found,
                "inventory_scope": inventory_scope,
                "inventory_complete": inventory_complete,
            },
        )


def _client_key_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not (host == _HOST_SUFFIX or host.endswith("." + _HOST_SUFFIX))
    ):
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if (
        len(parts) >= 6
        and parts[:4] == ["v4", "ats", "web.php", "portal"]
        and parts[5] in {"career-page", "jobs"}
    ):
        return parts[4].upper() if _CLIENT_KEY.fullmatch(parts[4]) else None
    if parts == ["v4", "ats", "web.php", "jobs"]:
        values = dict(parse_qsl(parsed.query, keep_blank_values=True))
        client_key = values.get("clientkey", "")
        return client_key.upper() if _CLIENT_KEY.fullmatch(client_key) else None
    return None


def _board_url(client_key: str) -> str:
    return f"https://{_PORTAL_HOST}/v4/ats/web.php/portal/{quote(client_key, safe='')}/career-page"


def _portal_config(html: str) -> tuple[str | None, str | None]:
    if not isinstance(html, str):
        return None, None
    marker = re.search(r"\bvar\s+configsFromHost\s*=\s*(?=\{)", html[:_MAX_CONFIG_CHARS])
    if marker is None:
        return None, None
    try:
        config, _end = json.JSONDecoder().raw_decode(html, marker.end())
    except json.JSONDecodeError:
        return None, None
    if not isinstance(config, dict):
        return None, None
    token = config.get("sessionJWT")
    lib_config = config.get("libConfig")
    if not isinstance(token, str) or not token or len(token) > 16_384:
        return None, None
    try:
        library = json.loads(lib_config) if isinstance(lib_config, str) else lib_config
    except json.JSONDecodeError:
        return None, None
    service_url = library.get("atsPortalMantleServiceUrl") if isinstance(library, dict) else None
    if not isinstance(service_url, str) or not _is_safe_service_url(service_url):
        return None, None
    return token, service_url


def _is_safe_service_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and host.endswith("." + _HOST_SUFFIX)
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _is_expected_api_url(url: str, service_url: str) -> bool:
    try:
        actual = urlparse(url)
        expected = urlparse(service_url)
        actual_port = actual.port
        expected_port = expected.port
    except (TypeError, ValueError):
        return False
    return (
        actual.scheme == expected.scheme == "https"
        and actual.hostname == expected.hostname
        and actual_port in {None, 443}
        and expected_port in {None, 443}
        and actual.username is None
        and actual.password is None
        and actual.path == "/api/ats/job-posting-previews/search"
    )


def _search_payload(query: JobQuery, skip: int) -> dict:
    return {
        "skip": skip,
        "take": _PAGE_SIZE,
        "filtersForQuery": {
            "distanceFrom": 0,
            "workEnvironments": [],
            "positionTypes": [],
            "educationLevels": [],
            "categories": [],
            "travelTypes": [],
            "shiftTypes": [],
            "otherFilters": [],
            "keywordSearchText": (query.title or "").strip(),
            "location": (query.location or "").strip(),
            "sortOption": "",
        },
    }


def _candidate(record, client_key: str) -> JobCandidate | None:
    if not isinstance(record, dict):
        return None
    title = str(record.get("jobTitle") or "").strip()
    job_id = str(record.get("jobId") or "").strip()
    if not title or not _JOB_ID.fullmatch(job_id):
        return None
    url = f"https://{_PORTAL_HOST}/v4/ats/web.php/portal/{client_key}/jobs/{job_id}"
    location = str(record.get("locations") or "").strip()
    return JobCandidate(
        title=title,
        url=url,
        provider="paycom",
        location=location or None,
        raw={
            "job_id": job_id,
            "position_type": record.get("positionType"),
            "remote_type": record.get("remoteType"),
        },
    )


def _is_valid_board(board: JobBoard) -> bool:
    return (
        board.provider == "paycom"
        and bool(board.identifier)
        and bool(_CLIENT_KEY.fullmatch(board.identifier or ""))
        and _client_key_from_url(board.url) == board.identifier.upper()
    )


def _normalized_title(title: str | None) -> str:
    return " ".join((title or "").casefold().split())


def _fetch_failure(board, board_urls, api_urls, error) -> AdapterResult:
    return AdapterResult(
        provider="paycom",
        board=board,
        reason_code="PROVIDER_FETCH_FAILED",
        retryable=True,
        trace={
            "adapter": "paycom",
            "board_urls": board_urls,
            "api_urls": api_urls,
            "error": str(error),
        },
    )


def _invalid_response(board, board_url, api_urls) -> AdapterResult:
    return AdapterResult(
        provider="paycom",
        board=board,
        reason_code="INVALID_STRUCTURED_DATA",
        trace={
            "adapter": "paycom",
            "board_urls": [board_url],
            "api_urls": api_urls,
            "error": "invalid Paycom job preview response",
        },
    )


ADAPTER = PaycomAdapter()
