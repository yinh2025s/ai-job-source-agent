from __future__ import annotations

import hashlib
import ipaddress
import json
from urllib.parse import urlencode, urlparse, urlunparse

from .search_backend import (
    SearchBackendResponse,
    SearchHit,
    SearchQuery,
)
from .web import Fetcher


SEARCH_BACKEND_CONTRACT_VERSION = "1"
SEARCH_LANGUAGE = "en-US"
SEARCH_SAFESEARCH = "1"
_PROVENANCE_ORIGIN = "https://search-backend.invalid"


class SearxngSearchBackend:
    name = "searxng"

    def __init__(
        self,
        endpoint: str,
        *,
        server_profile_digest: str | None = None,
    ) -> None:
        self._endpoint = _validated_endpoint(endpoint)
        self._server_profile_digest = _validated_server_profile_digest(
            server_profile_digest
        )
        self._profile_digest = _profile_digest(
            self._endpoint,
            self._server_profile_digest,
        )

    def search(
        self,
        query: SearchQuery,
        *,
        fetcher: Fetcher,
    ) -> SearchBackendResponse:
        raw_request_url = _request_url(self._endpoint, query.text)
        page = fetcher.fetch(
            raw_request_url,
            headers={"Accept": "application/json"},
        )
        raw_final_url = page.final_url or page.url
        safe_request_url = _safe_provenance_url(
            self._profile_digest,
            query.text,
        )
        safe_final_url = _safe_provenance_url(
            self._profile_digest,
            query.text,
            final_url=raw_final_url,
        )
        try:
            payload = json.loads(page.html)
        except (json.JSONDecodeError, TypeError):
            return SearchBackendResponse(
                request_url=safe_request_url,
                final_url=safe_final_url,
                hits=(),
                disposition="invalid_response",
                reason="malformed_json",
            )

        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            return SearchBackendResponse(
                request_url=safe_request_url,
                final_url=safe_final_url,
                hits=(),
                disposition="invalid_response",
                reason="invalid_results_shape",
            )

        hits = tuple(
            hit
            for item in payload["results"]
            if (hit := _parse_hit(item)) is not None
        )
        return SearchBackendResponse(
            request_url=safe_request_url,
            final_url=safe_final_url,
            hits=hits,
        )

    def public_configuration(self) -> dict[str, str]:
        return {
            "search_backend_kind": self.name,
            "search_backend_contract_version": SEARCH_BACKEND_CONTRACT_VERSION,
            "search_backend_profile_digest": self._profile_digest,
        }

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"profile_digest={self._profile_digest!r})"
        )


def _validated_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError("SearXNG endpoint must be a non-empty URL")
    if endpoint != endpoint.strip():
        raise ValueError("SearXNG endpoint must not contain surrounding whitespace")

    try:
        parsed = urlparse(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("SearXNG endpoint is invalid") from exc

    scheme = parsed.scheme.casefold()
    hostname = parsed.hostname
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError("SearXNG endpoint must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("SearXNG endpoint must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("SearXNG endpoint must not contain a query or fragment")
    if scheme == "http" and not _is_loopback_host(hostname):
        raise ValueError("HTTP SearXNG endpoints are restricted to loopback")

    host = hostname.casefold()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    if not path:
        path = "/search"
    elif not path.endswith("/search"):
        path = f"{path}/search"
    return urlunparse((scheme, netloc, path, "", "", ""))


def _is_loopback_host(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _request_url(endpoint: str, query: str) -> str:
    parameters = urlencode(
        {
            "format": "json",
            "language": SEARCH_LANGUAGE,
            "q": query,
            "safesearch": SEARCH_SAFESEARCH,
        }
    )
    return f"{endpoint}?{parameters}"


def _profile_digest(
    endpoint: str,
    server_profile_digest: str | None,
) -> str:
    profile = {
        "contract_version": SEARCH_BACKEND_CONTRACT_VERSION,
        "endpoint": endpoint,
        "format": "json",
        "language": SEARCH_LANGUAGE,
        "safesearch": SEARCH_SAFESEARCH,
        "server_profile_digest": server_profile_digest,
        "transport_calls_per_search": 1,
    }
    canonical = json.dumps(
        profile,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validated_server_profile_digest(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("SearXNG server profile digest is invalid")
    return value


def _safe_provenance_url(
    profile_digest: str,
    query: str,
    *,
    final_url: str | None = None,
) -> str:
    query_digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
    parameters = {"query_sha256": query_digest}
    if final_url is not None:
        parameters["final_url_sha256"] = hashlib.sha256(
            final_url.encode("utf-8")
        ).hexdigest()
    return (
        f"{_PROVENANCE_ORIGIN}/searxng/{profile_digest}"
        f"?{urlencode(parameters)}"
    )


def _parse_hit(item: object) -> SearchHit | None:
    if not isinstance(item, dict):
        return None
    url = item.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    title = item.get("title", "")
    content = item.get("content", "")
    if not isinstance(title, str) or not isinstance(content, str):
        return None
    return SearchHit(url=url, title=title, snippet=content)
