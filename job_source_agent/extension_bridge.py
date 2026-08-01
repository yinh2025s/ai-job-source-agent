from __future__ import annotations

import hmac
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .composition import (
    LINKEDIN_EVIDENCE_CACHE_FILENAME,
    AgentConfig,
    FetcherConfig,
    build_application,
)
from .linkedin import company_inputs_from_records
from .models import dataclass_to_dict


MAX_REQUEST_BYTES = 256 * 1024
MAX_PAIR_REQUEST_BYTES = 512
MAX_RECORDS = 30
PAIRING_CLIENT_ID = "ai-job-source-agent-extension"
PAIRING_PROTOCOL_VERSION = "1"
PAIRING_WINDOW_SECONDS: float | None = None
COMPANY_DISCOVERY_EVIDENCE_FILENAME = "company-discovery-evidence.json"

_CHROME_EXTENSION_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}\Z")
_PAIRABLE_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")


@dataclass(frozen=True)
class ExtensionBridgeConfig:
    fetcher: FetcherConfig
    agent: AgentConfig = AgentConfig(enable_parallel_candidate_discovery=True)
    workers: int = 2
    output_dir: Path | None = None
    company_discovery_evidence_path: Path | None = None


class ExtensionRunManager:
    """Own asynchronous extension runs without leaking HTTP concerns into the pipeline."""

    def __init__(self, config: ExtensionBridgeConfig) -> None:
        self.config = config
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._lock = threading.Lock()
        self._runs: dict[str, dict[str, Any]] = {}

    def submit(self, records: list[dict]) -> str:
        if not records:
            raise ValueError("At least one job record is required.")
        if len(records) > MAX_RECORDS:
            raise ValueError(f"A browser run supports at most {MAX_RECORDS} records.")
        companies = company_inputs_from_records(records)
        run_id = uuid.uuid4().hex
        with self._lock:
            self._runs[run_id] = {
                "run_id": run_id,
                "status": "queued",
                "submitted": len(companies),
                "completed": 0,
            }
        try:
            self._executor.submit(self._execute, run_id, companies)
        except RuntimeError:
            self._update(
                run_id,
                status="failed",
                error="bridge_executor_unavailable",
            )
        return run_id

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return json.loads(json.dumps(run)) if run is not None else None

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _execute(self, run_id: str, companies) -> None:
        self._update(run_id, status="running")
        try:
            results = self._discover_companies(run_id, companies)
            records = [result.result_record() for result in results]
            traces = [dataclass_to_dict(result.trace_record()) for result in results]
            payload = {
                "run_id": run_id,
                "status": "complete",
                "submitted": len(companies),
                "completed": len(companies),
                "summary": _summarize(records),
                "results": records,
            }
            self._write_artifacts(run_id, records, traces, payload["summary"])
            self._replace(run_id, payload)
        except Exception as exc:
            self._update(run_id, status="failed", error=f"{type(exc).__name__}: {exc}")

    def _discover_companies(self, run_id: str, companies) -> list:
        ordered_results: list[Any | None] = [None] * len(companies)
        worker_count = min(max(1, self.config.workers), len(companies))
        if worker_count == 1:
            for index, company in enumerate(companies):
                ordered_results[index] = self._discover_company(company)
                self._update(run_id, completed=index + 1)
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(self._discover_company, company): index
                    for index, company in enumerate(companies)
                }
                completed = 0
                for future in as_completed(futures):
                    ordered_results[futures[future]] = future.result()
                    completed += 1
                    self._update(run_id, completed=completed)
        return [result for result in ordered_results if result is not None]

    def _discover_company(self, company):
        application = build_application(
            self.config.fetcher,
            self.config.agent,
            linkedin_evidence_cache_path=(
                self.config.output_dir / LINKEDIN_EVIDENCE_CACHE_FILENAME
                if self.config.output_dir is not None
                else None
            ),
            company_discovery_evidence_path=(
                self.config.company_discovery_evidence_path
                if self.config.company_discovery_evidence_path is not None
                else (
                    self.config.output_dir / COMPANY_DISCOVERY_EVIDENCE_FILENAME
                    if self.config.output_dir is not None
                    else None
                )
            ),
        )
        return application.pipeline.discover(company)

    def _write_artifacts(
        self,
        run_id: str,
        results: list[dict],
        traces: list[dict],
        summary: dict,
    ) -> None:
        if self.config.output_dir is None:
            return
        run_dir = self.config.output_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(run_dir / "results.json", results)
        _write_json_atomic(run_dir / "trace.json", traces)
        _write_json_atomic(run_dir / "summary.json", summary)

    def _replace(self, run_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._runs[run_id] = payload

    def _update(self, run_id: str, **updates: Any) -> None:
        with self._lock:
            self._runs[run_id].update(updates)


class ExtensionBridgeServer(ThreadingHTTPServer):
    def __init__(
        self,
        address,
        manager: ExtensionRunManager,
        token: str,
        *,
        pairing_enabled: bool = True,
        pairing_window_seconds: float | None = PAIRING_WINDOW_SECONDS,
        monotonic=time.monotonic,
    ) -> None:
        if not token:
            raise ValueError("A non-empty bridge token is required.")
        if pairing_enabled and not _PAIRABLE_TOKEN.fullmatch(token):
            raise ValueError("Automatic pairing requires a 32-128 character URL-safe token.")
        super().__init__(address, ExtensionBridgeHandler)
        self.manager = manager
        self.token = token
        self.pairing_enabled = pairing_enabled
        self.pairing_window_seconds = pairing_window_seconds
        self._monotonic = monotonic
        self._pairing_started_at = monotonic()
        self._pairing_lock = threading.Lock()
        self._paired_origin: str | None = None

    @property
    def paired_origin(self) -> str | None:
        with self._pairing_lock:
            return self._paired_origin

    def pair(self, origin: str) -> tuple[str, str | None]:
        """Claim or recover pairing, returning a typed status and optional token."""
        with self._pairing_lock:
            if not self.pairing_enabled:
                return "pairing_disabled", None
            if self._paired_origin is not None:
                if hmac.compare_digest(origin, self._paired_origin):
                    return "paired", self.token
                return "pairing_conflict", None
            if self.pairing_window_seconds is not None:
                elapsed = self._monotonic() - self._pairing_started_at
                if elapsed > self.pairing_window_seconds:
                    return "pairing_expired", None
            self._paired_origin = origin
            return "paired", self.token

    def claim_authenticated_origin(self, origin: str) -> bool:
        """Preserve manual-token use while pinning the first authenticated extension."""
        with self._pairing_lock:
            if self._paired_origin is None:
                self._paired_origin = origin
                return True
            return hmac.compare_digest(origin, self._paired_origin)

    def allows_cors_origin(self, origin: str | None) -> bool:
        if not is_chrome_extension_origin(origin):
            return False
        with self._pairing_lock:
            return self._paired_origin is None or hmac.compare_digest(
                origin, self._paired_origin
            )


class ExtensionBridgeHandler(BaseHTTPRequestHandler):
    server: ExtensionBridgeServer

    def do_OPTIONS(self) -> None:  # noqa: N802
        origin = self.headers.get("Origin")
        pairing_preflight = self.path == "/v1/pair" and is_chrome_extension_origin(origin)
        if not pairing_preflight and not self.server.allows_cors_origin(origin):
            self._json_response(403, {"error": "origin_not_allowed"})
            return
        self.send_response(204)
        self._cors_headers(origin if pairing_preflight else None)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        if self.path == "/v1/health":
            self._json_response(200, {"status": "ok"})
            return
        prefix = "/v1/runs/"
        if self.path.startswith(prefix):
            run = self.server.manager.get(self.path[len(prefix) :])
            self._json_response(200, run) if run else self._json_response(404, {"error": "run_not_found"})
            return
        self._json_response(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/v1/pair":
            self._pair()
            return
        if not self._authorized():
            return
        if self.path != "/v1/runs":
            self._json_response(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._json_response(413, {"error": "invalid_request_size"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            records = payload.get("records") if isinstance(payload, dict) else None
            run_id = self.server.manager.submit(records)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._json_response(400, {"error": "invalid_request", "detail": str(exc)})
            return
        run = self.server.manager.get(run_id)
        response = {
            key: run[key]
            for key in ("run_id", "status", "submitted", "completed", "error")
            if run is not None and key in run
        }
        self._json_response(202, response)

    def _pair(self) -> None:
        origin = self.headers.get("Origin")
        if not is_chrome_extension_origin(origin):
            self._json_response(403, {"error": "origin_not_allowed"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_PAIR_REQUEST_BYTES:
            self._json_response(413, {"error": "invalid_request_size"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json_response(400, {"error": "invalid_pairing_request"})
            return
        expected = {
            "client": PAIRING_CLIENT_ID,
            "protocol_version": PAIRING_PROTOCOL_VERSION,
        }
        if payload != expected:
            self._json_response(400, {"error": "invalid_pairing_request"})
            return
        status, token = self.server.pair(origin)
        if status == "pairing_disabled":
            self._json_response(403, {"error": status}, cors_origin=origin)
            return
        if status == "pairing_conflict":
            self._json_response(409, {"error": status}, cors_origin=origin)
            return
        if status == "pairing_expired":
            self._json_response(410, {"error": status})
            return
        self._json_response(
            200,
            {
                "status": status,
                "protocol_version": PAIRING_PROTOCOL_VERSION,
                "token": token,
            },
        )

    def log_message(self, format: str, *args) -> None:
        return

    def _authorized(self) -> bool:
        origin = self.headers.get("Origin")
        if not is_allowed_origin(origin, self.server.paired_origin):
            self._json_response(403, {"error": "origin_not_allowed"})
            return False
        supplied = self.headers.get("Authorization") or ""
        if not has_valid_bearer(supplied, self.server.token):
            self._json_response(401, {"error": "unauthorized"})
            return False
        if origin is not None and not self.server.claim_authenticated_origin(origin):
            self._json_response(403, {"error": "origin_not_allowed"})
            return False
        return True

    def _cors_headers(self, cors_origin: str | None = None) -> None:
        origin = cors_origin or self.headers.get("Origin")
        if (
            cors_origin and is_chrome_extension_origin(cors_origin)
        ) or self.server.allows_cors_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _json_response(
        self,
        status: int,
        payload: Any,
        *,
        cors_origin: str | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self._cors_headers(cors_origin)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def validate_loopback_host(host: str) -> str:
    parsed = urlparse(f"http://{host}")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Extension bridge must bind to a loopback host.")
    return host


def is_chrome_extension_origin(origin: str | None) -> bool:
    return bool(origin and _CHROME_EXTENSION_ORIGIN.fullmatch(origin))


def is_allowed_origin(origin: str | None, paired_origin: str | None = None) -> bool:
    if origin is None:
        return True
    if not is_chrome_extension_origin(origin):
        return False
    return paired_origin is None or hmac.compare_digest(origin, paired_origin)


def has_valid_bearer(authorization: str, token: str) -> bool:
    return hmac.compare_digest(authorization, f"Bearer {token}")


def _summarize(records: list[dict]) -> dict[str, Any]:
    total = len(records)
    with_job_list = sum(bool(record.get("job_list_page_url")) for record in records)
    with_opening = sum(bool(record.get("open_position_url")) for record in records)
    successful = sum(record.get("pipeline_status") == "success" for record in records)
    return {
        "total": total,
        "success": successful,
        "with_job_list": with_job_list,
        "with_opening": with_opening,
        "rates": {
            "job_list": with_job_list / total if total else 0.0,
            "opening": with_opening / total if total else 0.0,
        },
    }


def _write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
