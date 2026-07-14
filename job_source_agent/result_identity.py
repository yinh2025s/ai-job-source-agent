from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from .web import TRACKING_PARAMS


_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")
_DECODED_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MALFORMED_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def canonicalize_identity_url(value: object) -> str:
    """Return a strict public identity, preserving semantic query and fragment data."""

    if (
        not isinstance(value, str)
        or not value
        or _CONTROL_OR_SPACE.search(value)
        or _contains_unicode_control(value)
    ):
        raise ValueError("identity URL must be a non-empty HTTP(S) URL without controls")
    if _MALFORMED_ESCAPE.search(value):
        raise ValueError("identity URL contains a malformed percent escape")

    try:
        parsed = urlsplit(value)
        port = parsed.port
        hostname = parsed.hostname
    except (UnicodeError, ValueError) as exc:
        raise ValueError("identity URL is malformed") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ValueError("identity URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("identity URL must not contain credentials")
    for component in (parsed.path, parsed.query, parsed.fragment):
        try:
            decoded = unquote(component, errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("identity URL contains invalid percent-encoded text") from exc
        if _DECODED_CONTROL.search(decoded) or _contains_unicode_control(decoded):
            raise ValueError("identity URL contains a percent-encoded control")

    scheme = parsed.scheme.casefold()
    if "%" in hostname:
        raise ValueError("identity URL host must not be percent encoded")
    try:
        ascii_host = hostname.encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise ValueError("identity URL host is invalid") from exc
    if ascii_host.endswith("."):
        ascii_host = ascii_host[:-1]
    if not ascii_host or any(label == "" for label in ascii_host.split(".")):
        raise ValueError("identity URL host is invalid")
    if (
        ascii_host == "localhost"
        or ascii_host.endswith(".local")
        or ascii_host.endswith(".internal")
    ):
        raise ValueError("identity URL host must be public")
    try:
        literal_ip = ipaddress.ip_address(ascii_host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and not literal_ip.is_global:
        raise ValueError("identity URL literal IP must be globally routable")
    if ":" in ascii_host:
        ascii_host = f"[{ascii_host}]"
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{ascii_host}:{port}"
    else:
        netloc = ascii_host

    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() not in TRACKING_PARAMS
        ],
        doseq=True,
    )
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, query, parsed.fragment))


def public_result_identity(result: dict, provider: str) -> dict:
    """Build the normalized, runtime-identifier-free identity for a result."""

    website_url = _canonical_or_none(result.get("company_website_url"))
    career_page_url = _canonical_or_none(result.get("career_page_url"))
    job_list_url = _canonical_or_none(result.get("job_list_page_url"))
    opening_url = _canonical_or_none(result.get("open_position_url"))
    return {
        "website_url": website_url,
        "career_page_url": career_page_url,
        "job_board": {
            "provider": provider,
            "tenant": tenant_locator(job_list_url) if job_list_url else None,
            "canonical_url": job_list_url,
        },
        "opening": {"canonical_url": opening_url},
    }


def tenant_locator(canonical_job_list_url: str) -> str:
    return f"url:{canonical_job_list_url}"


def identity_urls_equivalent(actual: str, expected: str, *, allow_www: bool = False) -> bool:
    if actual == expected:
        return True
    if not allow_www:
        return False
    actual_parts = urlsplit(actual)
    expected_parts = urlsplit(expected)
    return (
        actual_parts.scheme,
        actual_parts.hostname.removeprefix("www.") if actual_parts.hostname else None,
        actual_parts.port,
        actual_parts.path,
        actual_parts.query,
        actual_parts.fragment,
    ) == (
        expected_parts.scheme,
        expected_parts.hostname.removeprefix("www.") if expected_parts.hostname else None,
        expected_parts.port,
        expected_parts.path,
        expected_parts.query,
        expected_parts.fragment,
    )


def _canonical_or_none(value: object) -> str | None:
    if value is None or value == "":
        return None
    try:
        return canonicalize_identity_url(value)
    except ValueError:
        return None


def _contains_unicode_control(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)
