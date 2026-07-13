from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Protocol, runtime_checkable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import fcntl

from .web import safe_normalize_url


EVIDENCE_SCHEMA_VERSION = 1
DEFAULT_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
_SENSITIVE_QUERY_KEYS = {
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


@runtime_checkable
class LinkedInWebsiteEvidenceStore(Protocol):
    def load(self, company_name: str, linkedin_company_url: str) -> tuple[str, ...]:
        ...

    def save(
        self,
        company_name: str,
        linkedin_company_url: str,
        official_website_urls: tuple[str, ...],
    ) -> None:
        ...


class FilesystemLinkedInWebsiteEvidenceStore:
    """Atomic cache for public, company-scoped LinkedIn website evidence."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be finite and positive")
        self.path = Path(path)
        self.max_age_seconds = max_age_seconds
        self.clock = clock

    def load(self, company_name: str, linkedin_company_url: str) -> tuple[str, ...]:
        key = _evidence_key(company_name, linkedin_company_url)
        with self._lock():
            payload = self._read_payload()
            record = payload.get("records", {}).get(key)
        if not isinstance(record, dict):
            return ()
        try:
            observed_at = float(record["observed_at"])
        except (KeyError, TypeError, ValueError):
            return ()
        now = float(self.clock())
        if not math.isfinite(observed_at) or not math.isfinite(now):
            return ()
        age = now - observed_at
        if age < 0 or age > self.max_age_seconds:
            return ()
        if record.get("company_name") != _normalize_company_name(company_name):
            return ()
        if record.get("linkedin_company_url") != _normalize_linkedin_url(linkedin_company_url):
            return ()
        urls = record.get("official_website_urls")
        if not isinstance(urls, list):
            return ()
        normalized: list[str] = []
        for value in urls:
            if not isinstance(value, str):
                continue
            try:
                url = _normalize_public_url(value)
            except (TypeError, ValueError):
                continue
            if url not in normalized:
                normalized.append(url)
        return tuple(normalized)

    def save(
        self,
        company_name: str,
        linkedin_company_url: str,
        official_website_urls: tuple[str, ...],
    ) -> None:
        urls: list[str] = []
        for value in official_website_urls:
            try:
                url = _normalize_public_url(value)
            except (TypeError, ValueError):
                continue
            if url not in urls:
                urls.append(url)
        if not urls:
            return
        observed_at = float(self.clock())
        if not math.isfinite(observed_at):
            raise ValueError("clock must return a finite timestamp")
        key = _evidence_key(company_name, linkedin_company_url)
        with self._lock():
            payload = self._read_payload()
            records = payload.setdefault("records", {})
            records[key] = {
                "company_name": _normalize_company_name(company_name),
                "linkedin_company_url": _normalize_linkedin_url(linkedin_company_url),
                "official_website_urls": urls,
                "observed_at": observed_at,
            }
            self._write_payload(payload)

    def _read_payload(self) -> dict:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return {"schema_version": EVIDENCE_SCHEMA_VERSION, "records": {}}
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != EVIDENCE_SCHEMA_VERSION
            or not isinstance(payload.get("records"), dict)
        ):
            return {"schema_version": EVIDENCE_SCHEMA_VERSION, "records": {}}
        return payload

    def _write_payload(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = handle.name
                json.dump(payload, handle, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            _fsync_directory(self.path.parent)
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    @contextmanager
    def _lock(self) -> Iterator[None]:
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _evidence_key(company_name: str, linkedin_company_url: str) -> str:
    identity = json.dumps(
        [_normalize_company_name(company_name), _normalize_linkedin_url(linkedin_company_url)],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _normalize_company_name(company_name: str) -> str:
    normalized = " ".join(str(company_name).casefold().split())
    if not normalized:
        raise ValueError("company_name must not be empty")
    return normalized


def _normalize_linkedin_url(linkedin_company_url: str) -> str:
    normalized = _normalize_public_url(linkedin_company_url)
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").casefold()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        raise ValueError("linkedin_company_url must use a LinkedIn host")
    if not re.fullmatch(r"/company/[^/]+/?", parsed.path):
        raise ValueError("linkedin_company_url must identify a company page")
    return urlunparse(("https", "www.linkedin.com", parsed.path.rstrip("/"), "", "", ""))


def _normalize_public_url(value: str) -> str:
    normalized = safe_normalize_url(value)
    if normalized is None:
        raise ValueError("evidence URL must be an absolute HTTP(S) URL")
    parsed = urlparse(normalized)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("evidence URL has an invalid port") from exc
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("evidence URL must not contain credentials")
    if port not in {None, 80, 443}:
        raise ValueError("evidence URL must use a standard HTTP(S) port")
    safe_query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() not in _SENSITIVE_QUERY_KEYS
        ],
        doseq=True,
    )
    return urlunparse(parsed._replace(query=safe_query))


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
