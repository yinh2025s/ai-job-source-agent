from __future__ import annotations

import multiprocessing
import traceback
from collections.abc import Callable
from typing import Any


class ProcessBudgetExceeded(TimeoutError):
    """Raised after a worker is forcibly stopped at its wall-clock deadline."""


class RemoteProcessError(RuntimeError):
    """Raised when a budgeted worker exits with an exception."""


def run_with_process_budget(
    function: Callable[..., Any],
    args: tuple[Any, ...],
    timeout: float,
) -> Any:
    """Run a picklable function in a killable process with a hard deadline."""

    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker_entrypoint, args=(function, args, sender))
    process.start()
    sender.close()
    try:
        if not receiver.poll(max(0.0, timeout)):
            _stop_process(process)
            raise ProcessBudgetExceeded(f"operation exceeded {timeout:g} seconds")
        try:
            status, payload = receiver.recv()
        except EOFError as exc:
            process.join(timeout=1)
            raise RemoteProcessError(f"worker exited without a result (exit code {process.exitcode})") from exc
    finally:
        receiver.close()
        process.join(timeout=2)
        if process.is_alive():
            _stop_process(process)

    if status == "error":
        raise RemoteProcessError(str(payload))
    return payload


def _worker_entrypoint(function: Callable[..., Any], args: tuple[Any, ...], sender) -> None:
    try:
        sender.send(("ok", function(*args)))
    except BaseException:
        sender.send(("error", traceback.format_exc(limit=12)))
    finally:
        sender.close()


def _stop_process(process) -> None:
    if not process.is_alive():
        process.join(timeout=1)
        return
    process.terminate()
    process.join(timeout=2)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)
