#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.client
import importlib
import json
import os
from pathlib import Path
import socket
import sys
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_FILES = (
    "pyproject.toml",
    "job_source_agent/__init__.py",
    "job_source_agent/extension_bridge.py",
    "scripts/extension_bridge.py",
    "extension/manifest.json",
    "extension/content.js",
    "extension/popup.html",
    "extension/popup.js",
    "extension/popup.css",
    "extension/background.js",
)


class PreflightError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check and start the reviewer extension bridge."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Run preflight without starting the bridge.",
    )
    return parser


def _check_runtime(
    version_info: Sequence[int] | None = None,
    implementation: str | None = None,
) -> None:
    from job_source_agent.runtime import inspect_runtime

    status = inspect_runtime(version_info=version_info, implementation=implementation)
    if not status.release_compatible:
        found = f"{status.implementation} {'.'.join(map(str, status.version))}"
        raise PreflightError(
            f"Python check failed: found {found}; use CPython 3.12 "
            "(for example, make reviewer-start PYTHON=python3.12)."
        )


def _check_project_files(root: Path) -> None:
    missing = [relative for relative in REQUIRED_FILES if not (root / relative).is_file()]
    if missing:
        raise PreflightError(
            f"Project check failed: missing {missing[0]}; use the complete release package."
        )
    try:
        manifest = json.loads(
            (root / "extension" / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightError("Extension check failed: manifest.json is not readable JSON.") from exc
    if manifest.get("manifest_version") != 3:
        raise PreflightError("Extension check failed: manifest.json must use Manifest V3.")
    try:
        importlib.import_module("job_source_agent.extension_bridge")
        importlib.import_module("scripts.extension_bridge")
    except (ImportError, OSError, SyntaxError) as exc:
        raise PreflightError(
            "Project check failed: bridge modules could not be loaded; "
            "use the complete release package."
        ) from exc


def _validate_endpoint(host: str, port: int) -> tuple[str, int]:
    from job_source_agent.extension_bridge import validate_loopback_host

    try:
        validated_host = validate_loopback_host(host)
    except (TypeError, ValueError) as exc:
        raise PreflightError(
            "Bridge host must be loopback: use 127.0.0.1 or localhost."
        ) from exc
    if not 1 <= port <= 65535:
        raise PreflightError("Bridge port must be an integer from 1 to 65535.")
    return validated_host, port


def _port_is_occupied(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _looks_like_existing_bridge(host: str, port: int) -> bool:
    connection = http.client.HTTPConnection(host, port, timeout=0.5)
    try:
        connection.request("GET", "/v1/health")
        response = connection.getresponse()
        body = response.read(1024)
        payload = json.loads(body.decode("utf-8"))
        return (
            response.status == 401
            and payload == {"error": "unauthorized"}
            and response.getheader("Cache-Control") == "no-store"
            and (response.getheader("Content-Type") or "").startswith("application/json")
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    finally:
        connection.close()


def _check_port(
    host: str,
    port: int,
    *,
    occupied: Callable[[str, int], bool] = _port_is_occupied,
    existing_bridge: Callable[[str, int], bool] = _looks_like_existing_bridge,
) -> None:
    if not occupied(host, port):
        return
    if existing_bridge(host, port):
        raise PreflightError(
            f"Port {port} already has a Job Source Agent bridge. "
            "Use that running bridge or stop it before starting another."
        )
    raise PreflightError(
        f"Port {port} is in use. Stop that process or run "
        f"make reviewer-start REVIEWER_PORT=<free-port>."
    )


def bridge_command(python: str, host: str, port: int) -> list[str]:
    return [
        python,
        "-m",
        "scripts.extension_bridge",
        "--host",
        host,
        "--port",
        str(port),
        "--workers",
        "4",
        "--fetch-timeout",
        "8",
    ]


def run(
    argv: list[str] | None = None,
    *,
    root: Path = ROOT,
    version_info: Sequence[int] | None = None,
    implementation: str | None = None,
    occupied: Callable[[str, int], bool] = _port_is_occupied,
    existing_bridge: Callable[[str, int], bool] = _looks_like_existing_bridge,
    execv: Callable[[str, list[str]], object] = os.execv,
) -> int:
    args = build_parser().parse_args(argv)
    _check_project_files(root)
    _check_runtime(version_info=version_info, implementation=implementation)
    host, port = _validate_endpoint(args.host, args.port)
    _check_port(
        host,
        port,
        occupied=occupied,
        existing_bridge=existing_bridge,
    )
    print(f"Preflight passed: CPython 3.12, extension files, {host}:{port}.")
    if args.check_only:
        print("Reviewer check complete; bridge was not started.")
        return 0

    command = bridge_command(sys.executable, host, port)
    print("Starting Job Source Agent bridge; press Ctrl-C to stop.", flush=True)
    execv(sys.executable, command)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except PreflightError as exc:
        print(f"reviewer-start: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
