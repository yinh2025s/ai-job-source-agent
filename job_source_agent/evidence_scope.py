from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any

from .models import PIPELINE_STAGES


EVIDENCE_SCOPE_SCHEMA_VERSION = "1.0"
EMPTY_RECORDS_SHA256 = hashlib.sha256(b"").hexdigest()
_OPAQUE_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{16,128}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class EvidenceScopeRef:
    """Privacy-safe reference to terminal fetch outcomes from one stage invocation."""

    snapshot_store_id: str
    scope_id: str
    capture_attempt_id: str
    execution_fingerprint: str
    stage: str
    request_count: int
    records_sha256: str
    first_sequence: int | None = None
    last_sequence: int | None = None
    schema_version: str = EVIDENCE_SCOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_opaque_id(self.snapshot_store_id, "snapshot_store_id")
        _validate_sha256(self.scope_id, "scope_id")
        _validate_opaque_id(self.capture_attempt_id, "capture_attempt_id")
        _validate_sha256(self.execution_fingerprint, "execution_fingerprint")
        if self.stage not in PIPELINE_STAGES:
            raise ValueError(f"Unknown evidence stage: {self.stage!r}")
        if isinstance(self.request_count, bool) or not isinstance(self.request_count, int):
            raise TypeError("request_count must be an integer")
        if self.request_count < 0:
            raise ValueError("request_count must be non-negative")
        _validate_sha256(self.records_sha256, "records_sha256")
        if self.schema_version != EVIDENCE_SCOPE_SCHEMA_VERSION:
            raise ValueError("Evidence scope schema version is incompatible")

        if self.request_count == 0:
            if self.first_sequence is not None or self.last_sequence is not None:
                raise ValueError("Empty evidence scopes cannot declare sequence bounds")
            if self.records_sha256 != EMPTY_RECORDS_SHA256:
                raise ValueError("Empty evidence scopes must use the empty records digest")
            return

        if not _positive_int(self.first_sequence) or not _positive_int(self.last_sequence):
            raise ValueError("Non-empty evidence scopes require positive sequence bounds")
        if self.first_sequence > self.last_sequence:
            raise ValueError("Evidence scope sequence bounds are reversed")

    @classmethod
    def from_payload(cls, payload: Any) -> EvidenceScopeRef:
        if not isinstance(payload, dict):
            raise ValueError("Evidence scope payload must be an object")
        expected = {
            "snapshot_store_id",
            "scope_id",
            "capture_attempt_id",
            "execution_fingerprint",
            "stage",
            "request_count",
            "records_sha256",
            "first_sequence",
            "last_sequence",
            "schema_version",
        }
        if set(payload) != expected:
            raise ValueError("Evidence scope payload is incomplete or contains unknown fields")
        return cls(**payload)


@dataclass(frozen=True)
class StageEvidenceLineage:
    """Producer identity and optional snapshot evidence for one durable stage result."""

    stage: str
    execution_fingerprint: str
    producer_attempt_id: str
    snapshot_scope: EvidenceScopeRef | None = None
    schema_version: str = EVIDENCE_SCOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.stage not in PIPELINE_STAGES:
            raise ValueError(f"Unknown lineage stage: {self.stage!r}")
        _validate_sha256(self.execution_fingerprint, "execution_fingerprint")
        _validate_opaque_id(self.producer_attempt_id, "producer_attempt_id")
        if self.schema_version != EVIDENCE_SCOPE_SCHEMA_VERSION:
            raise ValueError("Stage evidence lineage schema version is incompatible")
        if self.snapshot_scope is not None:
            if not isinstance(self.snapshot_scope, EvidenceScopeRef):
                raise TypeError("snapshot_scope must use EvidenceScopeRef")
            if self.snapshot_scope.stage != self.stage:
                raise ValueError("Snapshot scope stage does not match lineage stage")
            if self.snapshot_scope.execution_fingerprint != self.execution_fingerprint:
                raise ValueError("Snapshot scope execution fingerprint does not match lineage")
            if self.snapshot_scope.capture_attempt_id != self.producer_attempt_id:
                raise ValueError("Snapshot scope attempt does not match lineage producer")

    @classmethod
    def from_payload(cls, payload: Any) -> StageEvidenceLineage:
        if not isinstance(payload, dict):
            raise ValueError("Stage evidence lineage payload must be an object")
        expected = {
            "stage",
            "execution_fingerprint",
            "producer_attempt_id",
            "snapshot_scope",
            "schema_version",
        }
        if set(payload) != expected:
            raise ValueError("Stage evidence lineage is incomplete or contains unknown fields")
        scope_payload = payload["snapshot_scope"]
        scope = None if scope_payload is None else EvidenceScopeRef.from_payload(scope_payload)
        return cls(
            stage=payload["stage"],
            execution_fingerprint=payload["execution_fingerprint"],
            producer_attempt_id=payload["producer_attempt_id"],
            snapshot_scope=scope,
            schema_version=payload["schema_version"],
        )


def new_capture_attempt_id() -> str:
    return uuid.uuid4().hex


def evidence_scope_id(
    snapshot_store_id: str,
    capture_attempt_id: str,
    execution_fingerprint: str,
    stage: str,
) -> str:
    _validate_opaque_id(snapshot_store_id, "snapshot_store_id")
    _validate_opaque_id(capture_attempt_id, "capture_attempt_id")
    _validate_sha256(execution_fingerprint, "execution_fingerprint")
    if stage not in PIPELINE_STAGES:
        raise ValueError(f"Unknown evidence stage: {stage!r}")
    payload = {
        "capture_attempt_id": capture_attempt_id,
        "execution_fingerprint": execution_fingerprint,
        "snapshot_store_id": snapshot_store_id,
        "stage": stage,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def evidence_records_sha256(descriptors: Iterable[dict[str, object]]) -> str:
    """Digest ordered privacy-safe terminal descriptors for one evidence scope."""

    digest = hashlib.sha256()
    for descriptor in descriptors:
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "kind",
            "outcome_sha256",
            "request_ordinal",
            "request_sha256",
        }:
            raise ValueError("Evidence record descriptor fields do not match contract")
        if descriptor["kind"] not in {"page", "fetch_failure"}:
            raise ValueError("Evidence record descriptor kind is unknown")
        if not _positive_int(descriptor["request_ordinal"]):
            raise ValueError("Evidence record request ordinal must be positive")
        _validate_sha256(descriptor["request_sha256"], "request_sha256")
        _validate_sha256(descriptor["outcome_sha256"], "outcome_sha256")
        encoded = json.dumps(
            descriptor,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        digest.update(encoded)
        digest.update(b"\n")
    return digest.hexdigest()


def _validate_opaque_id(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or _OPAQUE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a privacy-safe opaque identifier")


def _validate_sha256(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
