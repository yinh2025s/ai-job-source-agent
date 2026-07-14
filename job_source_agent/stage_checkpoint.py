from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, Iterator

import fcntl

from .checkpoint import ADAPTER_VERSION, CHECKPOINT_SCHEMA_VERSION
from .contracts import CONTRACT_SCHEMA_VERSION, StageExecution
from .evidence_scope import StageEvidenceLineage
from .homepage_navigation import HomepageNavigationEvidence
from .job_board import DiscoveredJobBoard
from .models import PIPELINE_STAGES, StageResult


class FilesystemCheckpointStore:
    """Persist compatible stage executions as atomic JSON checkpoints."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def load(self, execution_fingerprint: str, stage: str) -> StageExecution | None:
        _stage_index(stage)
        with self._fingerprint_lock(execution_fingerprint):
            path = self._checkpoint_path(execution_fingerprint, stage)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                return _deserialize_checkpoint(payload, execution_fingerprint, stage)
            except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                return None

    def save(self, execution_fingerprint: str, execution: StageExecution) -> None:
        stage = execution.result.stage
        _stage_index(stage)
        with self._fingerprint_lock(execution_fingerprint):
            path = self._checkpoint_path(execution_fingerprint, stage)
            execution_payload = asdict(execution)
            discovered_board = execution.updates.get("discovered_job_board")
            if "discovered_job_board" in execution.updates and not isinstance(
                discovered_board, DiscoveredJobBoard
            ):
                raise TypeError("discovered_job_board checkpoint update has an invalid type")
            if isinstance(discovered_board, DiscoveredJobBoard):
                checkpoint_board = discovered_board.to_checkpoint_payload()
                if checkpoint_board is None:
                    execution_payload["updates"].pop("discovered_job_board", None)
                else:
                    execution_payload["updates"]["discovered_job_board"] = checkpoint_board
            homepage_evidence = execution.updates.get("homepage_navigation_evidence")
            if "homepage_navigation_evidence" in execution.updates and not isinstance(
                homepage_evidence, HomepageNavigationEvidence
            ):
                raise TypeError(
                    "homepage_navigation_evidence checkpoint update has an invalid type"
                )
            if isinstance(homepage_evidence, HomepageNavigationEvidence):
                execution_payload["updates"]["homepage_navigation_evidence"] = (
                    homepage_evidence.to_checkpoint_payload()
                )
            payload = {
                "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
                "adapter_version": ADAPTER_VERSION,
                "execution_fingerprint": execution_fingerprint,
                "stage": stage,
                "execution": execution_payload,
            }

            path.parent.mkdir(parents=True, exist_ok=True)
            self._cleanup_temporary_files(path.parent, stage)
            temporary_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=path.parent,
                    prefix=f".{stage}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temporary_path = handle.name
                    json.dump(payload, handle, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, path)
                temporary_path = None
                _fsync_directory(path.parent)
            finally:
                if temporary_path is not None:
                    try:
                        os.unlink(temporary_path)
                    except FileNotFoundError:
                        pass

    def invalidate_from(self, execution_fingerprint: str, stage: str) -> None:
        stage_index = _stage_index(stage)
        with self._fingerprint_lock(execution_fingerprint):
            directory = self._fingerprint_directory(execution_fingerprint)
            changed = False
            for invalidated_stage in PIPELINE_STAGES[stage_index:]:
                self._cleanup_temporary_files(directory, invalidated_stage)
                try:
                    (directory / f"{invalidated_stage}.json").unlink()
                    changed = True
                except FileNotFoundError:
                    pass

            if changed:
                _fsync_directory(directory)
            try:
                directory.rmdir()
            except (FileNotFoundError, OSError):
                pass

    @contextmanager
    def _fingerprint_lock(self, input_fingerprint: str) -> Iterator[None]:
        directory = self._fingerprint_directory(input_fingerprint)
        lock_directory = self.root / ".locks"
        lock_directory.mkdir(parents=True, exist_ok=True)
        lock_path = lock_directory / f"{directory.name}.lock"
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _cleanup_temporary_files(directory: Path, stage: str) -> None:
        try:
            temporary_files = directory.glob(f".{stage}.*.tmp")
            for temporary_path in temporary_files:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    def _checkpoint_path(self, input_fingerprint: str, stage: str) -> Path:
        _stage_index(stage)
        return self._fingerprint_directory(input_fingerprint) / f"{stage}.json"

    def _fingerprint_directory(self, input_fingerprint: str) -> Path:
        if not isinstance(input_fingerprint, str) or not input_fingerprint:
            raise ValueError("input_fingerprint must be a non-empty string")
        storage_key = hashlib.sha256(input_fingerprint.encode("utf-8")).hexdigest()
        return self.root / storage_key[:2] / storage_key


FileCheckpointStore = FilesystemCheckpointStore
JsonCheckpointStore = FilesystemCheckpointStore
JSONCheckpointStore = FilesystemCheckpointStore


def _stage_index(stage: str) -> int:
    try:
        return PIPELINE_STAGES.index(stage)
    except ValueError as error:
        raise ValueError(f"Unknown pipeline stage: {stage}") from error


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


def _deserialize_checkpoint(
    payload: Any,
    execution_fingerprint: str,
    stage: str,
) -> StageExecution:
    if not isinstance(payload, dict):
        raise ValueError("Checkpoint payload must be an object")
    if payload.get("checkpoint_schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("Checkpoint schema version is incompatible")
    if payload.get("adapter_version") != ADAPTER_VERSION:
        raise ValueError("Checkpoint adapter version is incompatible")
    if payload.get("execution_fingerprint") != execution_fingerprint:
        raise ValueError("Checkpoint execution fingerprint does not match")
    if payload.get("stage") != stage:
        raise ValueError("Checkpoint stage does not match")

    execution = payload.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("Checkpoint execution must be an object")
    if set(execution) != {
        "result",
        "updates",
        "trace",
        "evidence_lineage",
        "schema_version",
    }:
        raise ValueError("Checkpoint execution is incomplete or contains unsupported fields")
    if execution.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        raise ValueError("Stage execution schema version is incompatible")

    result_payload = execution.get("result")
    if not isinstance(result_payload, dict) or result_payload.get("stage") != stage:
        raise ValueError("Checkpoint result stage does not match")
    result_fields = {field.name for field in fields(StageResult)}
    if not set(result_payload).issubset(result_fields):
        raise ValueError("Checkpoint result contains unsupported fields")

    updates = execution.get("updates", {})
    trace = execution.get("trace", {})
    if not isinstance(updates, dict) or not isinstance(trace, dict):
        raise ValueError("Checkpoint updates and trace must be objects")
    if "discovered_job_board" in updates:
        updates = dict(updates)
        updates["discovered_job_board"] = DiscoveredJobBoard.from_checkpoint_payload(
            updates["discovered_job_board"]
        )
    if "homepage_navigation_evidence" in updates:
        updates = dict(updates)
        updates["homepage_navigation_evidence"] = (
            HomepageNavigationEvidence.from_checkpoint_payload(
                updates["homepage_navigation_evidence"]
            )
        )

    lineage_payload = execution.get("evidence_lineage")
    lineage = (
        None
        if lineage_payload is None
        else StageEvidenceLineage.from_payload(lineage_payload)
    )
    if lineage is not None:
        if lineage.stage != stage:
            raise ValueError("Checkpoint lineage stage does not match")
        if lineage.execution_fingerprint != execution_fingerprint:
            raise ValueError("Checkpoint lineage execution fingerprint does not match")

    return StageExecution(
        result=StageResult(**result_payload),
        updates=updates,
        trace=trace,
        evidence_lineage=lineage,
        schema_version=execution["schema_version"],
    )
