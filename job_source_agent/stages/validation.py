from __future__ import annotations

import time
from collections import Counter
from typing import Protocol

from ..contracts import PipelineContext, StageExecution
from ..models import (
    STAGE_RESULT_VALIDATION,
)
from ..pipeline_status import derive_pipeline_status
from ..reasons import make_stage_result
from ..identity_continuity import validate_opening_identity_chain
from ..opening_selection_validation import validate_opening_selection


class ResultValidationService(Protocol):
    def validate(self, context: PipelineContext) -> list[str]:
        ...


class DefaultResultValidationService:
    def validate(self, context: PipelineContext) -> list[str]:
        counts = Counter(result.stage for result in context.stage_results)
        duplicates = sorted(stage for stage, count in counts.items() if count > 1)
        if duplicates:
            return ["Duplicate stage results were produced."]
        issues = validate_opening_identity_chain(
            hiring=context.hiring_identity_evidence,
            provider=context.provider_identity,
            opening=context.opening_identity,
            open_position_url=context.open_position_url,
            job_list_page_url=context.job_list_page_url,
        )
        selection_issues, _location = validate_opening_selection(
            selection=context.opening_selection_evidence,
            provider=context.provider_identity,
            opening=context.opening_identity,
            open_position_url=context.open_position_url,
            target_title=context.company.job_title,
            target_location=context.company.job_location,
        )
        return list(dict.fromkeys([*issues, *selection_issues]))


class ResultValidationStage:
    name = STAGE_RESULT_VALIDATION

    def __init__(self, service: ResultValidationService | None = None) -> None:
        self.service = service or DefaultResultValidationService()

    def run(self, context: PipelineContext) -> StageExecution:
        started = time.perf_counter()
        issues = self.service.validate(context)
        _selection_issues, location_classification = validate_opening_selection(
            selection=context.opening_selection_evidence,
            provider=context.provider_identity,
            opening=context.opening_identity,
            open_position_url=context.open_position_url,
            target_title=context.company.job_title,
            target_location=context.company.job_location,
        )
        pipeline_status = derive_pipeline_status(context.stage_results)
        if issues:
            detail = " ".join(issues)
            return StageExecution(
                result=make_stage_result(
                    self.name,
                    "failed",
                    reason_code=(
                        "RESULT_IDENTITY_MISMATCH"
                        if any(
                            issue.endswith("MISMATCH")
                            or issue.endswith("MISSING")
                            or issue.endswith("UNVERIFIED")
                            or issue == "OPENING_URL_INVALID"
                            for issue in issues
                        )
                        else "RESULT_VALIDATION_FAILED"
                    ),
                    duration_ms=_elapsed_ms(started),
                    input_count=1,
                    evidence=[{"field": "pipeline_status", "value": pipeline_status}],
                    detail=detail,
                ),
                trace={
                    "pipeline_status": pipeline_status,
                    "issues": issues,
                    "location_classification": location_classification,
                },
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
            trace={
                "pipeline_status": pipeline_status,
                "issues": [],
                "location_classification": location_classification,
            },
        )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
