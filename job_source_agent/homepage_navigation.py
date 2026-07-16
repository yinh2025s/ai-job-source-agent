from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import re
from typing import Any
from urllib.parse import unquote, urlparse

from .scoring import score_career_link
from .web import Page, RawLink, extract_links, safe_normalize_url


HOMEPAGE_NAVIGATION_SCHEMA_VERSION = 1
MAX_HOMEPAGE_NAVIGATION_CANDIDATES = 8
_MAX_URL_BYTES = 2_048
_MAX_PAYLOAD_BYTES = 20 * 1_024
_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:\b(?:bearer|basic)\s+[A-Za-z0-9+/=_-]{8,}|"
    r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)
_HTML_CONTENT = re.compile(r"<(?:!doctype|html|script|body|head)\b", re.IGNORECASE)


@dataclass(frozen=True)
class HomepageNavigationEvidence:
    """Bounded public navigation URLs observed on an S2-verified homepage."""

    homepage_url: str
    candidate_urls: tuple[str, ...]
    schema_version: int = HOMEPAGE_NAVIGATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_evidence(self)

    def to_checkpoint_payload(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "homepage_url": self.homepage_url,
            "candidate_urls": list(self.candidate_urls),
        }
        if _payload_size(payload) > _MAX_PAYLOAD_BYTES:
            raise ValueError("Homepage navigation evidence payload is too large")
        return payload

    @classmethod
    def from_checkpoint_payload(cls, payload: Any) -> HomepageNavigationEvidence:
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "homepage_url",
            "candidate_urls",
        }:
            raise ValueError("Homepage navigation evidence has unsupported fields")
        if _payload_size(payload) > _MAX_PAYLOAD_BYTES:
            raise ValueError("Homepage navigation evidence payload is too large")
        candidates = payload.get("candidate_urls")
        if not isinstance(candidates, list):
            raise ValueError("Homepage navigation candidates must be a list")
        return cls(
            schema_version=payload.get("schema_version"),
            homepage_url=payload.get("homepage_url"),
            candidate_urls=tuple(candidates),
        )

    def matches(self, homepage_url: str) -> bool:
        normalized = _public_queryless_https_url(homepage_url)
        if normalized is None:
            return False
        expected = urlparse(self.homepage_url)
        actual = urlparse(normalized)
        expected_host = (expected.hostname or "").casefold().removeprefix("www.")
        actual_host = (actual.hostname or "").casefold().removeprefix("www.")
        return (
            expected_host == actual_host
            and expected.path.rstrip("/") == actual.path.rstrip("/")
        )

    def raw_links(self) -> list[RawLink]:
        return [
            RawLink(
                url=url,
                text="",
                source_url=self.homepage_url,
                origin="verified_homepage_navigation",
            )
            for url in self.candidate_urls
        ]


def evidence_from_verified_homepage(
    page: Page,
    *,
    homepage_url: str,
) -> HomepageNavigationEvidence | None:
    """Extract only URL-semantic career links from an already verified homepage."""

    normalized_homepage = _public_queryless_https_url(homepage_url)
    if normalized_homepage is None:
        return None
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for link in extract_links(page):
        if link.origin != "page_link":
            continue
        candidate_url = _public_queryless_https_url(link.url)
        if candidate_url is None or candidate_url in seen:
            continue
        url_only_link = RawLink(
            url=candidate_url,
            text="",
            source_url=normalized_homepage,
            origin="verified_homepage_navigation",
        )
        score = score_career_link(url_only_link).score
        if score < 50:
            continue
        seen.add(candidate_url)
        ranked.append((score, candidate_url))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    candidate_urls = tuple(
        url for _score, url in ranked[:MAX_HOMEPAGE_NAVIGATION_CANDIDATES]
    )
    if not candidate_urls:
        return None
    return HomepageNavigationEvidence(
        homepage_url=normalized_homepage,
        candidate_urls=candidate_urls,
    )


def _validate_evidence(evidence: HomepageNavigationEvidence) -> None:
    if evidence.schema_version != HOMEPAGE_NAVIGATION_SCHEMA_VERSION:
        raise ValueError("Homepage navigation evidence schema version is incompatible")
    if _public_queryless_https_url(evidence.homepage_url) != evidence.homepage_url:
        raise ValueError("Homepage navigation homepage URL is not canonical public HTTPS")
    candidates = evidence.candidate_urls
    if (
        not isinstance(candidates, tuple)
        or not candidates
        or len(candidates) > MAX_HOMEPAGE_NAVIGATION_CANDIDATES
    ):
        raise ValueError("Homepage navigation candidate count is invalid")
    if len(set(candidates)) != len(candidates):
        raise ValueError("Homepage navigation candidates must be unique")
    for candidate in candidates:
        if _public_queryless_https_url(candidate) != candidate:
            raise ValueError("Homepage navigation candidate URL is not canonical public HTTPS")


def _public_queryless_https_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if len(value.encode("utf-8")) > _MAX_URL_BYTES or _contains_unsafe_content(value):
        return None
    normalized = safe_normalize_url(value)
    if normalized is None or normalized != value:
        return None
    try:
        parsed = urlparse(normalized)
        port = parsed.port
        decoded_path = unquote(parsed.path)
    except (TypeError, ValueError, UnicodeError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not _is_public_host(parsed.hostname)
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or _has_controls(decoded_path)
    ):
        return None
    return normalized


def _is_public_host(value: str | None) -> bool:
    host = (value or "").casefold().rstrip(".")
    if (
        not host
        or len(host) > 253
        or not _HOSTNAME.fullmatch(host)
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
    ):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return "." in host


def _contains_unsafe_content(value: str) -> bool:
    decoded = unquote(value)
    return bool(
        _has_controls(value)
        or _has_controls(decoded)
        or _SECRET_VALUE.search(value)
        or _SECRET_VALUE.search(decoded)
        or _HTML_CONTENT.search(value)
        or _HTML_CONTENT.search(decoded)
    )


def _has_controls(value: str) -> bool:
    return any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)


def _payload_size(payload: Any) -> int:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("Homepage navigation evidence payload is not JSON-safe") from exc
    return len(encoded)
