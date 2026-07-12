from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import RESULT_SCHEMA_VERSION


CHECKPOINT_SCHEMA_VERSION = "1.0"
ADAPTER_VERSION = "2026-07-12.8"

FINGERPRINT_FIELDS = (
    "company_name",
    "company_website_url",
    "hiring_entity_name",
    "career_root_url",
    "linkedin_job_url",
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
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
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
