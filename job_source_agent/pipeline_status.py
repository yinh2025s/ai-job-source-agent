from __future__ import annotations

from collections.abc import Iterable

from .models import (
    STAGE_CAREER_DISCOVERY,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
    STAGE_RESULT_VALIDATION,
    StageResult,
)


def derive_pipeline_status(stage_results: Iterable[StageResult]) -> str:
    """Apply the single product-level stage status policy."""

    statuses = {result.stage: result.status for result in stage_results}
    if statuses.get(STAGE_RESULT_VALIDATION) in {"failed", "unsupported"}:
        return "failed"
    if statuses.get(STAGE_OPENING_MATCH) == "success":
        return "success"
    if statuses.get(STAGE_JOB_BOARD_DISCOVERY) == "success":
        return "partial" if statuses.get(STAGE_OPENING_MATCH) == "partial" else "success"
    if statuses.get(STAGE_CAREER_DISCOVERY) == "success":
        return "partial"
    if "partial" in statuses.values():
        return "partial"
    if "unsupported" in statuses.values():
        return "unsupported"
    return "failed"
