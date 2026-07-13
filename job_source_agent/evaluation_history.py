from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

import fcntl

from .evaluation import compare_summaries


HISTORY_SCHEMA_VERSION = "1.0"
_RUN_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{8}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class EvaluationHistoryError(RuntimeError):
    pass


class CorruptEvaluationHistoryError(EvaluationHistoryError):
    pass


@dataclass(frozen=True)
class EvaluationRun:
    run_id: str
    created_at: str
    summary_sha256: str
    summary: dict[str, Any]
    baseline_run_id: str | None
    regression: dict[str, Any] | None
    label: str | None
    metadata: dict[str, str]

    @property
    def cohort_identity(self) -> dict[str, str] | None:
        return derive_cohort_identity(self.summary, metadata=self.metadata)


@dataclass(frozen=True)
class HistoryScan:
    runs: tuple[EvaluationRun, ...]
    skipped: tuple[dict[str, str], ...]


class EvaluationHistory:
    """Atomic, content-addressed storage for evaluator summary history."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def archive(
        self,
        summary: dict[str, Any],
        *,
        label: str | None = None,
        metadata: dict[str, str] | None = None,
        compare_with_latest: bool = True,
    ) -> EvaluationRun:
        summary_bytes = _canonical_json(summary)
        archived_summary = json.loads(summary_bytes)
        digest = hashlib.sha256(summary_bytes).hexdigest()
        created = datetime.now(timezone.utc)
        created_at = created.isoformat(timespec="microseconds").replace("+00:00", "Z")
        timestamp = created.strftime("%Y%m%dT%H%M%S.%fZ")
        run_id = f"{timestamp}-{digest[:12]}-{uuid.uuid4().hex[:8]}"

        with self._lock():
            manifest = self._load_manifest(allow_missing=True)
            validated_metadata = _validate_metadata(metadata)
            cohort_identity = derive_cohort_identity(archived_summary, metadata=validated_metadata)
            baseline = None
            if compare_with_latest:
                baseline = self._latest_compatible_run(manifest["runs"], cohort_identity)
            if baseline is not None:
                regression = compare_summaries(archived_summary, baseline.summary)
            elif compare_with_latest and manifest["runs"]:
                regression = {"comparison_status": "no_compatible_baseline"}
            else:
                regression = None
            run = EvaluationRun(
                run_id=run_id,
                created_at=created_at,
                summary_sha256=digest,
                summary=archived_summary,
                baseline_run_id=baseline.run_id if baseline else None,
                regression=regression,
                label=_validate_label(label),
                metadata=validated_metadata,
            )
            self._publish_object(digest, summary_bytes)
            _write_json_atomic(self._managed_path("runs", f"{run_id}.json"), _run_payload(run, include_summary=False))
            manifest["runs"].append(run_id)
            manifest["latest_run_id"] = run_id
            _write_json_atomic(self._managed_path("manifest.json"), manifest)
            return run

    def _latest_compatible_run(
        self,
        run_ids: list[str],
        cohort_identity: dict[str, str] | None,
    ) -> EvaluationRun | None:
        for run_id in reversed(run_ids):
            candidate = self._load_run(run_id)
            if cohort_identities_compatible(candidate.cohort_identity, cohort_identity):
                return candidate
        return None

    def latest(self) -> EvaluationRun | None:
        with self._lock():
            manifest = self._load_manifest(allow_missing=True)
            run_id = manifest["latest_run_id"]
            return self._load_run(run_id) if run_id else None

    def load(self, run_id: str) -> EvaluationRun:
        _validate_run_id(run_id)
        with self._lock():
            return self._load_run(run_id)

    def scan(self, *, on_corrupt: Literal["error", "skip"] = "error") -> HistoryScan:
        if on_corrupt not in {"error", "skip"}:
            raise ValueError("on_corrupt must be 'error' or 'skip'")
        with self._lock():
            manifest = self._load_manifest(allow_missing=True)
            runs: list[EvaluationRun] = []
            skipped: list[dict[str, str]] = []
            for run_id in manifest["runs"]:
                try:
                    runs.append(self._load_run(run_id))
                except CorruptEvaluationHistoryError as error:
                    if on_corrupt == "error":
                        raise
                    skipped.append({"run_id": run_id, "error": str(error)})
            return HistoryScan(tuple(runs), tuple(skipped))

    def _publish_object(self, digest: str, content: bytes) -> None:
        path = self._managed_path("objects", digest[:2], f"{digest}.json")
        if path.exists():
            try:
                existing = path.read_bytes()
            except OSError as error:
                raise CorruptEvaluationHistoryError(f"Cannot read summary object {digest}: {error}") from error
            if existing != content or hashlib.sha256(existing).hexdigest() != digest:
                raise CorruptEvaluationHistoryError(f"Summary object {digest} does not match its content address")
            return
        _write_bytes_atomic(path, content)

    def _load_run(self, run_id: str) -> EvaluationRun:
        _validate_run_id(run_id)
        payload = _read_json(self._managed_path("runs", f"{run_id}.json"), f"run {run_id}")
        if not isinstance(payload, dict) or set(payload) != {
            "history_schema_version", "run_id", "created_at", "summary_sha256",
            "baseline_run_id", "regression", "label",
            "metadata",
        }:
            raise CorruptEvaluationHistoryError(f"Run {run_id} has an invalid envelope")
        if payload["history_schema_version"] != HISTORY_SCHEMA_VERSION or payload["run_id"] != run_id:
            raise CorruptEvaluationHistoryError(f"Run {run_id} has incompatible identity or schema")
        digest = payload["summary_sha256"]
        if not isinstance(digest, str) or not _DIGEST_PATTERN.fullmatch(digest):
            raise CorruptEvaluationHistoryError(f"Run {run_id} has an invalid summary digest")
        baseline_run_id = payload["baseline_run_id"]
        if baseline_run_id is not None:
            try:
                _validate_run_id(baseline_run_id)
            except ValueError as error:
                raise CorruptEvaluationHistoryError(f"Run {run_id} has an invalid baseline reference") from error
        object_path = self._managed_path("objects", digest[:2], f"{digest}.json")
        try:
            content = object_path.read_bytes()
        except (OSError, UnicodeError) as error:
            raise CorruptEvaluationHistoryError(f"Cannot read summary object {digest}: {error}") from error
        if hashlib.sha256(content).hexdigest() != digest:
            raise CorruptEvaluationHistoryError(f"Summary object {digest} failed content verification")
        try:
            summary = json.loads(content)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise CorruptEvaluationHistoryError(f"Summary object {digest} is invalid JSON") from error
        if not isinstance(summary, dict):
            raise CorruptEvaluationHistoryError(f"Summary object {digest} must contain an object")
        if not isinstance(payload["created_at"], str):
            raise CorruptEvaluationHistoryError(f"Run {run_id} has an invalid timestamp")
        if payload["regression"] is not None and not isinstance(payload["regression"], dict):
            raise CorruptEvaluationHistoryError(f"Run {run_id} has an invalid regression")
        if payload["label"] is not None and not isinstance(payload["label"], str):
            raise CorruptEvaluationHistoryError(f"Run {run_id} has an invalid label")
        try:
            metadata = _validate_metadata(payload["metadata"])
        except ValueError as error:
            raise CorruptEvaluationHistoryError(f"Run {run_id} has invalid metadata") from error
        return EvaluationRun(
            run_id,
            payload["created_at"],
            digest,
            summary,
            baseline_run_id,
            payload["regression"],
            payload["label"],
            metadata,
        )

    def _load_manifest(self, *, allow_missing: bool) -> dict[str, Any]:
        path = self._managed_path("manifest.json")
        if not path.exists() and allow_missing:
            return {"history_schema_version": HISTORY_SCHEMA_VERSION, "latest_run_id": None, "runs": []}
        payload = _read_json(path, "history manifest")
        if not isinstance(payload, dict) or set(payload) != {"history_schema_version", "latest_run_id", "runs"}:
            raise CorruptEvaluationHistoryError("History manifest has an invalid envelope")
        if payload["history_schema_version"] != HISTORY_SCHEMA_VERSION or not isinstance(payload["runs"], list):
            raise CorruptEvaluationHistoryError("History manifest has an incompatible schema")
        try:
            for run_id in payload["runs"]:
                _validate_run_id(run_id)
            if payload["latest_run_id"] is not None:
                _validate_run_id(payload["latest_run_id"])
        except (TypeError, ValueError) as error:
            raise CorruptEvaluationHistoryError("History manifest contains an invalid run id") from error
        if len(set(payload["runs"])) != len(payload["runs"]):
            raise CorruptEvaluationHistoryError("History manifest contains duplicate runs")
        if payload["latest_run_id"] != (payload["runs"][-1] if payload["runs"] else None):
            raise CorruptEvaluationHistoryError("History manifest latest pointer is inconsistent")
        return payload

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self._assert_safe_root()
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self._managed_path(".history.lock")
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _assert_safe_root(self) -> None:
        if self.root.exists() and self.root.is_symlink():
            raise EvaluationHistoryError("Evaluation history root must not be a symlink")

    def _managed_path(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        current = self.root
        for part in parts[:-1]:
            current = current / part
            if current.exists() and current.is_symlink():
                raise EvaluationHistoryError(f"Managed history directory must not be a symlink: {current}")
        if path.exists() and path.is_symlink():
            raise EvaluationHistoryError(f"Managed history file must not be a symlink: {path}")
        return path


def _run_payload(run: EvaluationRun, *, include_summary: bool) -> dict[str, Any]:
    payload = {
        "history_schema_version": HISTORY_SCHEMA_VERSION,
        "run_id": run.run_id,
        "created_at": run.created_at,
        "summary_sha256": run.summary_sha256,
        "baseline_run_id": run.baseline_run_id,
        "regression": run.regression,
        "label": run.label,
        "metadata": run.metadata,
    }
    if include_summary:
        payload["summary"] = run.summary
    return payload


def run_record(run: EvaluationRun) -> dict[str, Any]:
    payload = _run_payload(run, include_summary=True)
    payload["cohort_identity"] = run.cohort_identity
    return payload


def derive_cohort_identity(
    summary: dict[str, Any],
    *,
    metadata: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """Return stable comparison identity without relying on machine-local paths."""
    manifest = _summary_manifest(summary)
    identity: dict[str, str] = {}
    companies_identity = _identity_value(manifest, "companies_sha256")
    identity_key = "companies_sha256"
    if companies_identity is None:
        companies_identity = _identity_value(manifest, "input_identity", "input_sha256")
        identity_key = "input_identity"
    if companies_identity is None:
        companies_identity = _nested_identity_value(manifest, "input")
        identity_key = "input_identity"
    if companies_identity is None:
        companies_identity = _first_metadata_value(metadata, "cohort_companies_sha256")
    if companies_identity is None:
        companies_identity = _first_metadata_value(metadata, "cohort_input_sha256")
        identity_key = "input_identity"
    if companies_identity is not None:
        identity[identity_key] = companies_identity

    expectations_identity = _identity_value(manifest, "expectations_identity", "expectations_sha256")
    if expectations_identity is None:
        expectations_identity = _nested_identity_value(manifest, "expectations")
    if expectations_identity is None:
        expectations_identity = _first_metadata_value(metadata, "cohort_expectations_sha256")
    if expectations_identity is not None:
        identity["expectations_identity"] = expectations_identity
    return identity or None


def _summary_manifest(summary: dict[str, Any]) -> dict[str, Any]:
    for key in ("evaluation_manifest", "summary_manifest", "cohort_manifest", "manifest"):
        value = summary.get(key)
        if isinstance(value, dict):
            return value
    cohort_identity = summary.get("cohort_identity")
    return cohort_identity if isinstance(cohort_identity, dict) else {}


def _identity_value(container: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = container.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _nested_identity_value(container: dict[str, Any], key: str) -> str | None:
    value = container.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if not isinstance(value, dict):
        return None
    direct = _identity_value(value, "identity", "sha256", "content_sha256", "fingerprint")
    if direct is not None:
        return direct
    try:
        encoded = _canonical_json(value)
    except ValueError:
        return None
    return hashlib.sha256(encoded).hexdigest()


def _first_metadata_value(metadata: dict[str, str] | None, *keys: str) -> str | None:
    if metadata is None:
        return None
    for key in keys:
        value = metadata.get(key)
        if value:
            return value
    return None


def cohort_identities_compatible(
    left: dict[str, str] | None,
    right: dict[str, str] | None,
) -> bool:
    if left is None or right is None:
        return left is right
    left_primary = left.get("companies_sha256") or left.get("input_identity")
    right_primary = right.get("companies_sha256") or right.get("input_identity")
    return (
        left_primary is not None
        and left_primary == right_primary
        and left.get("expectations_identity") == right.get("expectations_identity")
    )


def _canonical_json(value: Any) -> bytes:
    if not isinstance(value, dict):
        raise ValueError("Evaluation summary must be a JSON object")
    try:
        return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("Evaluation summary must contain finite JSON values") from error


def _validate_run_id(run_id: Any) -> None:
    if not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Invalid evaluation run id")


def _validate_label(label: str | None) -> str | None:
    if label is None:
        return None
    if not isinstance(label, str) or not label.strip() or len(label) > 200:
        raise ValueError("Evaluation label must be 1-200 non-blank characters")
    return label.strip()


def _validate_metadata(metadata: dict[str, str] | None) -> dict[str, str]:
    if metadata is None:
        return {}
    if not isinstance(metadata, dict) or len(metadata) > 32:
        raise ValueError("Evaluation metadata must be an object with at most 32 entries")
    validated: dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
            raise ValueError("Evaluation metadata keys must be lowercase identifiers")
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError("Evaluation metadata values must be 1-2000 non-blank characters")
        validated[key] = value.strip()
    return validated


def _read_json(path: Path, description: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CorruptEvaluationHistoryError(f"Cannot read {description}: {error}") from error


def _write_json_atomic(path: Path, payload: Any) -> None:
    _write_bytes_atomic(path, _canonical_json(payload))


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
