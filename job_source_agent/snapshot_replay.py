from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .evidence_scope import EvidenceScopeRef
from .outcome_tape import (
    FetchFailureOutcomeTapeEntry,
    OutcomeTape,
    OutcomeTapeEntry,
    OutcomeTapeError,
    PageOutcomeTapeEntry,
)
from .reasons import REASON_SPECS, reason_spec
from .request_identity import RequestIdentity, request_identity_from_dict
from .snapshot import (
    sanitize_snapshot_body,
    sanitize_url,
    snapshot_artifact_blob_path,
    snapshot_artifact_path_for_url,
    snapshot_blob_path,
    snapshot_path_for_url,
)


REPLAY_SCHEMA_VERSION = 2
REQUIRED_RECORD_FIELDS = {
    "request_url",
    "page_url",
    "final_url",
    "sanitized_url",
    "source",
    "path",
    "artifact_paths",
    "sha256",
    "byte_count",
    "captured_at_epoch",
}
SCOPE_MEMBERSHIP_FIELDS = {
    "snapshot_store_id",
    "scope_id",
    "capture_attempt_id",
    "execution_fingerprint",
    "stage",
    "request_ordinal",
}
V3_PAGE_RECORD_FIELDS = REQUIRED_RECORD_FIELDS | {
    "schema_version",
    "kind",
    "sequence",
    "request",
    "blob_path",
    "artifact_blob_paths",
} | SCOPE_MEMBERSHIP_FIELDS
V3_FAILURE_RECORD_FIELDS = {
    "schema_version",
    "kind",
    "sequence",
    "request",
    "failure",
    "captured_at_epoch",
    "terminal",
} | SCOPE_MEMBERSHIP_FIELDS


class SnapshotReplayError(ValueError):
    """Raised when a snapshot set cannot be replayed safely."""


@dataclass(frozen=True)
class ReplayResult:
    manifest: dict[str, Any]
    summary: dict[str, Any]
    manifest_path: Path
    summary_path: Path


def load_scoped_outcome_tapes(
    snapshot_dir: str | Path,
    scopes: Iterable[EvidenceScopeRef],
) -> dict[str, OutcomeTape]:
    """Load exact schema-v3 terminal outcomes for finalized evidence scopes."""
    source_root = Path(snapshot_dir).resolve()
    requested: dict[str, EvidenceScopeRef] = {}
    for scope in scopes:
        if not isinstance(scope, EvidenceScopeRef):
            raise TypeError("scopes must contain EvidenceScopeRef values")
        if scope.scope_id in requested:
            raise SnapshotReplayError(f"Duplicate requested evidence scope: {scope.scope_id}")
        requested[scope.scope_id] = scope
    if not requested:
        return {}

    index_path = source_root / "snapshots.jsonl"
    failure_index_path = source_root / "fetch-failures.jsonl"
    _validate_index_path(index_path, "Snapshot index")
    _validate_index_path(failure_index_path, "Snapshot failure index")
    page_records, _ = _read_optional_records(index_path)
    failure_records, _ = _read_optional_records(failure_index_path)

    selected: dict[str, list[tuple[int, OutcomeTapeEntry]]] = {
        scope_id: [] for scope_id in requested
    }
    for line_number, record in page_records:
        if not _is_v3_record(record, line_number, "snapshot"):
            continue
        _validate_v3_fields(record, V3_PAGE_RECORD_FIELDS, "page", line_number, "Snapshot")
        scope_id = record["scope_id"]
        if scope_id not in requested:
            continue
        entry = _parse_v3_page_record(source_root, record, line_number)
        selected[scope_id].append((record["sequence"], entry))

    for line_number, record in failure_records:
        if not _is_v3_record(record, line_number, "failure"):
            continue
        _validate_v3_fields(
            record,
            V3_FAILURE_RECORD_FIELDS,
            "fetch_failure",
            line_number,
            "Failure",
        )
        scope_id = record["scope_id"]
        if scope_id not in requested:
            continue
        entry = _parse_v3_failure_record(record, line_number)
        selected[scope_id].append((record["sequence"], entry))

    tapes: dict[str, OutcomeTape] = {}
    for scope_id, scope in requested.items():
        records = selected[scope_id]
        records.sort(key=lambda item: item[1].request_ordinal)
        ordinals = [item[1].request_ordinal for item in records]
        if len(ordinals) != len(set(ordinals)):
            raise SnapshotReplayError(f"Evidence scope {scope_id} has a duplicate request ordinal")
        sequences = [sequence for sequence, _ in records]
        try:
            tapes[scope_id] = OutcomeTape(scope, (entry for _, entry in records))
        except (OutcomeTapeError, TypeError, ValueError) as exc:
            raise SnapshotReplayError(f"Invalid evidence scope {scope_id}: {exc}") from exc
        if sequences and (
            min(sequences) != scope.first_sequence or max(sequences) != scope.last_sequence
        ):
            raise SnapshotReplayError(
                f"Evidence scope {scope_id} sequence bounds do not match its records"
            )
    return tapes


def replay_snapshots(snapshot_dir: str | Path, output_dir: str | Path) -> ReplayResult:
    """Validate sanitized snapshots and materialize deterministic offline fixtures."""
    source_root = Path(snapshot_dir).resolve()
    destination_path = Path(output_dir)
    if destination_path.is_symlink():
        raise SnapshotReplayError("Replay output must not be a symbolic link")
    destination_root = destination_path.resolve()
    index_path = source_root / "snapshots.jsonl"
    failure_index_path = source_root / "fetch-failures.jsonl"
    if index_path.is_symlink() or (index_path.exists() and not index_path.is_file()):
        raise SnapshotReplayError(f"Snapshot index is missing or unsafe: {index_path}")
    if failure_index_path.is_symlink() or (
        failure_index_path.exists() and not failure_index_path.is_file()
    ):
        raise SnapshotReplayError(f"Snapshot failure index is unsafe: {failure_index_path}")
    if not index_path.exists() and not failure_index_path.exists():
        raise SnapshotReplayError("Snapshot page and failure indexes are both missing")
    if destination_root == source_root or _is_within(destination_root, source_root):
        raise SnapshotReplayError("Replay output must not be the snapshot directory or one of its children")

    records, skipped_corrupt_tail = _read_records(index_path) if index_path.exists() else ([], 0)
    failure_records, skipped_failure_tail = _read_optional_records(failure_index_path)
    records = _legacy_records(records)
    failure_records = _legacy_records(failure_records)
    page_records: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    observed_by_path: dict[str, dict[str, Any]] = {}
    latest_sequence_by_request: dict[str, int] = {}
    duplicate_count = 0
    superseded_count = 0
    seen_sequences: set[int] = set()

    for line_number, record in records:
        entry = _validate_record(source_root, record, line_number)
        _validate_unique_sequence(entry.get("sequence"), seen_sequences, "snapshot", line_number)
        artifacts = _validate_artifacts(source_root, record, line_number)
        existing = observed_by_path.get(entry["fixture_path"])
        if existing:
            if existing["sha256"] != entry["sha256"]:
                superseded_count += 1
            else:
                duplicate_count += 1
        observed_by_path[entry["fixture_path"]] = entry
        page_records.append((entry, artifacts))
        if entry["sequence"] is not None:
            identity_key = _request_identity_key(entry["request"])
            latest_sequence_by_request[identity_key] = max(
                entry["sequence"],
                latest_sequence_by_request.get(identity_key, 0),
            )

    failures = []
    privacy_exclusions = 0
    for line_number, record in failure_records:
        failure_entry = _validate_failure_record(record, line_number)
        _validate_unique_sequence(
            failure_entry["sequence"],
            seen_sequences,
            "fetch failure",
            line_number,
        )
        identity_key = _request_identity_key(failure_entry["request"])
        latest_sequence_by_request[identity_key] = max(
            failure_entry["sequence"],
            latest_sequence_by_request.get(identity_key, 0),
        )
        if not failure_entry["request"]["replayable"]:
            privacy_exclusions += 1
            continue
        failures.append(failure_entry)
    page_records = [
        (entry, artifacts)
        for entry, artifacts in page_records
        if entry["sequence"] is None
        or entry["sequence"]
        == latest_sequence_by_request[_request_identity_key(entry["request"])]
    ]
    failures = [
        entry
        for entry in failures
        if entry["sequence"]
        == latest_sequence_by_request[_request_identity_key(entry["request"])]
    ]
    failures.sort(key=lambda item: item["sequence"])

    selected_by_path: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    selected_by_request_path: dict[str, dict[str, Any]] = {}
    for entry, artifacts in page_records:
        request_fixture_path = snapshot_path_for_url(
            Path("sites"),
            entry["request_urls"][0],
            request_identity=entry.get("request_identity"),
        ).as_posix()
        if request_fixture_path != entry["fixture_path"]:
            selected_by_request_path[request_fixture_path] = {
                **entry,
                "fixture_path": request_fixture_path,
                "alias_of": entry["fixture_path"],
            }
        selected_by_path[entry["fixture_path"]] = (entry, artifacts)

    selected_fixture_entries: dict[str, dict[str, Any]] = {}
    for entry in [selected[0] for selected in selected_by_path.values()] + list(
        selected_by_request_path.values()
    ):
        existing = selected_fixture_entries.get(entry["fixture_path"])
        if existing is None or entry["record_index"] >= existing["record_index"]:
            selected_fixture_entries[entry["fixture_path"]] = entry
    fixture_entries_internal = sorted(
        selected_fixture_entries.values(),
        key=lambda item: (item["fixture_path"], item["final_url"]),
    )
    artifact_entries: dict[str, dict[str, Any]] = {}
    for _, selected_artifacts in selected_by_path.values():
        for artifact in selected_artifacts:
            existing_artifact = artifact_entries.get(artifact["replay_path"])
            if existing_artifact and existing_artifact["sha256"] != artifact["sha256"]:
                raise SnapshotReplayError(
                    f"Conflicting selected artifacts target {artifact['replay_path']}"
                )
            artifact_entries[artifact["replay_path"]] = artifact

    artifacts = sorted(artifact_entries.values(), key=lambda item: item["replay_path"])
    _validate_selected_canonical_views(fixture_entries_internal, artifacts)
    _reset_managed_outputs(destination_root)
    _materialize(destination_root, fixture_entries_internal, artifacts)

    fixture_entries = [
        {
            key: value
            for key, value in entry.items()
            if key not in {"source_path", "canonical_path", "record_index", "request_identity"}
        }
        for entry in fixture_entries_internal
    ]

    public_artifacts = [
        {
            key: value
            for key, value in artifact.items()
            if key not in {"source_path", "canonical_path"}
        }
        for artifact in artifacts
    ]
    manifest = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "evidence_mode": "legacy_global_latest",
        "fixtures_dir": "sites",
        "entries": fixture_entries,
        "artifacts": public_artifacts,
        "failure_entries": failures,
    }
    summary = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "evidence_mode": "legacy_global_latest",
        "snapshot_records": len(records),
        "fixture_count": len(fixture_entries),
        "artifact_count": len(artifacts),
        "duplicate_records": duplicate_count,
        "superseded_records": superseded_count,
        "skipped_records": skipped_corrupt_tail,
        "corrupt_tail_records": skipped_corrupt_tail,
        "failure_records": len(failure_records),
        "replayable_failures": len(failures),
        "privacy_exclusions": privacy_exclusions,
        "corrupt_failure_tail_records": skipped_failure_tail,
        "status": "success",
    }
    manifest_path = destination_root / "replay-manifest.json"
    summary_path = destination_root / "replay-summary.json"
    _write_json_atomic(manifest_path, manifest)
    _write_json_atomic(summary_path, summary)
    _write_json_atomic(
        destination_root / "fetch-failures.json",
        {"schema_version": REPLAY_SCHEMA_VERSION, "entries": failures},
    )
    return ReplayResult(manifest, summary, manifest_path, summary_path)


def _validate_index_path(path: Path, label: str) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise SnapshotReplayError(f"{label} is unsafe: {path}")


def _legacy_records(
    records: list[tuple[int, dict[str, Any]]],
) -> list[tuple[int, dict[str, Any]]]:
    return [
        (line_number, record)
        for line_number, record in records
        if record.get("schema_version", 1) != 3
    ]


def _is_v3_record(record: dict[str, Any], line_number: int, label: str) -> bool:
    schema_version = record.get("schema_version", 1)
    if schema_version in {1, 2}:
        return False
    if schema_version != 3:
        raise SnapshotReplayError(
            f"{label.title()} line {line_number}: unsupported record schema"
        )
    return True


def _validate_v3_fields(
    record: dict[str, Any],
    expected_fields: set[str],
    expected_kind: str,
    line_number: int,
    label: str,
) -> None:
    if set(record) != expected_fields:
        raise SnapshotReplayError(f"{label} line {line_number}: fields do not match schema v3")
    if record["kind"] != expected_kind:
        raise SnapshotReplayError(f"{label} line {line_number}: invalid schema-v3 record kind")


def _v3_common(record: dict[str, Any]) -> dict[str, Any]:
    try:
        request = request_identity_from_dict(record["request"])
    except (TypeError, ValueError) as exc:
        raise SnapshotReplayError(f"Invalid schema-v3 request identity: {exc}") from exc
    return {
        "snapshot_store_id": record["snapshot_store_id"],
        "scope_id": record["scope_id"],
        "capture_attempt_id": record["capture_attempt_id"],
        "execution_fingerprint": record["execution_fingerprint"],
        "stage": record["stage"],
        "request_ordinal": record["request_ordinal"],
        "request": request,
    }


def _parse_v3_page_record(
    source_root: Path,
    record: dict[str, Any],
    line_number: int,
) -> PageOutcomeTapeEntry:
    legacy_record = {
        key: value for key, value in record.items() if key not in SCOPE_MEMBERSHIP_FIELDS
    }
    legacy_record["schema_version"] = 2
    try:
        validated = _validate_record(source_root, legacy_record, line_number)
        _validate_artifacts(source_root, legacy_record, line_number)
        html = validated["source_path"].read_text(encoding="utf-8")
        return PageOutcomeTapeEntry(
            **_v3_common(record),
            page_url=record["page_url"],
            html=html,
            final_url=record["final_url"],
            source=record["source"],
        )
    except (OutcomeTapeError, TypeError, ValueError, OSError) as exc:
        if isinstance(exc, SnapshotReplayError):
            raise
        raise SnapshotReplayError(f"Snapshot line {line_number}: invalid schema-v3 page: {exc}") from exc


def _parse_v3_failure_record(
    record: dict[str, Any],
    line_number: int,
) -> FetchFailureOutcomeTapeEntry:
    legacy_record = {
        key: value for key, value in record.items() if key not in SCOPE_MEMBERSHIP_FIELDS
    }
    legacy_record["schema_version"] = 2
    try:
        validated = _validate_failure_record(legacy_record, line_number)
        return FetchFailureOutcomeTapeEntry(
            **_v3_common(record),
            **validated["failure"],
        )
    except (OutcomeTapeError, TypeError, ValueError) as exc:
        if isinstance(exc, SnapshotReplayError):
            raise
        raise SnapshotReplayError(
            f"Failure line {line_number}: invalid schema-v3 failure: {exc}"
        ) from exc


def _read_records(index_path: Path) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    records: list[tuple[int, dict[str, Any]]] = []
    raw_index = index_path.read_text(encoding="utf-8")
    physical_lines = raw_index.splitlines()
    skipped_corrupt_tail = 0
    for line_number, raw_line in enumerate(physical_lines, start=1):
        if not raw_line.strip():
            raise SnapshotReplayError(f"Line {line_number}: blank snapshot records are not allowed")
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            if (
                line_number == len(physical_lines)
                and not raw_index.endswith(("\n", "\r"))
                and _is_incomplete_json_tail(raw_line, exc)
            ):
                skipped_corrupt_tail = 1
                continue
            raise SnapshotReplayError(f"Line {line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise SnapshotReplayError(f"Line {line_number}: snapshot record must be an object")
        records.append((line_number, record))
    if not records:
        raise SnapshotReplayError("Snapshot index contains no records")
    return records, skipped_corrupt_tail


def _read_optional_records(index_path: Path) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    if not index_path.exists():
        return [], 0
    if not index_path.is_file() or index_path.is_symlink():
        raise SnapshotReplayError(f"Snapshot failure index is unsafe: {index_path}")
    return _read_records(index_path)


def _is_incomplete_json_tail(raw_line: str, error: json.JSONDecodeError) -> bool:
    """Return true only when valid JSON could be formed by appending at EOF."""
    stripped = raw_line.rstrip()
    if error.msg.startswith("Unterminated string"):
        return True
    return error.pos >= len(stripped)


def _validate_record(source_root: Path, record: dict[str, Any], line_number: int) -> dict[str, Any]:
    missing = sorted(REQUIRED_RECORD_FIELDS - record.keys())
    if missing:
        raise SnapshotReplayError(f"Line {line_number}: missing metadata fields: {', '.join(missing)}")

    schema_version = record.get("schema_version", 1)
    if schema_version not in {1, 2}:
        raise SnapshotReplayError(f"Line {line_number}: unsupported snapshot schema version")
    request_identity: RequestIdentity | None = None
    sequence = None
    if schema_version == 2:
        if record.get("kind") != "page":
            raise SnapshotReplayError(f"Line {line_number}: invalid snapshot record kind")
        sequence = record.get("sequence")
        if type(sequence) is not int or sequence <= 0:
            raise SnapshotReplayError(f"Line {line_number}: sequence must be a positive integer")
        try:
            request_identity = request_identity_from_dict(record.get("request"))
        except ValueError as exc:
            raise SnapshotReplayError(f"Line {line_number}: invalid request identity: {exc}") from exc
        if not request_identity.replayable:
            raise SnapshotReplayError(
                f"Line {line_number}: page snapshot has non-replayable request identity"
            )

    urls = {}
    for field in ("request_url", "page_url", "final_url", "sanitized_url"):
        value = record[field]
        if not isinstance(value, str) or not value:
            raise SnapshotReplayError(f"Line {line_number}: {field} must be a non-empty URL")
        _validate_sanitized_url(value, field, line_number)
        urls[field] = value
    if urls["final_url"] != urls["sanitized_url"]:
        raise SnapshotReplayError(f"Line {line_number}: final_url and sanitized_url must match")
    if request_identity and request_identity.sanitized_url != urls["request_url"]:
        raise SnapshotReplayError(f"Line {line_number}: request identity does not match request_url")

    relative_path = _validated_relative_path(record["path"], "path", line_number, "sites")
    canonical_path = _resolve_member(source_root, relative_path, line_number)
    expected_path = snapshot_path_for_url(
        source_root / "sites",
        urls["sanitized_url"],
        request_identity=request_identity,
    )
    if canonical_path != expected_path.resolve():
        raise SnapshotReplayError(f"Line {line_number}: path does not match sanitized_url")
    blob_path_value = record.get("blob_path")
    if blob_path_value is not None:
        blob_path = _validated_relative_path(blob_path_value, "blob_path", line_number, "blobs")
        source_path = _resolve_member(source_root, blob_path, line_number)
        expected_blob_path = snapshot_blob_path(source_root, str(record["sha256"])).resolve()
        if source_path != expected_blob_path:
            raise SnapshotReplayError(f"Line {line_number}: blob_path does not match sha256")
    else:
        source_path = canonical_path
    body = _read_regular_file(source_path, line_number, "snapshot")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SnapshotReplayError(f"Line {line_number}: snapshot body is not UTF-8") from exc
    if sanitize_snapshot_body(text) != text:
        raise SnapshotReplayError(f"Line {line_number}: snapshot body is not fully sanitized")

    digest = hashlib.sha256(body).hexdigest()
    if not isinstance(record["sha256"], str) or record["sha256"] != digest:
        raise SnapshotReplayError(f"Line {line_number}: sha256 does not match snapshot body")
    if type(record["byte_count"]) is not int or record["byte_count"] != len(body):
        raise SnapshotReplayError(f"Line {line_number}: byte_count does not match snapshot body")
    if not isinstance(record["source"], str) or not record["source"]:
        raise SnapshotReplayError(f"Line {line_number}: source must be a non-empty string")
    if (
        not isinstance(record["captured_at_epoch"], (int, float))
        or isinstance(record["captured_at_epoch"], bool)
        or not math.isfinite(record["captured_at_epoch"])
    ):
        raise SnapshotReplayError(f"Line {line_number}: captured_at_epoch must be numeric")
    if not isinstance(record["artifact_paths"], dict):
        raise SnapshotReplayError(f"Line {line_number}: artifact_paths must be an object")

    return {
        "fixture_path": relative_path.as_posix(),
        "request_urls": [urls["request_url"]],
        "page_urls": [urls["page_url"]],
        "final_url": urls["final_url"],
        "sha256": digest,
        "byte_count": len(body),
        "source_path": source_path,
        "canonical_path": canonical_path,
        "record_index": line_number,
        "sequence": sequence,
        "request": request_identity.as_dict() if request_identity else None,
        "request_identity": request_identity,
    }


def _validate_failure_record(record: dict[str, Any], line_number: int) -> dict[str, Any]:
    expected_fields = {
        "schema_version",
        "kind",
        "sequence",
        "request",
        "failure",
        "captured_at_epoch",
        "terminal",
    }
    if not isinstance(record, dict) or set(record) != expected_fields:
        raise SnapshotReplayError(f"Failure line {line_number}: fields do not match schema")
    if record["schema_version"] != 2 or record["kind"] != "fetch_failure":
        raise SnapshotReplayError(f"Failure line {line_number}: unsupported record schema")
    sequence = record["sequence"]
    if type(sequence) is not int or sequence <= 0:
        raise SnapshotReplayError(f"Failure line {line_number}: invalid sequence")
    if record["terminal"] is not True:
        raise SnapshotReplayError(f"Failure line {line_number}: failure must be terminal")
    try:
        identity = request_identity_from_dict(record["request"])
    except ValueError as exc:
        raise SnapshotReplayError(
            f"Failure line {line_number}: invalid request identity: {exc}"
        ) from exc
    failure = record["failure"]
    if not isinstance(failure, dict) or set(failure) != {
        "status",
        "reason_code",
        "retryable",
        "message",
        "taxonomy_version",
    }:
        raise SnapshotReplayError(f"Failure line {line_number}: invalid failure fields")
    status = failure["status"]
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        raise SnapshotReplayError(f"Failure line {line_number}: invalid HTTP status")
    reason_code = failure["reason_code"]
    if reason_code not in REASON_SPECS:
        raise SnapshotReplayError(f"Failure line {line_number}: unknown reason code")
    if type(failure["retryable"]) is not bool:
        raise SnapshotReplayError(f"Failure line {line_number}: retryable must be boolean")
    if failure["retryable"] != reason_spec(reason_code).retryable:
        raise SnapshotReplayError(f"Failure line {line_number}: retryability mismatch")
    if failure["taxonomy_version"] != 1:
        raise SnapshotReplayError(f"Failure line {line_number}: unsupported taxonomy version")
    message = failure["message"]
    if not isinstance(message, str) or not message or len(message) > 500:
        raise SnapshotReplayError(f"Failure line {line_number}: invalid failure message")
    if sanitize_snapshot_body(message) != message:
        raise SnapshotReplayError(f"Failure line {line_number}: unsanitized failure message")
    captured = record["captured_at_epoch"]
    if (
        not isinstance(captured, (int, float))
        or isinstance(captured, bool)
        or not math.isfinite(captured)
    ):
        raise SnapshotReplayError(f"Failure line {line_number}: invalid capture time")
    return {
        "sequence": sequence,
        "request": identity.as_dict(),
        "failure": failure,
        "captured_at_epoch": captured,
    }


def _validate_unique_sequence(
    sequence: int | None,
    seen: set[int],
    label: str,
    line_number: int,
) -> None:
    if sequence is None:
        return
    if sequence in seen:
        raise SnapshotReplayError(f"{label.title()} line {line_number}: duplicate sequence")
    seen.add(sequence)


def _request_identity_key(request: dict[str, Any]) -> str:
    return json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _validate_artifacts(
    source_root: Path,
    record: dict[str, Any],
    line_number: int,
) -> list[dict[str, Any]]:
    artifacts = record["artifact_paths"]
    artifact_blobs = record.get("artifact_blob_paths", {})
    if not isinstance(artifact_blobs, dict):
        raise SnapshotReplayError(f"Line {line_number}: artifact_blob_paths must be an object")
    validated = []
    if any(not isinstance(name, str) for name in artifacts):
        raise SnapshotReplayError(f"Line {line_number}: artifact names must be strings")
    for name in sorted(artifacts):
        path_value = artifacts[name]
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise SnapshotReplayError(f"Line {line_number}: invalid artifact name")
        relative_path = _validated_relative_path(path_value, f"artifact_paths.{name}", line_number, "artifacts")
        canonical_path = _resolve_member(source_root, relative_path, line_number)
        expected_path = snapshot_artifact_path_for_url(
            source_root / "artifacts", record["sanitized_url"], name
        ).resolve()
        if canonical_path != expected_path:
            raise SnapshotReplayError(f"Line {line_number}: artifact path does not match metadata for {name}")
        blob_path_value = artifact_blobs.get(name)
        if blob_path_value is not None:
            blob_path = _validated_relative_path(
                blob_path_value,
                f"artifact_blob_paths.{name}",
                line_number,
                "blobs",
            )
            source_path = _resolve_member(source_root, blob_path, line_number)
        else:
            source_path = canonical_path
        content = _read_regular_file(source_path, line_number, f"artifact {name}")
        digest = hashlib.sha256(content).hexdigest()
        if blob_path_value is not None:
            expected_blob_path = snapshot_artifact_blob_path(source_root, digest, name).resolve()
            if source_path != expected_blob_path:
                raise SnapshotReplayError(
                    f"Line {line_number}: artifact blob path does not match content for {name}"
                )
        validated.append(
            {
                "name": name,
                "replay_path": relative_path.as_posix(),
                "sha256": digest,
                "byte_count": len(content),
                "source_path": source_path,
                "canonical_path": canonical_path,
            }
        )
    return validated


def _validate_sanitized_url(url: str, field: str, line_number: int) -> None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise SnapshotReplayError(f"Line {line_number}: invalid {field}: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise SnapshotReplayError(f"Line {line_number}: invalid {field}")
    if port is not None and not (1 <= port <= 65535):
        raise SnapshotReplayError(f"Line {line_number}: invalid {field} port")
    if sanitize_url(url) != url:
        raise SnapshotReplayError(f"Line {line_number}: {field} contains unsanitized data")


def _validated_relative_path(value: Any, field: str, line_number: int, prefix: str) -> Path:
    if not isinstance(value, str) or not value:
        raise SnapshotReplayError(f"Line {line_number}: {field} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.parts[0] != prefix:
        raise SnapshotReplayError(f"Line {line_number}: unsafe {field}: {value}")
    return path


def _resolve_member(root: Path, relative_path: Path, line_number: int) -> Path:
    resolved = (root / relative_path).resolve()
    if not _is_within(resolved, root):
        raise SnapshotReplayError(f"Line {line_number}: path escapes snapshot directory")
    return resolved


def _read_regular_file(path: Path, line_number: int, label: str) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise SnapshotReplayError(f"Line {line_number}: missing or unsafe {label}: {path}")
    return path.read_bytes()


def _materialize(
    destination_root: Path,
    entries: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    destination_root.mkdir(parents=True, exist_ok=True)
    (destination_root / "sites").mkdir()
    for entry in entries:
        source = entry["source_path"]
        destination = destination_root / entry["fixture_path"]
        _copy_verified(destination_root, source, destination, entry["sha256"])
    for artifact in artifacts:
        _copy_verified(
            destination_root,
            artifact["source_path"],
            destination_root / artifact["replay_path"],
            artifact["sha256"],
        )


def _reset_managed_outputs(destination_root: Path) -> None:
    for directory_name in ("sites", "artifacts"):
        path = destination_root / directory_name
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise SnapshotReplayError(f"Unsafe replay output path: {path}")
        if path.exists():
            shutil.rmtree(path)
    for filename in ("replay-manifest.json", "replay-summary.json", "fetch-failures.json"):
        path = destination_root / filename
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise SnapshotReplayError(f"Unsafe replay output path: {path}")
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _validate_selected_canonical_views(
    entries: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    for entry in entries:
        if entry.get("alias_of"):
            continue
        body = _read_regular_file(entry["canonical_path"], 0, "selected snapshot")
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SnapshotReplayError("Selected snapshot body is not UTF-8") from exc
        if sanitize_snapshot_body(text) != text:
            raise SnapshotReplayError("Selected snapshot body is not fully sanitized")
        if hashlib.sha256(body).hexdigest() != entry["sha256"]:
            raise SnapshotReplayError("Selected snapshot body does not match its immutable blob")
    for artifact in artifacts:
        content = _read_regular_file(artifact["canonical_path"], 0, "artifact")
        if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
            raise SnapshotReplayError("Selected artifact does not match its immutable blob")


def _copy_verified(root: Path, source: Path, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not _is_within(destination.resolve(), root):
        raise SnapshotReplayError(f"Replay destination escapes output directory: {destination}")
    if destination.exists():
        if destination.is_symlink() or not destination.is_file():
            raise SnapshotReplayError(f"Unsafe replay destination: {destination}")
        if hashlib.sha256(destination.read_bytes()).hexdigest() == expected_sha256:
            return
        raise SnapshotReplayError(f"Replay destination already contains different data: {destination}")
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
