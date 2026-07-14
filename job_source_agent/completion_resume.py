from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import PIPELINE_STAGES


@dataclass(frozen=True)
class CompletionResumeDecision:
    action: str
    reason: str
    retry_stage: str | None = None
    reason_code: str | None = None


def classify_completion_resume(
    result: dict[str, Any],
    trace: dict[str, Any],
) -> CompletionResumeDecision:
    """Decide whether a compatible batch completion may be restored or retried."""

    if result.get("pipeline_status") == "success":
        return CompletionResumeDecision("completion_restore", "pipeline_success")

    stages = trace.get("stages")
    if not isinstance(stages, list):
        stages = result.get("stages")
    if not isinstance(stages, list) or len(stages) != len(PIPELINE_STAGES):
        return CompletionResumeDecision("unclassified_restore", "invalid_stage_chain")

    first_non_success: dict[str, Any] | None = None
    for expected_stage, stage_result in zip(PIPELINE_STAGES, stages):
        if not isinstance(stage_result, dict) or stage_result.get("stage") != expected_stage:
            return CompletionResumeDecision("unclassified_restore", "invalid_stage_chain")
        status = stage_result.get("status")
        if not isinstance(status, str):
            return CompletionResumeDecision("unclassified_restore", "invalid_stage_chain")
        if status not in {"success", "not_applicable"}:
            first_non_success = stage_result
            break

    if first_non_success is None:
        return CompletionResumeDecision("unclassified_restore", "inconsistent_pipeline_status")
    if first_non_success.get("status") == "not_run":
        return CompletionResumeDecision("unclassified_restore", "missing_failure_stage")

    retryable = first_non_success.get("retryable")
    if type(retryable) is not bool:
        return CompletionResumeDecision("unclassified_restore", "missing_retryability")

    retry_stage = str(first_non_success["stage"])
    reason_code = first_non_success.get("reason_code")
    if reason_code is not None and not isinstance(reason_code, str):
        return CompletionResumeDecision("unclassified_restore", "invalid_reason_code")
    if retryable:
        return CompletionResumeDecision(
            "retryable_resubmit",
            "retryable_stage_failure",
            retry_stage=retry_stage,
            reason_code=reason_code,
        )
    return CompletionResumeDecision(
        "non_retryable_restore",
        "non_retryable_stage_outcome",
        retry_stage=retry_stage,
        reason_code=reason_code,
    )


def completion_resume_marker(decision: CompletionResumeDecision) -> dict[str, str]:
    marker = {
        "action": decision.action,
        "reason": decision.reason,
    }
    if decision.retry_stage:
        marker["stage"] = decision.retry_stage
    if decision.reason_code:
        marker["reason_code"] = decision.reason_code
    return marker
