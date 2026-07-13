from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


IDENTITY_VERSION = "1"
REDACTED_VALUE = "[REDACTED]"

_SENSITIVE_KEYS = {
    "accesstoken",
    "apikey",
    "auth",
    "authorization",
    "code",
    "csrf",
    "idtoken",
    "key",
    "password",
    "protectedsessionjwt",
    "refreshtoken",
    "secret",
    "session",
    "sessioncsrftoken",
    "sessionjwt",
    "sig",
    "signature",
    "state",
    "token",
}
_SENSITIVE_MARKERS = ("token", "secret", "password", "credential", "session", "csrf")
_SEMANTIC_HEADERS = {
    "accept",
    "content-type",
    "origin",
    "referer",
    "x-referer-host",
}
_MAX_MULTIPART_CHARS = 2_000_000
_MAX_MULTIPART_FIELDS = 100
_CEIPAL_CREDENTIAL_PATH = re.compile(
    r"^/([^/]+)/((?:careerportal)[A-Za-z0-9_-]*)(/.*)?$",
    re.I,
)
_REDACTED_PATH_SEGMENT = "%5BREDACTED%5D"


@dataclass(frozen=True)
class RequestIdentity:
    identity_version: str
    method: str
    sanitized_url: str
    body_fingerprint: str | None
    semantic_headers: dict[str, str]
    replayable: bool
    non_replayable_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.as_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    @property
    def requires_fixture_suffix(self) -> bool:
        return (
            self.method != "GET"
            or self.body_fingerprint is not None
            or bool(self.semantic_headers)
        )


def canonical_sensitive_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(key).casefold())


def is_sensitive_key(key: str) -> bool:
    canonical = canonical_sensitive_key(key)
    return canonical in _SENSITIVE_KEYS or any(marker in canonical for marker in _SENSITIVE_MARKERS)


def sanitize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    if (parsed.hostname or "").casefold() == "careerapi.ceipal.com":
        match = _CEIPAL_CREDENTIAL_PATH.fullmatch(path)
        if match and match.group(1).casefold() not in {
            REDACTED_VALUE.casefold(),
            _REDACTED_PATH_SEGMENT.casefold(),
        }:
            path = (
                f"/{_REDACTED_PATH_SEGMENT}/{match.group(2)}"
                + (match.group(3) or "")
            )
    query = urlencode(
        [
            (key, _redacted_value(value) if is_sensitive_key(key) else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ],
        doseq=True,
    )
    return urlunparse(parsed._replace(path=path, query=query, fragment=""))


def build_request_identity(
    url: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> RequestIdentity:
    method = "POST" if data is not None else "GET"
    semantic_headers = _sanitize_semantic_headers(headers or {})
    body_fingerprint, replayable, reason = _body_identity(data, semantic_headers)
    return RequestIdentity(
        identity_version=IDENTITY_VERSION,
        method=method,
        sanitized_url=sanitize_url(url),
        body_fingerprint=body_fingerprint,
        semantic_headers=semantic_headers,
        replayable=replayable,
        non_replayable_reason=reason,
    )


def request_identity_from_dict(payload: dict[str, Any]) -> RequestIdentity:
    if not isinstance(payload, dict):
        raise ValueError("request identity must be an object")
    expected = {
        "identity_version",
        "method",
        "sanitized_url",
        "body_fingerprint",
        "semantic_headers",
        "replayable",
        "non_replayable_reason",
    }
    if set(payload) != expected:
        raise ValueError("request identity fields do not match schema")
    identity_version = payload["identity_version"]
    method = payload["method"]
    sanitized_url = payload["sanitized_url"]
    body_fingerprint = payload["body_fingerprint"]
    semantic_headers = payload["semantic_headers"]
    replayable = payload["replayable"]
    reason = payload["non_replayable_reason"]
    if identity_version != IDENTITY_VERSION:
        raise ValueError("unsupported request identity version")
    if method not in {"GET", "POST"}:
        raise ValueError("unsupported request method")
    if not isinstance(sanitized_url, str) or sanitize_url(sanitized_url) != sanitized_url:
        raise ValueError("request URL is not sanitized")
    if body_fingerprint is not None and not re.fullmatch(r"[0-9a-f]{64}", body_fingerprint):
        raise ValueError("invalid body fingerprint")
    if not isinstance(semantic_headers, dict) or semantic_headers != _sanitize_semantic_headers(semantic_headers):
        raise ValueError("semantic headers are not sanitized")
    if type(replayable) is not bool:
        raise ValueError("replayable must be boolean")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("invalid non-replayable reason")
    if replayable and reason is not None:
        raise ValueError("replayable identity cannot have an exclusion reason")
    if not replayable and (body_fingerprint is not None or not reason):
        raise ValueError("non-replayable identity must have only an exclusion reason")
    return RequestIdentity(
        identity_version=identity_version,
        method=method,
        sanitized_url=sanitized_url,
        body_fingerprint=body_fingerprint,
        semantic_headers=semantic_headers,
        replayable=replayable,
        non_replayable_reason=reason,
    )


def _body_identity(
    data: bytes | None,
    semantic_headers: dict[str, str],
) -> tuple[str | None, bool, str | None]:
    if data is None:
        return None, True, None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, False, "opaque_non_utf8_body"
    if not text:
        return _digest_text(""), True, None

    content_type = semantic_headers.get("content-type", "").split(";", 1)[0].strip()
    stripped = text.lstrip()
    if content_type.endswith("json") or stripped.startswith(("{", "[")):
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return None, False, "invalid_json_body"
        sanitized = _sanitize_structured_value(value)
        canonical = json.dumps(
            sanitized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return _digest_text(canonical), True, None

    if content_type == "multipart/form-data":
        fields = _multipart_form_fields(text, semantic_headers.get("content-type", ""))
        if fields is None:
            return None, False, "invalid_multipart_form_body"
        canonical = json.dumps(
            sorted(
                (
                    key,
                    _redacted_value(value) if is_sensitive_key(key) else value,
                )
                for key, value in fields
            ),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return _digest_text(canonical), True, None

    if content_type == "application/x-www-form-urlencoded" or "=" in text:
        try:
            fields = parse_qsl(text, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            return None, False, "invalid_form_body"
        canonical = urlencode(
            sorted(
                (
                    key,
                    _redacted_value(value) if is_sensitive_key(key) else value,
                )
                for key, value in fields
            ),
            doseq=True,
        )
        return _digest_text(canonical), True, None

    return None, False, "opaque_body"


def _multipart_form_fields(text: str, content_type: str) -> list[tuple[str, str]] | None:
    if len(text) > _MAX_MULTIPART_CHARS:
        return None
    boundary_match = re.search(
        r"(?:^|;)\s*boundary=(?:\"([^\"]+)\"|([^;\s]+))",
        content_type,
        re.I,
    )
    if boundary_match is None:
        return None
    boundary = boundary_match.group(1) or boundary_match.group(2)
    if (
        not boundary
        or len(boundary) > 70
        or any(ord(character) < 32 or ord(character) > 126 for character in boundary)
    ):
        return None

    delimiter = f"--{boundary}"
    parts = text.split(delimiter)
    if len(parts) < 3 or parts[0] or parts[-1] not in {"--", "--\r\n"}:
        return None
    fields: list[tuple[str, str]] = []
    for part in parts[1:-1]:
        if not part.startswith("\r\n") or not part.endswith("\r\n"):
            return None
        header_text, separator, value = part[2:-2].partition("\r\n\r\n")
        if not separator:
            return None
        headers: dict[str, str] = {}
        for line in header_text.split("\r\n"):
            name, colon, header_value = line.partition(":")
            key = name.casefold().strip()
            if not colon or not key or key in headers:
                return None
            headers[key] = header_value.strip()
        disposition = headers.get("content-disposition", "")
        name_match = re.fullmatch(
            r'form-data\s*;\s*name="([^"\r\n]{1,200})"',
            disposition,
            re.I,
        )
        if name_match is None or "filename" in disposition.casefold():
            return None
        if set(headers) - {"content-disposition"}:
            return None
        fields.append((name_match.group(1), value))
        if len(fields) > _MAX_MULTIPART_FIELDS:
            return None
    return fields


def _sanitize_structured_value(value: Any, *, parent_sensitive: bool = False) -> Any:
    if parent_sensitive:
        if value in (None, "", [], {}):
            return value
        return REDACTED_VALUE
    if isinstance(value, dict):
        return {
            str(key): _sanitize_structured_value(item, parent_sensitive=is_sensitive_key(str(key)))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_structured_value(item) for item in value]
    return value


def _sanitize_semantic_headers(headers: dict[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for raw_key, raw_value in headers.items():
        key = str(raw_key).casefold().strip()
        if key not in _SEMANTIC_HEADERS or is_sensitive_key(key):
            continue
        value = " ".join(str(raw_value).split())
        if key in {"origin", "referer", "x-referer-host"}:
            value = sanitize_url(value)
        elif key == "content-type":
            media_type, separator, parameters = value.partition(";")
            value = media_type.casefold().strip()
            if separator:
                value += ";" + parameters.strip()
        sanitized[key] = value
    return dict(sorted(sanitized.items()))


def _redacted_value(value: str) -> str:
    return REDACTED_VALUE if value else value


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
