from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .models import RESULT_SCHEMA_VERSION
from .source_posting import source_posting_fingerprint_payload


CHECKPOINT_SCHEMA_VERSION = "1.3"
ADAPTER_VERSION = "2026-07-14.68"

FINGERPRINT_FIELDS = (
    "company_name",
    "company_website_url",
    "hiring_entity_name",
    "career_root_url",
    "linkedin_job_url",
    "external_apply_url",
    "linkedin_company_url",
    "job_title",
    "job_location",
)


def input_fingerprint(record: dict[str, Any]) -> str:
    payload = {
        field: _normalize_value(record.get(field))
        for field in FINGERPRINT_FIELDS
        if record.get(field) not in (None, "")
    }
    source_posting = source_posting_fingerprint_payload(record.get("source_trace"))
    if source_posting:
        payload["source_posting"] = source_posting
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def execution_fingerprint(record: dict[str, Any], run_configuration_digest: str) -> str:
    if not isinstance(run_configuration_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", run_configuration_digest
    ):
        raise ValueError("run_configuration_digest must be a lowercase SHA-256 hex digest")
    payload = {
        "input_fingerprint": input_fingerprint(record),
        "run_configuration_digest": run_configuration_digest,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def checkpoint_metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "result_schema_version": record.get("result_schema_version") or RESULT_SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "input_fingerprint": input_fingerprint(record),
    }


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.strip().split())
    return value
