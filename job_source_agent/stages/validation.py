from __future__ import annotations

import time
from collections import Counter
from typing import Protocol

from ..contracts import PipelineContext, StageExecution
from ..models import (
    STAGE_CAREER_DISCOVERY,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
    STAGE_RESULT_VALIDATION,
)
from ..reasons import make_stage_result


class ResultValidationService(Protocol):
    def validate(self, context: PipelineContext) -> list[str]:
        ...


class DefaultResultValidationService:
    def validate(self, context: PipelineContext) -> list[str]:
        counts = Counter(result.stage for result in context.stage_results)
        duplicates = sorted(stage for stage, count in counts.items() if count > 1)
        if duplicates:
            return ["Duplicate stage results were produced."]
        return []


class ResultValidationStage:
    name = STAGE_RESULT_VALIDATION

    def __init__(self, service: ResultValidationService | None = None) -> None:
        self.service = service or DefaultResultValidationService()

    def run(self, context: PipelineContext) -> StageExecution:
        started = time.perf_counter()
        issues = self.service.validate(context)
        pipeline_status = _pipeline_status(context)
        if issues:
            detail = " ".join(issues)
            return StageExecution(
                result=make_stage_result(
                    self.name,
                    "failed",
                    reason_code="RESULT_VALIDATION_FAILED",
                    duration_ms=_elapsed_ms(started),
                    input_count=1,
                    evidence=[{"field": "pipeline_status", "value": pipeline_status}],
                    detail=detail,
                ),
                trace={"pipeline_status": pipeline_status, "issues": issues},
            )

        return StageExecution(
            result=make_stage_result(
                self.name,
                "success",
                duration_ms=_elapsed_ms(started),
                input_count=1,
                output_count=1,
                evidence=[{"field": "pipeline_status", "value": pipeline_status}],
            ),
            trace={"pipeline_status": pipeline_status, "issues": []},
        )


def _pipeline_status(context: PipelineContext) -> str:
    statuses = {result.stage: result.status for result in context.stage_results}
    if statuses.get(STAGE_OPENING_MATCH) == "success":
        return "success"
    if statuses.get(STAGE_JOB_BOARD_DISCOVERY) == "success":
        return "partial" if statuses.get(STAGE_OPENING_MATCH) == "partial" else "success"
    if statuses.get(STAGE_CAREER_DISCOVERY) == "success":
        return "partial"
    if "unsupported" in statuses.values():
        return "unsupported"
    return "failed"


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
