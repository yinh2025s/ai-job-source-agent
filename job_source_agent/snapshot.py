from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .web import Page


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


@dataclass
class SnapshotRecord:
    request_url: str
    page_url: str
    final_url: str
    sanitized_url: str
    source: str
    path: str
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
        path = snapshot_path_for_url(self.fixtures_dir, sanitized_final_url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        record = SnapshotRecord(
            request_url=sanitize_url(request_url or page.url),
            page_url=sanitize_url(page.url),
            final_url=sanitized_final_url,
            sanitized_url=sanitized_final_url,
            source=page.source,
            path=str(path.relative_to(self.root_dir)),
            sha256=hashlib.sha256(encoded).hexdigest(),
            byte_count=len(encoded),
            captured_at_epoch=round(time.time(), 3),
        )
        self.root_dir.mkdir(parents=True, exist_ok=True)
        with self.index_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.__dict__, sort_keys=True) + "\n")
        return record


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


def snapshot_path_for_url(fixtures_dir: str | Path, url: str) -> Path:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [part for part in parsed.path.split("/") if part]
    base = Path(fixtures_dir) / host
    if not parts:
        return base / "index.html"
    candidate = base.joinpath(*[_safe_path_part(part) for part in parts])
    if candidate.suffix:
        return candidate
    return candidate / "index.html"


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
    for key in sorted(SENSITIVE_QUERY_KEYS):
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
