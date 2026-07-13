from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import fcntl

from .reasons import classify_fetch_error, reason_spec
from .request_identity import build_request_identity, is_sensitive_key, sanitize_url
from .web import FetchError, Page, fixture_path_candidates


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
    "protectedSessionJWT",
    "sessionCSRFToken",
    "sessionJWT",
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


@dataclass
class FetchFailureRecord:
    schema_version: int
    kind: str
    sequence: int
    request: dict
    failure: dict
    captured_at_epoch: float
    terminal: bool


class SnapshotStore:
    """Persist fetched pages as sanitized, fixture-compatible snapshots."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.fixtures_dir = self.root_dir / "sites"
        self.index_path = self.root_dir / "snapshots.jsonl"
        self.failure_index_path = self.root_dir / "fetch-failures.jsonl"
        self.sequence_path = self.root_dir / ".snapshot-sequence"

    def write_page(
        self,
        page: Page,
        request_url: str | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> SnapshotRecord:
        request_identity = build_request_identity(
            request_url or page.url,
            data=data,
            headers=headers,
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
                schema_version=2,
                kind="page",
                sequence=self._next_sequence(),
                request=request_identity.as_dict(),
                request_url=sanitize_url(request_url or page.url),
                page_url=sanitize_url(page.url),
                final_url=sanitized_final_url,
                sanitized_url=sanitized_final_url,
                source=page.source,
                path=str(path.relative_to(self.root_dir)),
                blob_path=str(blob_path.relative_to(self.root_dir)),
                artifact_paths=artifact_paths,
                artifact_blob_paths=artifact_blob_paths,
                sha256=digest,
                byte_count=len(encoded),
                captured_at_epoch=round(time.time(), 3),
            )
            _append_jsonl_durable(self.index_path, record.__dict__)
        return record

    def write_failure(
        self,
        error: FetchError,
        request_url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> FetchFailureRecord:
        request_identity = build_request_identity(request_url, data=data, headers=headers)
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
                schema_version=2,
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
            )
            _append_jsonl_durable(self.failure_index_path, record.__dict__)
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

    def __init__(self, fetcher, snapshot_dir: str | Path) -> None:
        self.fetcher = fetcher
        self.snapshot_store = SnapshotStore(snapshot_dir)
        self.timeout = getattr(fetcher, "timeout", None)

    def fetch(self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Page:
        try:
            page = self.fetcher.fetch(url, data=data, headers=headers)
        except FetchError as error:
            self.snapshot_store.write_failure(error, url, data=data, headers=headers)
            raise
        record = self.snapshot_store.write_page(
            page,
            request_url=url,
            data=data,
            headers=headers,
        )
        page.source = f"{page.source}|snapshot:{record.path}"
        return page

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
        redacted = re.sub(
            rf"(?i)([\"']{re.escape(key)}[\"']\s*:\s*)([\"'])[^\"']*(\2)",
            rf"\1\2[REDACTED]\3",
            redacted,
        )
        redacted = re.sub(
            rf"(?i)((?<![A-Za-z0-9_$]){re.escape(key)}\s*[=:]\s*)([\"']?)[^\"'&\s<>,;]+(\2)",
            rf"\1\2[REDACTED]\3",
            redacted,
        )
        redacted = re.sub(
            rf"(?i)(<input\b[^>]*(?:id|name)\s*=\s*[\"']{re.escape(key)}[\"'][^>]*"
            rf"\bvalue\s*=\s*[\"'])[^\"']*([\"'])",
            rf"\1[REDACTED]\2",
            redacted,
        )
        redacted = re.sub(
            rf"(?i)(<input\b[^>]*\bvalue\s*=\s*[\"'])[^\"']*([\"'][^>]*"
            rf"(?:id|name)\s*=\s*[\"']{re.escape(key)}[\"'])",
            rf"\1[REDACTED]\2",
            redacted,
        )
        redacted = re.sub(
            rf"(?i)(<meta\b[^>]*(?:id|name|property)\s*=\s*[\"']{re.escape(key)}[\"'][^>]*"
            rf"\bcontent\s*=\s*[\"'])[^\"']*([\"'])",
            rf"\1[REDACTED]\2",
            redacted,
        )
        redacted = re.sub(
            rf"(?i)(<meta\b[^>]*\bcontent\s*=\s*[\"'])[^\"']*([\"'][^>]*"
            rf"(?:id|name|property)\s*=\s*[\"']{re.escape(key)}[\"'])",
            rf"\1[REDACTED]\2",
            redacted,
        )
    redacted = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1[REDACTED]", redacted)
    return redacted


def _is_sensitive_key(key: str) -> bool:
    return is_sensitive_key(key)


def _safe_path_part(part: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", part)
    return cleaned or "_"


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
