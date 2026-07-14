from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from typing import Any

from .evidence_scope import (
    EvidenceScopeRef,
    evidence_records_sha256,
    evidence_scope_id,
)
from .models import PIPELINE_STAGES
from .request_identity import request_identity_from_dict


_OPAQUE_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{16,128}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class SnapshotRequestCapture:
    """Scope metadata assigned before one logical fetch begins."""

    snapshot_store_id: str
    scope_id: str
    capture_attempt_id: str
    execution_fingerprint: str
    stage: str
    request_ordinal: int

    def __post_init__(self) -> None:
        _validate_scope_identity(self)
        _validate_positive_int(self.request_ordinal, "request_ordinal")


@dataclass(frozen=True)
class TerminalRecordDescriptor:
    """Privacy-safe terminal outcome identity used to finalize a scope."""

    kind: str
    request_ordinal: int
    sequence: int
    request_sha256: str
    outcome_sha256: str
    snapshot_store_id: str
    scope_id: str
    capture_attempt_id: str
    execution_fingerprint: str
    stage: str

    def __post_init__(self) -> None:
        if self.kind not in {"page", "fetch_failure"}:
            raise ValueError("Terminal record descriptor has an unknown kind")
        _validate_positive_int(self.request_ordinal, "request_ordinal")
        _validate_positive_int(self.sequence, "sequence")
        _validate_sha256(self.request_sha256, "request_sha256")
        _validate_sha256(self.outcome_sha256, "outcome_sha256")
        _validate_scope_identity(self)

    @classmethod
    def from_record(cls, record: Any) -> TerminalRecordDescriptor:
        request = record.request
        request_identity_from_dict(request)
        if record.kind == "page":
            outcome = record.sha256
        elif record.kind == "fetch_failure":
            outcome = _sha256_json(record.failure)
        else:
            raise ValueError(f"Unsupported terminal snapshot kind: {record.kind!r}")
        return cls(
            kind=record.kind,
            request_ordinal=record.request_ordinal,
            sequence=record.sequence,
            request_sha256=_sha256_json(request),
            outcome_sha256=outcome,
            snapshot_store_id=record.snapshot_store_id,
            scope_id=record.scope_id,
            capture_attempt_id=record.capture_attempt_id,
            execution_fingerprint=record.execution_fingerprint,
            stage=record.stage,
        )

    def digest_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "outcome_sha256": self.outcome_sha256,
            "request_ordinal": self.request_ordinal,
            "request_sha256": self.request_sha256,
        }


class SnapshotCaptureCoordinator:
    """Coordinate one attempt/stage-scoped snapshot tape across fetch threads."""

    def __init__(self, snapshot_store: Any | None = None) -> None:
        self._lock = threading.RLock()
        self._snapshot_store_id: str | None = None
        self._active: _ActiveScope | None = None
        if snapshot_store is not None:
            self.bind_store(snapshot_store)

    def bind_store(self, snapshot_store: Any) -> None:
        store_id = snapshot_store.snapshot_store_id
        with self._lock:
            if self._snapshot_store_id is not None and self._snapshot_store_id != store_id:
                raise ValueError("Capture coordinator is already bound to another snapshot store")
            self._snapshot_store_id = store_id

    def begin_stage(
        self,
        attempt_id: str,
        execution_fingerprint: str,
        stage: str,
    ) -> str:
        with self._lock:
            if self._snapshot_store_id is None:
                raise RuntimeError("Capture coordinator is not bound to a snapshot store")
            if self._active is not None:
                raise RuntimeError("A snapshot stage scope is already active")
            scope_id = evidence_scope_id(
                self._snapshot_store_id,
                attempt_id,
                execution_fingerprint,
                stage,
            )
            self._active = _ActiveScope(
                capture_attempt_id=attempt_id,
                execution_fingerprint=execution_fingerprint,
                stage=stage,
                scope_id=scope_id,
                next_ordinal=1,
                descriptors={},
            )
            return scope_id

    def begin_request(self) -> SnapshotRequestCapture:
        with self._lock:
            active = self._require_active()
            ordinal = active.next_ordinal
            active.next_ordinal += 1
            return SnapshotRequestCapture(
                snapshot_store_id=self._snapshot_store_id or "",
                scope_id=active.scope_id,
                capture_attempt_id=active.capture_attempt_id,
                execution_fingerprint=active.execution_fingerprint,
                stage=active.stage,
                request_ordinal=ordinal,
            )

    def accept_terminal_record(self, record_or_descriptor: Any) -> None:
        descriptor = (
            record_or_descriptor
            if isinstance(record_or_descriptor, TerminalRecordDescriptor)
            else TerminalRecordDescriptor.from_record(record_or_descriptor)
        )
        with self._lock:
            active = self._require_active()
            self._validate_record_scope(descriptor, active)
            if descriptor.request_ordinal <= 0 or descriptor.request_ordinal >= active.next_ordinal:
                raise ValueError("Terminal record uses an unassigned request ordinal")
            if descriptor.request_ordinal in active.descriptors:
                raise ValueError("A terminal record already exists for this request ordinal")
            active.descriptors[descriptor.request_ordinal] = descriptor

    def finalize(self) -> EvidenceScopeRef:
        with self._lock:
            active = self._require_active()
            request_count = active.next_ordinal - 1
            if len(active.descriptors) != request_count:
                raise RuntimeError("Cannot finalize a scope with unterminated requests")
            descriptors = [active.descriptors[index] for index in range(1, request_count + 1)]
            digest = evidence_records_sha256(
                descriptor.digest_payload() for descriptor in descriptors
            )
            sequences = [descriptor.sequence for descriptor in descriptors]
            scope = EvidenceScopeRef(
                snapshot_store_id=self._snapshot_store_id or "",
                scope_id=active.scope_id,
                capture_attempt_id=active.capture_attempt_id,
                execution_fingerprint=active.execution_fingerprint,
                stage=active.stage,
                request_count=request_count,
                records_sha256=digest,
                first_sequence=min(sequences) if sequences else None,
                last_sequence=max(sequences) if sequences else None,
            )
            self._active = None
            return scope

    def abort_stage(self) -> None:
        """Discard an unfinished scope after a stage exception."""

        with self._lock:
            self._active = None

    def _require_active(self) -> _ActiveScope:
        if self._active is None:
            raise RuntimeError("No snapshot stage scope is active")
        return self._active

    def _validate_record_scope(
        self,
        record: TerminalRecordDescriptor,
        active: _ActiveScope,
    ) -> None:
        expected = {
            "snapshot_store_id": self._snapshot_store_id,
            "scope_id": active.scope_id,
            "capture_attempt_id": active.capture_attempt_id,
            "execution_fingerprint": active.execution_fingerprint,
            "stage": active.stage,
        }
        for field_name, expected_value in expected.items():
            if getattr(record, field_name) != expected_value:
                raise ValueError(f"Terminal record {field_name} does not match active scope")


@dataclass
class _ActiveScope:
    capture_attempt_id: str
    execution_fingerprint: str
    stage: str
    scope_id: str
    next_ordinal: int
    descriptors: dict[int, TerminalRecordDescriptor]


def _sha256_json(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _validate_scope_identity(value: Any) -> None:
    if (
        not isinstance(value.snapshot_store_id, str)
        or _OPAQUE_ID_PATTERN.fullmatch(value.snapshot_store_id) is None
    ):
        raise ValueError("snapshot_store_id must be a privacy-safe opaque identifier")
    if not isinstance(value.scope_id, str) or _SHA256_PATTERN.fullmatch(value.scope_id) is None:
        raise ValueError("scope_id must be a lowercase SHA-256 digest")
    if (
        not isinstance(value.capture_attempt_id, str)
        or _OPAQUE_ID_PATTERN.fullmatch(value.capture_attempt_id) is None
    ):
        raise ValueError("capture_attempt_id must be a privacy-safe opaque identifier")
    if (
        not isinstance(value.execution_fingerprint, str)
        or _SHA256_PATTERN.fullmatch(value.execution_fingerprint) is None
    ):
        raise ValueError("execution_fingerprint must be a lowercase SHA-256 digest")
    if value.stage not in PIPELINE_STAGES:
        raise ValueError(f"Unknown evidence stage: {value.stage!r}")
    if value.scope_id != evidence_scope_id(
        value.snapshot_store_id,
        value.capture_attempt_id,
        value.execution_fingerprint,
        value.stage,
    ):
        raise ValueError("scope_id does not match capture identity")


def _validate_sha256(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _validate_positive_int(value: Any, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
