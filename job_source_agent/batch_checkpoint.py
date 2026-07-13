from __future__ import annotations

import json
import math
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import fcntl

from .checkpoint import ADAPTER_VERSION, execution_fingerprint, input_fingerprint
from .run_configuration import AgentConfig, DeterministicRunConfig


BATCH_COMPLETION_SCHEMA_VERSION = "1.1"


@dataclass(frozen=True)
class BatchCompletion:
    input_fingerprint: str
    execution_fingerprint: str
    result: dict[str, Any]
    trace: dict[str, Any]
    elapsed: float


class FilesystemBatchCompletionStore:
    """Persist completed company runs as versioned, atomic JSON envelopes."""

    def __init__(
        self,
        root: str | Path,
        run_configuration: DeterministicRunConfig | None = None,
        completion_scope_digest: str | None = None,
    ) -> None:
        self.root = Path(root)
        self.run_configuration = run_configuration or DeterministicRunConfig.from_agent_config(
            AgentConfig()
        )
        self.completion_scope_digest = completion_scope_digest or self.run_configuration.digest

    def fingerprint(self, input_record: dict[str, Any]) -> str:
        return execution_fingerprint(input_record, self.completion_scope_digest)

    def save(
        self,
        input_record: dict[str, Any],
        result: dict[str, Any],
        trace: dict[str, Any],
        elapsed: float,
    ) -> BatchCompletion:
        completion = _validate_completion(
            input_fingerprint(input_record),
            self.fingerprint(input_record),
            result,
            trace,
            elapsed,
        )
        path = self._completion_path(completion.execution_fingerprint)
        payload = {
            "batch_completion_schema_version": BATCH_COMPLETION_SCHEMA_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "input_fingerprint": completion.input_fingerprint,
            "execution_fingerprint": completion.execution_fingerprint,
            "result": completion.result,
            "trace": completion.trace,
            "elapsed": completion.elapsed,
        }

        with self._completion_lock(completion.execution_fingerprint):
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=path.parent,
                    prefix=f".{completion.execution_fingerprint}.",
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

        return completion

    def load(self, input_record: dict[str, Any]) -> BatchCompletion | None:
        fingerprint = self.fingerprint(input_record)
        with self._completion_lock(fingerprint):
            return self._load_path(
                self._completion_path(fingerprint),
                fingerprint,
                input_fingerprint(input_record),
            )

    def scan(self, input_records: Iterable[dict[str, Any]]) -> dict[str, BatchCompletion]:
        """Return compatible completions keyed by fingerprint for expected inputs."""
        completions: dict[str, BatchCompletion] = {}
        for input_record in input_records:
            fingerprint = self.fingerprint(input_record)
            if fingerprint in completions:
                continue
            completion = self.load(input_record)
            if completion is not None:
                completions[fingerprint] = completion
        return completions

    @contextmanager
    def _completion_lock(self, fingerprint: str) -> Iterator[None]:
        lock_directory = self.root / ".locks"
        lock_directory.mkdir(parents=True, exist_ok=True)
        lock_path = lock_directory / f"{fingerprint}.lock"
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _completion_path(self, fingerprint: str) -> Path:
        return self.root / fingerprint[:2] / f"{fingerprint}.json"

    @staticmethod
    def _load_path(
        path: Path,
        expected_fingerprint: str,
        expected_input_fingerprint: str,
    ) -> BatchCompletion | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return _deserialize_completion(
                payload,
                expected_fingerprint,
                expected_input_fingerprint,
            )
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return None


BatchCompletionStore = FilesystemBatchCompletionStore


def _deserialize_completion(
    payload: Any,
    expected_fingerprint: str,
    expected_input_fingerprint: str,
) -> BatchCompletion:
    if not isinstance(payload, dict):
        raise ValueError("Batch completion envelope must be an object")
    if set(payload) != {
        "batch_completion_schema_version",
        "adapter_version",
        "input_fingerprint",
        "execution_fingerprint",
        "result",
        "trace",
        "elapsed",
    }:
        raise ValueError("Batch completion envelope is incomplete or contains unsupported fields")
    if payload["batch_completion_schema_version"] != BATCH_COMPLETION_SCHEMA_VERSION:
        raise ValueError("Batch completion schema version is incompatible")
    if payload["adapter_version"] != ADAPTER_VERSION:
        raise ValueError("Batch completion adapter version is incompatible")
    if payload["execution_fingerprint"] != expected_fingerprint:
        raise ValueError("Batch completion execution fingerprint does not match")
    if payload["input_fingerprint"] != expected_input_fingerprint:
        raise ValueError("Batch completion input fingerprint does not match")
    return _validate_completion(
        payload["input_fingerprint"],
        expected_fingerprint,
        payload["result"],
        payload["trace"],
        payload["elapsed"],
    )


def _validate_completion(
    input_fingerprint_value: str,
    execution_fingerprint_value: str,
    result: Any,
    trace: Any,
    elapsed: Any,
) -> BatchCompletion:
    if not isinstance(result, dict) or not isinstance(trace, dict):
        raise ValueError("Batch completion result and trace must be objects")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
        raise ValueError("Batch completion elapsed must be a number")
    elapsed_value = float(elapsed)
    if not math.isfinite(elapsed_value) or elapsed_value < 0:
        raise ValueError("Batch completion elapsed must be finite and non-negative")
    try:
        json.dumps(result, ensure_ascii=True)
        json.dumps(trace, ensure_ascii=True)
    except (TypeError, ValueError) as error:
        raise ValueError("Batch completion result and trace must be JSON serializable") from error
    return BatchCompletion(
        input_fingerprint=input_fingerprint_value,
        execution_fingerprint=execution_fingerprint_value,
        result=result,
        trace=trace,
        elapsed=elapsed_value,
    )


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
