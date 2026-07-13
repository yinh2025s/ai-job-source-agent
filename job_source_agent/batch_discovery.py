from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

LINKEDIN_MANIFEST_SCHEMA_VERSION = "1.0"
LINKEDIN_DISCOVERY_VERSION = "1.0"


class LinkedInDiscoveryManifestStore:
    """Freeze one dynamic LinkedIn cohort for deterministic batch resume."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def resolve(
        self,
        request: dict[str, Any],
        discover: Callable[[], list[dict[str, Any]]],
        *,
        refresh: bool = False,
    ) -> tuple[list[dict[str, Any]], str]:
        with self._lock():
            if not refresh:
                restored = self._load_unlocked(request)
                if restored is not None:
                    return restored, "restored"
            companies = discover()
            payload = {
                "schema_version": LINKEDIN_MANIFEST_SCHEMA_VERSION,
                "discovery_version": LINKEDIN_DISCOVERY_VERSION,
                "request": request,
                "companies": companies,
                "companies_sha256": _companies_digest(companies),
            }
            _validate_payload(payload, request)
            self._write_unlocked(payload)
            return companies, "refreshed" if refresh else "saved"

    def _load_unlocked(self, request: dict[str, Any]) -> list[dict[str, Any]] | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return _validate_payload(payload, request)
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def _write_unlocked(self, payload: dict[str, Any]) -> None:
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


def _validate_payload(payload: Any, request: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "discovery_version",
        "request",
        "companies",
        "companies_sha256",
    }:
        raise ValueError("LinkedIn discovery manifest has an unsupported shape")
    if payload["schema_version"] != LINKEDIN_MANIFEST_SCHEMA_VERSION:
        raise ValueError("LinkedIn discovery manifest schema is incompatible")
    if payload["discovery_version"] != LINKEDIN_DISCOVERY_VERSION or payload["request"] != request:
        raise ValueError("LinkedIn discovery manifest request is incompatible")
    companies = payload["companies"]
    if not isinstance(companies, list):
        raise ValueError("LinkedIn discovery manifest companies must be a list")
    if not all(isinstance(company, dict) for company in companies):
        raise ValueError("LinkedIn discovery manifest companies must be objects")
    if payload["companies_sha256"] != _companies_digest(companies):
        raise ValueError("LinkedIn discovery manifest company hash does not match")
    return companies


def _companies_digest(companies: list[dict[str, Any]]) -> str:
    encoded = json.dumps(companies, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
