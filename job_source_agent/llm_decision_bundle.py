from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .candidate_reasoning_contracts import (
    LLM_DECISION_SCHEMA_VERSION,
    LLMDecisionKey,
    LLMDecisionRecord,
    LLMDecisionStore,
)
from .llm_decision_store import (
    LLM_DECISION_STORE_SCHEMA_VERSION,
    StrictReplayLLMDecisionStore,
    deserialize_llm_decision_record,
    serialize_llm_decision_record,
)


LLM_DECISION_BUNDLE_SCHEMA_VERSION = "1"
LLM_DECISIONS_FILENAME = "llm-decisions.jsonl"
LLM_DECISION_MANIFEST_FILENAME = "llm-decision-manifest.json"

LLM_DECISION_BUNDLE_MISSING = "LLM_DECISION_BUNDLE_MISSING"
LLM_DECISION_BUNDLE_EXTRA = "LLM_DECISION_BUNDLE_EXTRA"
LLM_DECISION_BUNDLE_CORRUPT = "LLM_DECISION_BUNDLE_CORRUPT"
LLM_DECISION_BUNDLE_INCOMPATIBLE = "LLM_DECISION_BUNDLE_INCOMPATIBLE"

_MANIFEST_FIELDS = {
    "schema_version",
    "decision_store_schema_version",
    "decision_schema_version",
    "execution_identity",
    "run_configuration_digest",
    "llm_provider",
    "model_id",
    "prompt_version",
    "adapter_version",
    "record_count",
    "record_keys",
    "decisions_sha256",
}
_PUBLIC_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


class LLMDecisionBundleError(ValueError):
    code = LLM_DECISION_BUNDLE_CORRUPT

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class LLMDecisionBundleMissing(LLMDecisionBundleError):
    code = LLM_DECISION_BUNDLE_MISSING


class LLMDecisionBundleExtra(LLMDecisionBundleError):
    code = LLM_DECISION_BUNDLE_EXTRA


class LLMDecisionBundleCorrupt(LLMDecisionBundleError):
    code = LLM_DECISION_BUNDLE_CORRUPT


class LLMDecisionBundleIncompatible(LLMDecisionBundleError):
    code = LLM_DECISION_BUNDLE_INCOMPATIBLE


class AuditedLLMDecisionStore:
    """Capture every live decision used by a run into a digest-bound artifact pair."""

    def __init__(
        self,
        root: str | Path,
        *,
        execution_identity: str,
        run_configuration_digest: str,
        llm_provider: str,
        model_id: str,
        prompt_version: str,
        adapter_version: str,
        delegate: LLMDecisionStore | None = None,
    ) -> None:
        self.root = Path(root)
        self._identity = _ExpectedIdentity(
            execution_identity,
            run_configuration_digest,
            llm_provider,
            model_id,
            prompt_version,
            adapter_version,
        )
        self._delegate = delegate

    def load(self, key: LLMDecisionKey) -> LLMDecisionRecord | None:
        record = self._delegate.load(key) if self._delegate is not None else None
        if record is not None:
            self._capture(record)
        return record

    def save(self, record: LLMDecisionRecord) -> None:
        self._capture(record)
        if self._delegate is not None:
            self._delegate.save(record)

    def _capture(self, record: LLMDecisionRecord) -> None:
        _validate_record_identity(record, self._identity)
        with _artifact_lock(self.root):
            existing, _ = _load_artifacts(
                self.root,
                expected=self._identity,
                allow_missing=True,
            )
            by_key = {item.record_key: item for item in existing}
            prior = by_key.get(record.record_key)
            if prior is not None and serialize_llm_decision_record(prior) != serialize_llm_decision_record(record):
                raise LLMDecisionBundleCorrupt("a record key was reused with different content")
            by_key[record.record_key] = record
            _write_artifacts(self.root, tuple(by_key[key] for key in sorted(by_key)), self._identity)


def freeze_llm_decision_fixture(
    source_root: str | Path,
    bundle_root: str | Path,
    *,
    selected_input_evidence_digests: Iterable[str],
    execution_identity: str,
    run_configuration_digest: str,
    llm_provider: str,
    model_id: str,
    prompt_version: str,
    adapter_version: str,
) -> dict[str, Any]:
    """Copy only selected invocation decisions into a self-contained replay bundle."""
    expected = _ExpectedIdentity(
        execution_identity,
        run_configuration_digest,
        llm_provider,
        model_id,
        prompt_version,
        adapter_version,
    )
    records, _ = _load_artifacts(Path(source_root), expected=expected)
    selected = frozenset(selected_input_evidence_digests)
    if any(not _is_sha256(item) for item in selected):
        raise LLMDecisionBundleCorrupt("selected input evidence digest is invalid")
    filtered = tuple(
        record
        for record in records
        if _invocation_input_digest(record) in selected
    )
    root = Path(bundle_root)
    _write_artifacts(root, filtered, expected)
    manifest = _read_manifest(root / LLM_DECISION_MANIFEST_FILENAME)
    return {
        "status": "frozen",
        "record_count": len(filtered),
        "decisions_path": LLM_DECISIONS_FILENAME,
        "manifest_path": LLM_DECISION_MANIFEST_FILENAME,
        "decisions_sha256": manifest["decisions_sha256"],
        "manifest_sha256": _sha256_bytes(
            (root / LLM_DECISION_MANIFEST_FILENAME).read_bytes()
        ),
        "execution_identity": execution_identity,
    }


def load_llm_decision_fixture(
    root: str | Path,
    *,
    execution_identity: str,
    run_configuration_digest: str,
    llm_provider: str,
    model_id: str,
    prompt_version: str,
    adapter_version: str,
) -> tuple[StrictReplayLLMDecisionStore, dict[str, Any]]:
    expected = _ExpectedIdentity(
        execution_identity,
        run_configuration_digest,
        llm_provider,
        model_id,
        prompt_version,
        adapter_version,
    )
    records, manifest = _load_artifacts(Path(root), expected=expected)
    keys = tuple(record.key for record in records)
    return StrictReplayLLMDecisionStore(records, expected_keys=keys), manifest


def inspect_llm_decision_fixture(root: str | Path) -> dict[str, Any]:
    """Validate a source artifact pair and return its public manifest identity."""
    path = Path(root) / LLM_DECISION_MANIFEST_FILENAME
    if not path.exists():
        raise LLMDecisionBundleMissing("decision manifest is missing")
    if path.is_symlink():
        raise LLMDecisionBundleCorrupt("decision manifest cannot be a symlink")
    try:
        manifest = _read_manifest(path)
        expected = _ExpectedIdentity(
            manifest["execution_identity"],
            manifest["run_configuration_digest"],
            manifest["llm_provider"],
            manifest["model_id"],
            manifest["prompt_version"],
            manifest["adapter_version"],
        )
        _, validated = _load_artifacts(Path(root), expected=expected)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, LLMDecisionBundleError):
            raise
        raise LLMDecisionBundleCorrupt(str(exc)) from exc
    assert validated is not None
    return dict(validated)


class _ExpectedIdentity:
    def __init__(
        self,
        execution_identity: str,
        run_configuration_digest: str,
        llm_provider: str,
        model_id: str,
        prompt_version: str,
        adapter_version: str,
    ) -> None:
        if not _is_sha256(execution_identity) or not _is_sha256(run_configuration_digest):
            raise LLMDecisionBundleCorrupt("execution and run configuration identities must be SHA-256")
        for label, value in (
            ("llm_provider", llm_provider),
            ("model_id", model_id),
            ("prompt_version", prompt_version),
            ("adapter_version", adapter_version),
        ):
            if (
                not isinstance(value, str)
                or len(value) > 128
                or _PUBLIC_IDENTIFIER.fullmatch(value) is None
            ):
                raise LLMDecisionBundleCorrupt(f"{label} is invalid")
        self.execution_identity = execution_identity
        self.run_configuration_digest = run_configuration_digest
        self.llm_provider = llm_provider
        self.model_id = model_id
        self.prompt_version = prompt_version
        self.adapter_version = adapter_version

    def manifest(self, records: tuple[LLMDecisionRecord, ...], decisions_sha256: str) -> dict[str, Any]:
        return {
            "schema_version": LLM_DECISION_BUNDLE_SCHEMA_VERSION,
            "decision_store_schema_version": LLM_DECISION_STORE_SCHEMA_VERSION,
            "decision_schema_version": LLM_DECISION_SCHEMA_VERSION,
            "execution_identity": self.execution_identity,
            "run_configuration_digest": self.run_configuration_digest,
            "llm_provider": self.llm_provider,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "adapter_version": self.adapter_version,
            "record_count": len(records),
            "record_keys": [record.record_key for record in records],
            "decisions_sha256": decisions_sha256,
        }


def _load_artifacts(
    root: Path,
    *,
    expected: _ExpectedIdentity,
    allow_missing: bool = False,
) -> tuple[tuple[LLMDecisionRecord, ...], dict[str, Any] | None]:
    decisions_path = root / LLM_DECISIONS_FILENAME
    manifest_path = root / LLM_DECISION_MANIFEST_FILENAME
    if not decisions_path.exists() and not manifest_path.exists() and allow_missing:
        return (), None
    if not decisions_path.exists() or not manifest_path.exists():
        raise LLMDecisionBundleMissing("decision JSONL or manifest is missing")
    if decisions_path.is_symlink() or manifest_path.is_symlink():
        raise LLMDecisionBundleCorrupt("decision artifacts cannot be symlinks")
    try:
        encoded = decisions_path.read_bytes()
        manifest = _read_manifest(manifest_path)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, LLMDecisionBundleError):
            raise
        raise LLMDecisionBundleCorrupt(str(exc)) from exc
    _validate_manifest_identity(manifest, expected)
    if manifest["decisions_sha256"] != _sha256_bytes(encoded):
        raise LLMDecisionBundleCorrupt("decision JSONL digest does not match manifest")
    records: list[LLMDecisionRecord] = []
    try:
        text = encoded.decode("utf-8")
        for line in text.splitlines():
            if not line.strip():
                raise LLMDecisionBundleCorrupt("decision JSONL contains an empty line")
            records.append(deserialize_llm_decision_record(json.loads(line)))
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, LLMDecisionBundleError):
            raise
        raise LLMDecisionBundleCorrupt(str(exc)) from exc
    record_keys = [record.record_key for record in records]
    manifest_keys = manifest["record_keys"]
    if len(record_keys) > len(manifest_keys) or any(key not in manifest_keys for key in record_keys):
        raise LLMDecisionBundleExtra("decision JSONL contains a record absent from the manifest")
    if len(record_keys) < len(manifest_keys) or any(key not in record_keys for key in manifest_keys):
        raise LLMDecisionBundleMissing("manifest references a missing decision record")
    if record_keys != manifest_keys or manifest["record_count"] != len(records):
        raise LLMDecisionBundleCorrupt("decision order or count does not match manifest")
    if len(set(record_keys)) != len(record_keys):
        raise LLMDecisionBundleExtra("decision JSONL contains duplicate record keys")
    for record in records:
        _validate_record_identity(record, expected)
    return tuple(records), manifest


def _read_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != _MANIFEST_FIELDS:
        raise LLMDecisionBundleCorrupt("decision manifest fields are invalid")
    if not isinstance(payload["record_count"], int) or isinstance(payload["record_count"], bool) or payload["record_count"] < 0:
        raise LLMDecisionBundleCorrupt("decision manifest record_count is invalid")
    keys = payload["record_keys"]
    if not isinstance(keys, list) or any(not _is_sha256(item) for item in keys):
        raise LLMDecisionBundleCorrupt("decision manifest record keys are invalid")
    if not _is_sha256(payload["decisions_sha256"]):
        raise LLMDecisionBundleCorrupt("decision manifest digest is invalid")
    return payload


def _validate_manifest_identity(manifest: dict[str, Any], expected: _ExpectedIdentity) -> None:
    wanted = expected.manifest((), manifest["decisions_sha256"])
    for field in (
        "schema_version",
        "decision_store_schema_version",
        "decision_schema_version",
        "execution_identity",
        "run_configuration_digest",
        "llm_provider",
        "model_id",
        "prompt_version",
        "adapter_version",
    ):
        if manifest[field] != wanted[field]:
            raise LLMDecisionBundleIncompatible(f"decision manifest {field} is incompatible")


def _validate_record_identity(record: LLMDecisionRecord, expected: _ExpectedIdentity) -> None:
    if not isinstance(record, LLMDecisionRecord):
        raise LLMDecisionBundleCorrupt("artifact contains an invalid decision record")
    if record.execution_fingerprint != expected.execution_identity:
        raise LLMDecisionBundleIncompatible("decision execution identity is incompatible")
    for field, actual, wanted in (
        ("provider", record.key.llm_provider, expected.llm_provider),
        ("model", record.key.model_id, expected.model_id),
        ("prompt", record.key.prompt_version, expected.prompt_version),
        ("adapter", record.key.adapter_version, expected.adapter_version),
    ):
        if actual != wanted:
            raise LLMDecisionBundleIncompatible(f"decision {field} is incompatible")


def _invocation_input_digest(record: LLMDecisionRecord) -> str | None:
    if record.key.decision_kind == "query_plan":
        return record.key.input_evidence_digest
    value = record.sanitized_request.get("invocation_input_evidence_digest")
    return value if isinstance(value, str) and _is_sha256(value) else None


def _write_artifacts(
    root: Path,
    records: tuple[LLMDecisionRecord, ...],
    identity: _ExpectedIdentity,
) -> None:
    _ensure_private_directory(root)
    ordered = tuple(sorted(records, key=lambda item: item.record_key))
    lines = [
        json.dumps(
            serialize_llm_decision_record(record),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        for record in ordered
    ]
    encoded = (("\n".join(lines) + "\n") if lines else "").encode("utf-8")
    manifest = identity.manifest(ordered, _sha256_bytes(encoded))
    _write_bytes_atomic(root / LLM_DECISIONS_FILENAME, encoded)
    _write_bytes_atomic(
        root / LLM_DECISION_MANIFEST_FILENAME,
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


@contextmanager
def _artifact_lock(root: Path) -> Iterator[None]:
    _ensure_private_directory(root)
    path = root / ".llm-decisions.lock"
    if path.is_symlink():
        raise LLMDecisionBundleCorrupt("decision artifact lock cannot be a symlink")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise LLMDecisionBundleCorrupt(f"unsafe decision artifact path: {path.name}")
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _ensure_private_directory(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise LLMDecisionBundleCorrupt(f"unsafe decision artifact directory: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
