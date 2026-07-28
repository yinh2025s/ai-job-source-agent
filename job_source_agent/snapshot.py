from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import fcntl

from .browser_interaction import BrowserInteraction
from .reasons import classify_fetch_error, reason_spec
from .request_identity import build_request_identity, is_sensitive_key, sanitize_url
from .web import FetchError, Page, fixture_path_candidates, normalize_transport_exception

if TYPE_CHECKING:
    from .snapshot_capture import SnapshotCaptureCoordinator, SnapshotRequestCapture


SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "api-key",
    "apikey",
    "auth",
    "authorization",
    "code",
    "id_token",
    "key",
    "password",
    "refresh_token",
    "secret",
    "session",
    "sig",
    "signature",
    "state",
    "token",
}

SENSITIVE_BODY_FIELDS = SENSITIVE_QUERY_KEYS | {
    "_csrf",
    "authToken",
    "myJobsToken",
    "protectedSessionJWT",
    "sessionCSRFToken",
    "sessionJWT",
}

_LOCATION_CONTEXT_KEYS = {
    "address",
    "city",
    "country",
    "countrycode",
    "location",
    "postalcode",
    "province",
    "region",
    "street",
    "zipcode",
    "zip",
}
_AUTH_CONTEXT_KEYS = {
    "accesstoken",
    "authorization",
    "clientid",
    "code",
    "csrf",
    "idtoken",
    "nonce",
    "oauth",
    "redirecturi",
    "refreshtoken",
    "session",
    "token",
}
_MAX_EMBEDDED_JSON_OBJECT_CHARS = 16_384
_HMG_REPLAY_HASH = "0" * 32
_HMG_REPLAY_TIME = "1000000000"
_HMG_TICKET_OBJECT = re.compile(
    r'(?is)("ticket"\s*:\s*\{)([^{}]{0,1024})(\})'
)
_API_KEY_FIELD_PATTERN = r"[A-Za-z0-9_$-]*api[_-]?key"
_GOOGLE_BROWSER_API_KEY = re.compile(
    r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])"
)
_AWS_ACCESS_KEY_ID = re.compile(
    r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"
)
_JWT_VALUE = re.compile(
    r"(?P<prefix>(?<![A-Za-z0-9_.-])|%3[dD])"
    r"(?P<header>[A-Za-z0-9_-]{2,1024}={0,2})\."
    r"(?P<payload>[A-Za-z0-9_-]{2,16384}={0,2})\."
    r"(?P<signature>[A-Za-z0-9_-]{1,8192}={0,2})"
    r"(?![A-Za-z0-9_.=-])"
)
_JWT_CAPABILITY_OR_TIME_CLAIMS = {
    "auth_time",
    "capabilities",
    "capability",
    "exp",
    "iat",
    "nbf",
    "permission",
    "permissions",
    "role",
    "roles",
    "scope",
    "scopes",
    "scp",
}


@dataclass
class SnapshotRecord:
    schema_version: int
    kind: str
    sequence: int
    request: dict
    request_url: str
    page_url: str
    final_url: str
    sanitized_url: str
    source: str
    path: str
    blob_path: str
    artifact_paths: dict[str, str]
    artifact_blob_paths: dict[str, str]
    sha256: str
    byte_count: int
    captured_at_epoch: float
    snapshot_store_id: str | None = None
    scope_id: str | None = None
    capture_attempt_id: str | None = None
    execution_fingerprint: str | None = None
    stage: str | None = None
    request_ordinal: int | None = None


@dataclass
class FetchFailureRecord:
    schema_version: int
    kind: str
    sequence: int
    request: dict
    failure: dict
    captured_at_epoch: float
    terminal: bool
    snapshot_store_id: str | None = None
    scope_id: str | None = None
    capture_attempt_id: str | None = None
    execution_fingerprint: str | None = None
    stage: str | None = None
    request_ordinal: int | None = None


class SnapshotStore:
    """Persist fetched pages as sanitized, fixture-compatible snapshots."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.fixtures_dir = self.root_dir / "sites"
        self.index_path = self.root_dir / "snapshots.jsonl"
        self.failure_index_path = self.root_dir / "fetch-failures.jsonl"
        self.sequence_path = self.root_dir / ".snapshot-sequence"
        self.store_id_path = self.root_dir / ".snapshot-store-id"

    @property
    def snapshot_store_id(self) -> str:
        """Return the durable opaque identity for this snapshot root."""
        self.root_dir.mkdir(parents=True, exist_ok=True)
        with self._write_lock():
            try:
                store_id = self.store_id_path.read_text(encoding="ascii").strip()
            except FileNotFoundError:
                store_id = uuid.uuid4().hex
                _write_bytes_atomic(self.store_id_path, f"{store_id}\n".encode("ascii"))
            if re.fullmatch(r"[a-f0-9]{32}", store_id) is None:
                raise ValueError("Snapshot store ID is missing or corrupt")
            return store_id

    def write_page(
        self,
        page: Page,
        request_url: str | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        capture: SnapshotRequestCapture | None = None,
        *,
        interaction: BrowserInteraction | None = None,
    ) -> SnapshotRecord:
        self._validate_capture(capture)
        request_identity = build_request_identity(
            request_url or page.url,
            data=data,
            headers=headers,
            interaction=interaction,
        )
        sanitized_final_url = sanitize_url(page.final_url or page.url)
        html = sanitize_snapshot_body(page.html)
        encoded = html.encode("utf-8")
        self.root_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(encoded).hexdigest()
        path = snapshot_path_for_url(
            self.fixtures_dir,
            sanitized_final_url,
            request_identity=request_identity,
        )
        blob_path = snapshot_blob_path(self.root_dir, digest)
        with self._write_lock():
            _write_immutable_blob(blob_path, encoded, digest)
            _write_bytes_atomic(path, encoded)
            artifact_paths, artifact_blob_paths = self._write_artifacts(page, sanitized_final_url)
            record = SnapshotRecord(
                schema_version=3 if capture is not None else 2,
                kind="page",
                sequence=self._next_sequence(),
                request=request_identity.as_dict(),
                request_url=sanitize_url(request_url or page.url),
                page_url=sanitize_url(page.url),
                final_url=sanitized_final_url,
                sanitized_url=sanitized_final_url,
                source=sanitize_snapshot_source(page.source),
                path=str(path.relative_to(self.root_dir)),
                blob_path=str(blob_path.relative_to(self.root_dir)),
                artifact_paths=artifact_paths,
                artifact_blob_paths=artifact_blob_paths,
                sha256=digest,
                byte_count=len(encoded),
                captured_at_epoch=round(time.time(), 3),
                **(_capture_fields(capture) if capture is not None else {}),
            )
            _append_jsonl_durable(self.index_path, _record_payload(record))
        return record

    def write_failure(
        self,
        error: FetchError,
        request_url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        capture: SnapshotRequestCapture | None = None,
        *,
        interaction: BrowserInteraction | None = None,
    ) -> FetchFailureRecord:
        self._validate_capture(capture)
        request_identity = build_request_identity(
            request_url,
            data=data,
            headers=headers,
            interaction=interaction,
        )
        reason_code = error.reason_code or classify_fetch_error(str(error))
        retryable = (
            error.retryable
            if error.retryable is not None
            else reason_spec(reason_code).retryable
        )
        status = error.status if isinstance(error.status, int) else None
        safe_message = f"HTTP {status} {reason_code}" if status is not None else reason_code
        self.root_dir.mkdir(parents=True, exist_ok=True)
        with self._write_lock():
            record = FetchFailureRecord(
                schema_version=3 if capture is not None else 2,
                kind="fetch_failure",
                sequence=self._next_sequence(),
                request=request_identity.as_dict(),
                failure={
                    "status": status,
                    "reason_code": reason_code,
                    "retryable": retryable,
                    "message": safe_message,
                    "taxonomy_version": 1,
                },
                captured_at_epoch=round(time.time(), 3),
                terminal=True,
                **(_capture_fields(capture) if capture is not None else {}),
            )
            _append_jsonl_durable(self.failure_index_path, _record_payload(record))
        return record

    def _next_sequence(self) -> int:
        try:
            current = int(self.sequence_path.read_text(encoding="ascii"))
        except (FileNotFoundError, OSError, ValueError):
            current = 0
        next_value = current + 1
        _write_bytes_atomic(self.sequence_path, f"{next_value}\n".encode("ascii"))
        return next_value

    def _write_artifacts(
        self,
        page: Page,
        sanitized_url: str,
    ) -> tuple[dict[str, str], dict[str, str]]:
        artifact_paths: dict[str, str] = {}
        artifact_blob_paths: dict[str, str] = {}
        for name, content in (page.artifacts or {}).items():
            if not isinstance(content, bytes):
                continue
            digest = hashlib.sha256(content).hexdigest()
            artifact_path = snapshot_artifact_path_for_url(self.root_dir / "artifacts", sanitized_url, name)
            blob_path = snapshot_artifact_blob_path(self.root_dir, digest, name)
            _write_immutable_blob(blob_path, content, digest)
            _write_bytes_atomic(artifact_path, content)
            artifact_paths[name] = str(artifact_path.relative_to(self.root_dir))
            artifact_blob_paths[name] = str(blob_path.relative_to(self.root_dir))
        return artifact_paths, artifact_blob_paths

    def _validate_capture(self, capture: SnapshotRequestCapture | None) -> None:
        if capture is not None and capture.snapshot_store_id != self.snapshot_store_id:
            raise ValueError("Snapshot capture belongs to another snapshot store")

    @contextmanager
    def _write_lock(self):
        lock_path = self.root_dir / ".snapshot.lock"
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class SnapshottingFetcher:
    """Wrap a fetcher and record terminal fetch outcomes as snapshots."""

    def __init__(
        self,
        fetcher,
        snapshot_dir: str | Path,
        coordinator: SnapshotCaptureCoordinator | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.snapshot_store = SnapshotStore(snapshot_dir)
        self.coordinator = coordinator
        if coordinator is not None:
            coordinator.bind_store(self.snapshot_store)
        self.timeout = getattr(fetcher, "timeout", None)

    def fetch(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        *,
        interaction: BrowserInteraction | None = None,
    ) -> Page:
        capture = self.coordinator.begin_request() if self.coordinator is not None else None
        try:
            if interaction is None:
                page = self.fetcher.fetch(url, data=data, headers=headers)
            else:
                page = self.fetcher.fetch(
                    url,
                    data=data,
                    headers=headers,
                    interaction=interaction,
                )
        except BaseException as raw_error:
            error = normalize_transport_exception(
                raw_error,
                url=url,
                data=data,
                headers=headers,
            )
            if error is None:
                raise
            record = self.snapshot_store.write_failure(
                error,
                url,
                data=data,
                headers=headers,
                capture=capture,
                interaction=interaction,
            )
            if self.coordinator is not None:
                self.coordinator.accept_terminal_record(record)
            if error is raw_error:
                raise
            raise error from raw_error
        record = self.snapshot_store.write_page(
            page,
            request_url=url,
            data=data,
            headers=headers,
            capture=capture,
            interaction=interaction,
        )
        if self.coordinator is not None:
            self.coordinator.accept_terminal_record(record)
        page.source = f"{page.source}|snapshot:{record.path}"
        return page

    def remaining_fetch_seconds(self) -> float | None:
        remaining = getattr(self.fetcher, "remaining_fetch_seconds", None)
        return remaining() if callable(remaining) else None

    def record_fetch_failure(
        self,
        error: FetchError,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        *,
        interaction: BrowserInteraction | None = None,
    ) -> FetchFailureRecord:
        capture = self.coordinator.begin_request() if self.coordinator is not None else None
        record = self.snapshot_store.write_failure(
            error,
            url,
            data=data,
            headers=headers,
            capture=capture,
            interaction=interaction,
        )
        if self.coordinator is not None:
            self.coordinator.accept_terminal_record(record)
        return record

    def __getattr__(self, name: str):
        return getattr(self.fetcher, name)


def snapshot_path_for_url(
    fixtures_dir: str | Path,
    url: str,
    *,
    request_identity=None,
) -> Path:
    return fixture_path_candidates(
        fixtures_dir,
        url,
        request_identity=request_identity,
    )[0]


def snapshot_artifact_path_for_url(artifacts_dir: str | Path, url: str, artifact_name: str) -> Path:
    extension = {
        "screenshot_png": "png",
    }.get(artifact_name, "bin")
    safe_name = f"{_safe_path_part(artifact_name)}.{extension}"
    page_path = snapshot_path_for_url(artifacts_dir, url)
    if ".__query_" in page_path.name:
        safe_name = f"{page_path.stem}.{safe_name}"
    return page_path.with_name(safe_name)


def snapshot_blob_path(root_dir: str | Path, digest: str) -> Path:
    return Path(root_dir) / "blobs" / "pages" / f"{digest}.html"


def snapshot_artifact_blob_path(root_dir: str | Path, digest: str, artifact_name: str) -> Path:
    extension = {"screenshot_png": "png"}.get(artifact_name, "bin")
    return Path(root_dir) / "blobs" / "artifacts" / f"{digest}.{extension}"


def sanitize_snapshot_body(body: str) -> str:
    try:
        structured = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return _sanitize_snapshot_text(body)
    return json.dumps(
        _sanitize_snapshot_value(_sanitize_hmg_structured_ticket(structured)),
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _sanitize_snapshot_value(value):
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if _is_sensitive_key(str(key))
                and not is_public_location_state_field(value, str(key))
                else _sanitize_snapshot_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_snapshot_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_snapshot_text(value)
    return value


def _sanitize_snapshot_text(body: str) -> str:
    body = _sanitize_hmg_text_tickets(body)
    body = _redact_public_domain_registry_contacts(body)
    body, protected_location_keys = _protect_embedded_public_location_states(body)
    redacted = re.sub(
        r"(?i)(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._~+/=-]{8,}",
        r"\1[REDACTED]",
        body,
    )
    redacted = re.sub(
        r"(?i)(https://careerapi\.ceipal\.com/)[^/\"'\s<>]+"
        r"(/(?:careerportal)[A-Za-z0-9_-]*/)",
        r"\1[REDACTED]\2",
        redacted,
    )
    for key in sorted(SENSITIVE_BODY_FIELDS):
        redacted = _redact_snapshot_text_field(redacted, re.escape(key))
    redacted = _redact_snapshot_text_field(redacted, _API_KEY_FIELD_PATTERN)
    redacted = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1[REDACTED]", redacted)
    for marker, original_key in protected_location_keys:
        redacted = redacted.replace(json.dumps(marker), json.dumps(original_key))
    redacted = _GOOGLE_BROWSER_API_KEY.sub("[REDACTED]", redacted)
    redacted = _AWS_ACCESS_KEY_ID.sub("[REDACTED]", redacted)
    return _redact_jwt_values(redacted)


def _redact_jwt_values(body: str) -> str:
    def replace_if_valid(match: re.Match[str]) -> str:
        header = _decode_base64url_json_object(match.group("header"))
        payload = _decode_base64url_json_object(match.group("payload"))
        if header is None or payload is None:
            return match.group(0)
        if not isinstance(header.get("alg"), str) or not header["alg"].strip():
            return match.group(0)
        if not _JWT_CAPABILITY_OR_TIME_CLAIMS.intersection(payload):
            return match.group(0)
        return f'{match.group("prefix")}[REDACTED]'

    return _JWT_VALUE.sub(replace_if_valid, body)


def _decode_base64url_json_object(segment: str) -> dict | None:
    try:
        decoded = base64.b64decode(
            segment + "=" * (-len(segment) % 4),
            altchars=b"-_",
            validate=True,
        )
        value = json.loads(decoded)
    except (UnicodeDecodeError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _redact_snapshot_text_field(body: str, key_pattern: str) -> str:
    body = re.sub(
        rf"(?i)([\"']{key_pattern}[\"']\s*:\s*)([\"'])[^\"']*(\2)",
        rf"\1\2[REDACTED]\3",
        body,
    )
    body = re.sub(
        rf"(?i)((?<![A-Za-z0-9_$-]){key_pattern}\s*[=:]\s*)"
        rf"([\"']?)[^\"'&\s<>,;]+(\2)",
        rf"\1\2[REDACTED]\3",
        body,
    )
    body = re.sub(
        rf"(?i)(<input\b[^>]*(?:id|name)\s*=\s*[\"']{key_pattern}[\"'][^>]*"
        rf"\bvalue\s*=\s*[\"'])[^\"']*([\"'])",
        rf"\1[REDACTED]\2",
        body,
    )
    body = re.sub(
        rf"(?i)(<input\b[^>]*\bvalue\s*=\s*[\"'])[^\"']*([\"'][^>]*"
        rf"(?:id|name)\s*=\s*[\"']{key_pattern}[\"'])",
        rf"\1[REDACTED]\2",
        body,
    )
    body = re.sub(
        rf"(?i)(<meta\b[^>]*(?:id|name|property)\s*=\s*[\"']{key_pattern}[\"'][^>]*"
        rf"\bcontent\s*=\s*[\"'])[^\"']*([\"'])",
        rf"\1[REDACTED]\2",
        body,
    )
    return re.sub(
        rf"(?i)(<meta\b[^>]*\bcontent\s*=\s*[\"'])[^\"']*([\"'][^>]*"
        rf"(?:id|name|property)\s*=\s*[\"']{key_pattern}[\"'])",
        rf"\1[REDACTED]\2",
        body,
    )


def _sanitize_hmg_structured_ticket(value):
    if not isinstance(value, dict):
        return value
    result_set = value.get("ResultSet")
    if (
        not isinstance(result_set, dict)
        or not isinstance(result_set.get("list"), list)
        or not isinstance(result_set.get("list_meta"), dict)
    ):
        return value
    ticket = result_set.get("ticket")
    if not isinstance(ticket, dict):
        return value
    sanitized = dict(value)
    sanitized_result_set = dict(result_set)
    sanitized_ticket = dict(ticket)
    if sanitized_ticket.get("h"):
        sanitized_ticket["h"] = _HMG_REPLAY_HASH
    if sanitized_ticket.get("t"):
        sanitized_ticket["t"] = _HMG_REPLAY_TIME
    sanitized_result_set["ticket"] = sanitized_ticket
    sanitized["ResultSet"] = sanitized_result_set
    return sanitized


def _sanitize_hmg_text_tickets(body: str) -> str:
    if not isinstance(body, str):
        return body
    if _is_hmg_board_html(body):
        body = _replace_hmg_input_value(body, "h", _HMG_REPLAY_HASH)
        body = _replace_hmg_input_value(body, "t", _HMG_REPLAY_TIME)
    if (
        '"ResultSet"' in body
        and '"list_meta"' in body
        and '"list"' in body
        and '"ticket"' in body
    ):
        body = _HMG_TICKET_OBJECT.sub(_sanitize_hmg_ticket_object, body)
    return body


def _is_hmg_board_html(body: str) -> bool:
    lowered = body.casefold()
    has_assets = "hmg-jb.css" in lowered and "combobo.js" in lowered
    inventory_form = (
        "jbsearchlist_form" in lowered
        and "list_posts" in lowered
        and "pid" in lowered
        and "gwt" in lowered
        and "/json/index.smpl" in lowered
    )
    search_entry = "jb_search" in lowered and "jb_search_results" in lowered
    return has_assets and (inventory_form or search_entry)


def _replace_hmg_input_value(body: str, key: str, replacement: str) -> str:
    body = re.sub(
        rf"(?is)(<input\b[^>]*\bname\s*=\s*[\"']{key}[\"'][^>]*"
        rf"\bvalue\s*=\s*[\"'])[^\"']*([\"'])",
        lambda match: f"{match.group(1)}{replacement}{match.group(2)}",
        body,
    )
    return re.sub(
        rf"(?is)(<input\b[^>]*\bvalue\s*=\s*[\"'])[^\"']*([\"'][^>]*"
        rf"\bname\s*=\s*[\"']{key}[\"'])",
        lambda match: f"{match.group(1)}{replacement}{match.group(2)}",
        body,
    )


def _sanitize_hmg_ticket_object(match: re.Match[str]) -> str:
    body = match.group(2)
    body = re.sub(
        r'(?is)("h"\s*:\s*")[^"]*(")',
        lambda item: f"{item.group(1)}{_HMG_REPLAY_HASH}{item.group(2)}",
        body,
    )
    body = re.sub(
        r'(?is)("t"\s*:\s*")[^"]*(")',
        lambda item: f"{item.group(1)}{_HMG_REPLAY_TIME}{item.group(2)}",
        body,
    )
    return f"{match.group(1)}{body}{match.group(3)}"


def is_public_location_state_field(
    mapping: dict,
    key: str,
    *,
    allow_redacted: bool = False,
) -> bool:
    """Distinguish public geographic state from OAuth/query state."""

    if not isinstance(mapping, dict) or _normalized_field_name(key) != "state":
        return False
    normalized_keys = {
        _normalized_field_name(str(item_key))
        for item_key in mapping
        if str(item_key) != key
    }
    if normalized_keys & _AUTH_CONTEXT_KEYS:
        return False
    if not normalized_keys & _LOCATION_CONTEXT_KEYS:
        return False
    value = mapping.get(key)
    if allow_redacted and value == "[REDACTED]":
        return True
    return bool(
        isinstance(value, str)
        and 1 <= len(value.strip()) <= 100
        and not any(character in value for character in "\r\n\t<>{}[]=&/?")
    )


def _normalized_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _protect_embedded_public_location_states(
    body: str,
) -> tuple[str, list[tuple[str, str]]]:
    if not isinstance(body, str) or '"state"' not in body.casefold():
        return body, []

    candidates: list[tuple[int, int, dict]] = []
    for start, end in _json_object_spans(body):
        if end - start > _MAX_EMBEDDED_JSON_OBJECT_CHARS:
            continue
        fragment = body[start:end]
        lowered = fragment.casefold()
        if '"state"' not in lowered:
            continue
        try:
            value = json.loads(fragment)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(value, dict) and _contains_public_location_state(value):
            candidates.append((start, end, value))

    selected: list[tuple[int, int, dict]] = []
    for candidate in sorted(candidates, key=lambda item: item[1] - item[0], reverse=True):
        start, end, _ = candidate
        if any(start >= chosen_start and end <= chosen_end for chosen_start, chosen_end, _ in selected):
            continue
        selected.append(candidate)

    protected: list[tuple[str, str]] = []
    rendered = body
    for start, end, value in sorted(selected, reverse=True):
        marked = _mark_public_location_state_keys(value, protected)
        replacement = json.dumps(marked, ensure_ascii=True, separators=(",", ":"))
        rendered = rendered[:start] + replacement + rendered[end:]
    return rendered, protected


def _json_object_spans(body: str):
    stack: list[int] = []
    in_string = False
    escaped = False
    for index, character in enumerate(body):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            stack.append(index)
        elif character == "}" and stack:
            yield stack.pop(), index + 1


def _contains_public_location_state(value) -> bool:
    if isinstance(value, dict):
        if any(is_public_location_state_field(value, str(key)) for key in value):
            return True
        return any(_contains_public_location_state(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_public_location_state(item) for item in value)
    return False


def _mark_public_location_state_keys(value, protected: list[tuple[str, str]]):
    if isinstance(value, dict):
        marked = {}
        for key, item in value.items():
            rendered_key = str(key)
            if is_public_location_state_field(value, rendered_key):
                marker = f"__JSA_PUBLIC_LOCATION_STATE_{len(protected)}__"
                protected.append((marker, rendered_key))
                rendered_key = marker
            marked[rendered_key] = _mark_public_location_state_keys(item, protected)
        return marked
    if isinstance(value, list):
        return [_mark_public_location_state_keys(item, protected) for item in value]
    return value


_PUBLIC_DOMAIN_REGISTRY_COLUMNS = (
    "Domain name",
    "Domain type",
    "Organization name",
    "Suborganization name",
    "City",
    "State",
    "Security contact email",
)


def _redact_public_domain_registry_contacts(body: str) -> str:
    if not isinstance(body, str) or not body.startswith("Domain name,Domain type,"):
        return body
    try:
        rows = list(csv.reader(io.StringIO(body, newline=""), strict=True))
    except csv.Error:
        return "PUBLIC DOMAIN REGISTRY SNAPSHOT REDACTED: MALFORMED CSV\n"
    if not rows or tuple(rows[0]) != _PUBLIC_DOMAIN_REGISTRY_COLUMNS:
        return "PUBLIC DOMAIN REGISTRY SNAPSHOT REDACTED: SCHEMA MISMATCH\n"
    if any(len(row) != len(_PUBLIC_DOMAIN_REGISTRY_COLUMNS) for row in rows[1:]):
        return "PUBLIC DOMAIN REGISTRY SNAPSHOT REDACTED: MALFORMED ROW\n"
    for row in rows[1:]:
        row[-1] = "[REDACTED]"
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(rows)
    return output.getvalue()


def sanitize_snapshot_source(source: object) -> str:
    """Reduce runtime diagnostics to a privacy-safe provenance label."""

    if not isinstance(source, str):
        return "fetch"
    candidate = source.strip().split("|", 1)[0]
    if ":" in candidate:
        candidate = candidate.split(":", 1)[0]
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._-")
    return candidate[:64] or "fetch"


def _is_sensitive_key(key: str) -> bool:
    return is_sensitive_key(key)


def _safe_path_part(part: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", part)
    return cleaned or "_"


def _capture_fields(capture: SnapshotRequestCapture) -> dict[str, object]:
    return {
        "snapshot_store_id": capture.snapshot_store_id,
        "scope_id": capture.scope_id,
        "capture_attempt_id": capture.capture_attempt_id,
        "execution_fingerprint": capture.execution_fingerprint,
        "stage": capture.stage,
        "request_ordinal": capture.request_ordinal,
    }


def _record_payload(record: SnapshotRecord | FetchFailureRecord) -> dict[str, object]:
    payload = record.__dict__.copy()
    if record.schema_version == 2:
        for field_name in (
            "snapshot_store_id",
            "scope_id",
            "capture_attempt_id",
            "execution_fingerprint",
            "stage",
            "request_ordinal",
        ):
            payload.pop(field_name)
    return payload


def _write_immutable_blob(path: Path, content: bytes, digest: str) -> None:
    if path.exists():
        if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Snapshot blob collision or unsafe path: {path}")
        return
    _write_bytes_atomic(path, content)


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _append_jsonl_durable(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
