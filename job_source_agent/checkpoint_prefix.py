from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
import re
from urllib.parse import urlparse

from .contracts import CheckpointStore, PipelineContext, StageExecution
from .models import (
    PIPELINE_STAGES,
    STAGE_CAREER_DISCOVERY,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
    STAGE_WEBSITE_RESOLUTION,
    StageResult,
)
from .web import safe_normalize_url


_AUTHORITATIVE_STATUSES = {"success", "not_applicable"}
_NOT_APPLICABLE_STAGES = {PIPELINE_STAGES[0], STAGE_OPENING_MATCH}
_REQUIRED_SUCCESS_OUTPUTS = {
    STAGE_WEBSITE_RESOLUTION: "company_website_url",
    STAGE_CAREER_DISCOVERY: "career_page_url",
    STAGE_JOB_BOARD_DISCOVERY: "job_list_page_url",
    STAGE_OPENING_MATCH: "open_position_url",
}
_URL_UPDATE_FIELDS = {
    "company_website_url",
    "career_root_url",
    "career_page_url",
    "job_list_page_url",
    "open_position_url",
}
_PUBLIC_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE)
_ENCODED_CONTROL = re.compile(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", re.IGNORECASE)


@dataclass(frozen=True)
class CheckpointPrefixDefect:
    stage: str
    defect_class: str
    detail: str

    def to_trace(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "defect_class": self.defect_class,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CheckpointPrefixInspection:
    requested_start: str
    effective_start: str
    executions: tuple[StageExecution, ...]
    defects: tuple[CheckpointPrefixDefect, ...]

    def trace_record(self, *, mode: str) -> dict:
        first_defect = self.defects[0] if self.defects else None
        effective_index = PIPELINE_STAGES.index(self.effective_start)
        return {
            "mode": mode,
            "requested_start": self.requested_start,
            "effective_start": self.effective_start,
            "defect_class": first_defect.defect_class if first_defect else None,
            "defect_stages": [defect.stage for defect in self.defects],
            "defects": [defect.to_trace() for defect in self.defects],
            "invalidated_suffix": list(PIPELINE_STAGES[effective_index:]),
        }


class CheckpointPrefixError(ValueError):
    """Raised when an explicit rerun lacks an authoritative checkpoint prefix."""

    def __init__(self, inspection: CheckpointPrefixInspection) -> None:
        self.inspection = inspection
        self.requested_start = inspection.requested_start
        self.defects = inspection.defects
        summary = ", ".join(
            f"{defect.stage}:{defect.defect_class}" for defect in inspection.defects
        )
        super().__init__(
            f"Checkpoint prefix before {inspection.requested_start!r} is not authoritative: "
            f"{summary}"
        )


def inspect_checkpoint_prefix(
    store: CheckpointStore,
    input_fingerprint: str,
    context: PipelineContext,
    requested_start: str,
) -> CheckpointPrefixInspection:
    """Inspect and simulate the complete checkpoint chain before a start boundary."""

    requested_index = PIPELINE_STAGES.index(requested_start)
    simulation = PipelineContext.from_company(context.company)
    executions: list[StageExecution] = []
    defects: list[CheckpointPrefixDefect] = []
    gap_found = False

    for stage in PIPELINE_STAGES[:requested_index]:
        execution = store.load(input_fingerprint, stage)
        defect = _validate_checkpoint(execution, stage)
        if defect is not None:
            defects.append(defect)
            gap_found = True
            continue
        if gap_found:
            continue

        assert execution is not None
        try:
            simulation.apply(execution)
        except (TypeError, ValueError):
            defects.append(
                CheckpointPrefixDefect(
                    stage=stage,
                    defect_class="context_apply_failed",
                    detail="Checkpoint updates could not be applied in sequence.",
                )
            )
            gap_found = True
            continue
        executions.append(execution)

    effective_start = defects[0].stage if defects else requested_start
    return CheckpointPrefixInspection(
        requested_start=requested_start,
        effective_start=effective_start,
        executions=tuple(executions),
        defects=tuple(defects),
    )


def _validate_checkpoint(
    execution: StageExecution | None,
    stage: str,
) -> CheckpointPrefixDefect | None:
    if execution is None:
        return CheckpointPrefixDefect(
            stage=stage,
            defect_class="missing_corrupt_or_incompatible",
            detail="The checkpoint store returned no compatible record.",
        )
    if (
        not isinstance(execution, StageExecution)
        or not isinstance(execution.result, StageResult)
        or execution.result.stage != stage
        or not isinstance(execution.updates, dict)
    ):
        return CheckpointPrefixDefect(
            stage=stage,
            defect_class="semantically_invalid",
            detail="The checkpoint execution does not match the requested stage.",
        )
    if execution.result.status not in _AUTHORITATIVE_STATUSES:
        return CheckpointPrefixDefect(
            stage=stage,
            defect_class="non_authoritative_status",
            detail="Checkpoint status is not authoritative for reuse.",
        )
    if (
        execution.result.status == "not_applicable"
        and stage not in _NOT_APPLICABLE_STAGES
    ):
        return CheckpointPrefixDefect(
            stage=stage,
            defect_class="non_authoritative_status",
            detail="Checkpoint status is not authoritative for this stage.",
        )

    required_field = _REQUIRED_SUCCESS_OUTPUTS.get(stage)
    if execution.result.status == "success" and required_field is not None:
        value = execution.updates.get(required_field)
        if not value:
            return CheckpointPrefixDefect(
                stage=stage,
                defect_class="missing_required_output",
                detail="Successful checkpoint is missing its required public URL output.",
            )
    for field_name in _URL_UPDATE_FIELDS:
        if field_name not in execution.updates or execution.updates[field_name] is None:
            continue
        if not _is_safe_normalized_public_url(execution.updates[field_name]):
            return CheckpointPrefixDefect(
                stage=stage,
                defect_class="unsafe_url_update",
                detail="Checkpoint contains an unsafe public URL update.",
            )
    return None


def _is_safe_normalized_public_url(value: object) -> bool:
    if not isinstance(value, str) or not value or _ENCODED_CONTROL.search(value):
        return False
    normalized = safe_normalize_url(value)
    if normalized is None or normalized != value:
        return False
    parsed = urlparse(normalized)
    try:
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and port != {"http": 80, "https": 443}[parsed.scheme])
    ):
        return False

    host = parsed.hostname.rstrip(".").casefold()
    try:
        return ip_address(host).is_global
    except ValueError:
        labels = host.split(".")
        return (
            len(labels) >= 2
            and host not in {"localhost", "localhost.localdomain"}
            and not host.endswith((".local", ".internal", ".localhost"))
            and all(_PUBLIC_HOST_LABEL.fullmatch(label) for label in labels)
        )
