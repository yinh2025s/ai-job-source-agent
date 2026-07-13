from __future__ import annotations

import json
import multiprocessing
import os
import pickle
import signal
import struct
import tempfile
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .artifact_publish import AttemptArtifactTransaction


_ENVELOPE_MAGIC = b"JSA_PROCESS_RESULT_V1\n"
_ENVELOPE_SCHEMA_VERSION = 1


class ProcessBudgetExceeded(TimeoutError):
    """Raised after a worker is forcibly stopped at its wall-clock deadline."""


class RemoteProcessError(RuntimeError):
    """Raised when a budgeted worker exits with an exception."""


def run_with_process_budget(
    function: Callable[..., Any],
    args: tuple[Any, ...],
    timeout: float,
    *,
    _notification_delay: float = 0.0,
) -> Any:
    """Run a picklable function in a process tree with a hard deadline.

    The worker publishes its potentially large result to an atomic local envelope.
    The pipe carries only a small readiness notification, avoiding a race where the
    operation finished within budget but blocked while sending its result to the
    parent. ``_notification_delay`` exists solely for deterministic race tests.
    """

    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    published_at = context.Value("d", 0.0)
    process_group_ready = context.Value("b", False)

    with tempfile.TemporaryDirectory(prefix="job-source-process-budget-") as directory:
        envelope_path = Path(directory) / "result.envelope"
        process = context.Process(
            target=_worker_entrypoint,
            args=(
                function,
                args,
                sender,
                str(envelope_path),
                published_at,
                process_group_ready,
                max(0.0, _notification_delay),
            ),
        )
        process.start()
        sender.close()
        deadline = time.monotonic() + max(0.0, timeout)

        try:
            notification_received = receiver.poll(max(0.0, deadline - time.monotonic()))
            if notification_received:
                try:
                    receiver.recv_bytes()
                except EOFError:
                    pass
            else:
                _stop_process_tree(process, process_group_ready)

            envelope = _read_published_envelope(envelope_path, published_at, deadline)
            if envelope is None:
                if not notification_received:
                    raise ProcessBudgetExceeded(f"operation exceeded {timeout:g} seconds")
                process.join(timeout=1)
                raise RemoteProcessError(
                    f"worker exited without a result (exit code {process.exitcode})"
                )
        finally:
            receiver.close()
            process.join(timeout=0.2)
            _stop_process_tree(process, process_group_ready)

        status, payload = envelope
        if status == "error":
            raise RemoteProcessError(str(payload))
        return payload


def _worker_entrypoint(
    function: Callable[..., Any],
    args: tuple[Any, ...],
    sender,
    envelope_path: str,
    published_at,
    process_group_ready,
    notification_delay: float,
) -> None:
    try:
        _become_process_group_leader(process_group_ready)
        try:
            result = function(*args)
            status = "ok"
            payload = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
        except BaseException:
            status = "error"
            payload = traceback.format_exc(limit=12).encode("utf-8", errors="replace")

        _write_envelope_atomically(Path(envelope_path), status, payload)
        with published_at.get_lock():
            published_at.value = time.monotonic()

        if notification_delay:
            time.sleep(notification_delay)
        try:
            sender.send_bytes(b"published")
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        sender.close()


def _become_process_group_leader(process_group_ready) -> None:
    if os.name != "posix":
        return
    try:
        os.setsid()
    except OSError:
        return
    with process_group_ready.get_lock():
        process_group_ready.value = True


def _write_envelope_atomically(path: Path, status: str, payload: bytes) -> None:
    metadata = json.dumps(
        {
            "schema_version": _ENVELOPE_SCHEMA_VERSION,
            "status": status,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    transaction = AttemptArtifactTransaction(path.parent, f"process-{os.getpid()}")
    staged_path = transaction.stage_path("result.envelope")
    try:
        with staged_path.open("xb") as handle:
            handle.write(_ENVELOPE_MAGIC)
            handle.write(struct.pack("!I", len(metadata)))
            handle.write(metadata)
            handle.write(payload)
        transaction.publish_file(staged_path, path)
    finally:
        transaction.abort()


def _read_published_envelope(path: Path, published_at, deadline: float) -> tuple[str, Any] | None:
    with published_at.get_lock():
        publication_time = float(published_at.value)
    if publication_time <= 0.0 or publication_time > deadline:
        return None

    try:
        with path.open("rb") as handle:
            if handle.read(len(_ENVELOPE_MAGIC)) != _ENVELOPE_MAGIC:
                return None
            metadata_length_raw = handle.read(4)
            if len(metadata_length_raw) != 4:
                return None
            metadata_length = struct.unpack("!I", metadata_length_raw)[0]
            if metadata_length > 4096:
                return None
            metadata = json.loads(handle.read(metadata_length))
            payload = handle.read()
    except (OSError, ValueError, json.JSONDecodeError):
        return None

    if not isinstance(metadata, dict):
        return None
    if metadata.get("schema_version") != _ENVELOPE_SCHEMA_VERSION:
        return None
    status = metadata.get("status")
    if status == "ok":
        try:
            return status, pickle.loads(payload)
        except (
            pickle.PickleError,
            EOFError,
            AttributeError,
            ImportError,
            IndexError,
            OverflowError,
            TypeError,
            ValueError,
        ):
            return None
    if status == "error":
        return status, payload.decode("utf-8", errors="replace")
    return None


def _stop_process_tree(process, process_group_ready) -> None:
    group_ready = False
    with process_group_ready.get_lock():
        group_ready = bool(process_group_ready.value)

    if group_ready and process.pid is not None:
        _signal_process_group(process.pid, signal.SIGTERM)
    elif process.is_alive():
        process.terminate()

    process.join(timeout=2)
    group_alive = group_ready and process.pid is not None and _process_group_exists(process.pid)
    if group_alive:
        _signal_process_group(process.pid, signal.SIGKILL)
    elif process.is_alive():
        process.kill()
    process.join(timeout=2)


def _signal_process_group(group_id: int, signal_number: int) -> None:
    try:
        os.killpg(group_id, signal_number)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _process_group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True
