from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import fcntl

from .web import Page, fixture_path_candidates


SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
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


class SnapshotStore:
    """Persist fetched pages as sanitized, fixture-compatible snapshots."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.fixtures_dir = self.root_dir / "sites"
        self.index_path = self.root_dir / "snapshots.jsonl"

    def write_page(self, page: Page, request_url: str | None = None) -> SnapshotRecord:
        sanitized_final_url = sanitize_url(page.final_url or page.url)
        html = sanitize_snapshot_body(page.html)
        encoded = html.encode("utf-8")
        self.root_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(encoded).hexdigest()
        path = snapshot_path_for_url(self.fixtures_dir, sanitized_final_url)
        blob_path = snapshot_blob_path(self.root_dir, digest)
        with self._write_lock():
            _write_immutable_blob(blob_path, encoded, digest)
            _write_bytes_atomic(path, encoded)
            artifact_paths, artifact_blob_paths = self._write_artifacts(page, sanitized_final_url)
            record = SnapshotRecord(
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
            with self.index_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.__dict__, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return record

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
    """Wrap a fetcher and record every successful response as a snapshot."""

    def __init__(self, fetcher, snapshot_dir: str | Path) -> None:
        self.fetcher = fetcher
        self.snapshot_store = SnapshotStore(snapshot_dir)
        self.timeout = getattr(fetcher, "timeout", None)

    def fetch(self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> Page:
        page = self.fetcher.fetch(url, data=data, headers=headers)
        record = self.snapshot_store.write_page(page, request_url=url)
        page.source = f"{page.source}|snapshot:{record.path}"
        return page

    def __getattr__(self, name: str):
        return getattr(self.fetcher, name)


def snapshot_path_for_url(fixtures_dir: str | Path, url: str) -> Path:
    return fixture_path_candidates(fixtures_dir, url)[0]


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


def sanitize_url(url: str) -> str:
    parsed = urlparse(url)
    query = urlencode(
        [
            (key, _redacted_value(value) if _is_sensitive_key(key) else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ],
        doseq=True,
    )
    return urlunparse(parsed._replace(query=query, fragment=""))


def sanitize_snapshot_body(body: str) -> str:
    redacted = re.sub(
        r"(?i)(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._~+/=-]{8,}",
        r"\1[REDACTED]",
        body,
    )
    for key in sorted(SENSITIVE_BODY_FIELDS):
        redacted = re.sub(
            rf"(?i)([\"']{re.escape(key)}[\"']\s*:\s*)([\"'])[^\"']*(\2)",
            rf"\1\2[REDACTED]\3",
            redacted,
        )
        redacted = re.sub(
            rf"(?i)({re.escape(key)}\s*[=:]\s*)([\"']?)[^\"'&\s<>,;]+(\2)",
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
    lowered = key.lower()
    return lowered in SENSITIVE_QUERY_KEYS or any(marker in lowered for marker in ("token", "secret", "password"))


def _redacted_value(value: str) -> str:
    return "[REDACTED]" if value else value


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
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
