from __future__ import annotations

from collections.abc import Iterable

from .contracts import (
    CheckpointStore,
    EvidenceCaptureCoordinator,
    PipelineContext,
    Stage,
    StageExecution,
)
from .checkpoint_prefix import CheckpointPrefixError, inspect_checkpoint_prefix
from .evidence_scope import StageEvidenceLineage
from .models import PIPELINE_STAGES, StageResult


class ApplicationRunner:
    """Run injected pipeline stages with a canonical, resumable result shape.

    The runner owns orchestration only. Stage implementations retain ownership of
    domain failures and context updates, while this class guarantees that every
    run produces one result per standard pipeline stage in standard order.
    """

    def __init__(
        self,
        stages: Iterable[Stage],
        checkpoint_store: CheckpointStore | None = None,
        capture_coordinator: EvidenceCaptureCoordinator | None = None,
    ) -> None:
        by_name: dict[str, Stage] = {}
        for stage in stages:
            name = getattr(stage, "name", None)
            if name not in PIPELINE_STAGES:
                raise ValueError(f"Unknown pipeline stage: {name!r}")
            if name in by_name:
                raise ValueError(f"Duplicate pipeline stage: {name}")
            by_name[name] = stage

        self.stages = tuple(by_name[name] for name in PIPELINE_STAGES if name in by_name)
        self._stages_by_name = by_name
        self._checkpoint_store = checkpoint_store
        self._capture_coordinator = capture_coordinator

    @property
    def checkpointing_enabled(self) -> bool:
        """Return whether this runner was configured with checkpoint persistence."""

        return self._checkpoint_store is not None

    def run(
        self,
        context: PipelineContext,
        *,
        start_at: str | None = None,
        stop_after: str | None = None,
        input_fingerprint: str | None = None,
        rerun_from: str | None = None,
        execution_fingerprint: str | None = None,
        producer_attempt_id: str | None = None,
    ) -> PipelineContext:
        """Execute an inclusive stage range and return the mutated context.

        Results before ``start_at`` are reused when present, which lets a caller
        resume from a hydrated context. Results in the selected range are always
        recomputed. Results after ``stop_after`` are deterministically marked
        ``not_run`` so stale downstream checkpoint results cannot leak into a run.
        """

        self._validate_checkpoint_configuration(input_fingerprint, rerun_from=rerun_from)
        if rerun_from is not None:
            _stage_index(rerun_from, option="rerun_from")
            if start_at is not None and start_at != rerun_from:
                raise ValueError(
                    "start_at and rerun_from must identify the same stage when both are supplied"
                )
            start_at = rerun_from

        start_at = start_at or PIPELINE_STAGES[0]
        stop_after = stop_after or PIPELINE_STAGES[-1]
        start_index = _stage_index(start_at, option="start_at")
        stop_index = _stage_index(stop_after, option="stop_after")
        if start_index > stop_index:
            raise ValueError(
                f"start_at stage {start_at!r} comes after stop_after stage {stop_after!r}"
            )
        checkpoint_preflight = None
        if self._checkpoint_store is not None:
            assert self._checkpoint_store is not None
            assert input_fingerprint is not None
            checkpoint_preflight = inspect_checkpoint_prefix(
                self._checkpoint_store,
                input_fingerprint,
                context,
                start_at,
            )
            if rerun_from is not None and checkpoint_preflight.defects:
                raise CheckpointPrefixError(checkpoint_preflight)
            start_at = checkpoint_preflight.effective_start
            start_index = _stage_index(start_at, option="start_at")
            self._checkpoint_store.invalidate_from(input_fingerprint, start_at)
            _reset_checkpoint_context(context)
            context.trace["checkpoint_prefix"] = checkpoint_preflight.trace_record(
                mode="rerun" if rerun_from is not None else "resume"
            )
            _record_checkpoint_event(context, start_at, "invalidate_from")

        previous_results = _index_existing_results(context)
        previous_traces = dict(context.trace.get("stages", {}))
        previous_lineage = dict(context.stage_evidence_lineage)
        context.stage_results = []
        context.stage_evidence_lineage = {}
        context.trace["stages"] = {}

        for index, stage_name in enumerate(PIPELINE_STAGES):
            if index < start_index:
                checkpoint = (
                    checkpoint_preflight.executions[index]
                    if checkpoint_preflight is not None
                    else self._load_checkpoint(input_fingerprint, stage_name)
                )
                if checkpoint is not None:
                    _validate_execution(checkpoint, stage_name, source="Checkpoint")
                    context.apply(checkpoint)
                    _record_checkpoint_event(context, stage_name, "restore")
                elif (previous := previous_results.get(stage_name)) is not None:
                    context.apply(StageExecution(
                        result=previous,
                        trace=previous_traces.get(stage_name, {}),
                        evidence_lineage=previous_lineage.get(stage_name),
                    ))
                else:
                    if self._checkpoint_store is not None:
                        _record_checkpoint_event(context, stage_name, "miss")
                    context.apply(
                        _not_run(
                            stage_name,
                            "Stage is before the requested start_at boundary.",
                            scheduler_reason="before_start_at",
                        )
                    )
                continue

            if index > stop_index:
                context.apply(
                    _not_run(
                        stage_name,
                        f"Stage is after the requested stop_after boundary ({stop_after}).",
                        scheduler_reason="after_stop_after",
                    )
                )
                continue

            stage = self._stages_by_name.get(stage_name)
            if stage is None:
                context.apply(
                    _not_run(
                        stage_name,
                        "No stage implementation was supplied to the application runner.",
                        scheduler_reason="implementation_not_supplied",
                    )
                )
                continue

            snapshot_scope = None
            try:
                if self._capture_coordinator is not None:
                    if execution_fingerprint is None or producer_attempt_id is None:
                        raise ValueError(
                            "Scoped capture requires execution_fingerprint and producer_attempt_id"
                        )
                    self._capture_coordinator.begin_stage(
                        producer_attempt_id,
                        execution_fingerprint,
                        stage_name,
                    )
                execution = stage.run(context)
                _validate_execution(execution, stage_name, source="Stage")
                if self._capture_coordinator is not None:
                    snapshot_scope = self._capture_coordinator.finalize()
            except BaseException:
                if self._capture_coordinator is not None:
                    self._capture_coordinator.abort_stage()
                raise
            if execution_fingerprint is not None and producer_attempt_id is not None:
                lineage_attempt_id = (
                    snapshot_scope.capture_attempt_id
                    if snapshot_scope is not None
                    else producer_attempt_id
                )
                execution.evidence_lineage = StageEvidenceLineage(
                    stage=stage_name,
                    execution_fingerprint=execution_fingerprint,
                    producer_attempt_id=lineage_attempt_id,
                    snapshot_scope=snapshot_scope,
                )
            context.apply(execution)
            if self._checkpoint_store is not None:
                assert input_fingerprint is not None
                self._checkpoint_store.save(input_fingerprint, execution)
                _record_checkpoint_event(context, stage_name, "save")

        return context

    def _validate_checkpoint_configuration(
        self,
        input_fingerprint: str | None,
        *,
        rerun_from: str | None,
    ) -> None:
        if self._checkpoint_store is None and input_fingerprint is not None:
            raise ValueError("input_fingerprint requires a checkpoint_store")
        if self._checkpoint_store is not None and (
            not isinstance(input_fingerprint, str) or not input_fingerprint
        ):
            raise ValueError(
                "input_fingerprint must be a non-empty string when checkpoint_store is configured"
            )
        if self._checkpoint_store is None and rerun_from is not None:
            raise ValueError("rerun_from requires a checkpoint_store")

    def _load_checkpoint(
        self,
        input_fingerprint: str | None,
        stage_name: str,
    ) -> StageExecution | None:
        if self._checkpoint_store is None:
            return None
        assert input_fingerprint is not None
        return self._checkpoint_store.load(input_fingerprint, stage_name)


def _stage_index(stage_name: str, *, option: str) -> int:
    try:
        return PIPELINE_STAGES.index(stage_name)
    except ValueError as exc:
        raise ValueError(f"Unknown {option} pipeline stage: {stage_name!r}") from exc


def _index_existing_results(context: PipelineContext) -> dict[str, StageResult]:
    indexed: dict[str, StageResult] = {}
    for result in context.stage_results:
        if result.stage not in PIPELINE_STAGES:
            raise ValueError(f"Context contains an unknown pipeline stage: {result.stage!r}")
        if result.stage in indexed:
            raise ValueError(f"Context contains duplicate pipeline stage results: {result.stage}")
        indexed[result.stage] = result
    return indexed


def _validate_execution(
    execution: StageExecution,
    stage_name: str,
    *,
    source: str,
) -> None:
    if not isinstance(execution, StageExecution):
        raise TypeError(
            f"{source} for {stage_name!r} returned {type(execution).__name__}, "
            "expected StageExecution"
        )
    if execution.result.stage != stage_name:
        raise ValueError(
            f"{source} for {stage_name!r} returned a result for "
            f"{execution.result.stage!r}"
        )


def _record_checkpoint_event(
    context: PipelineContext,
    stage_name: str,
    action: str,
) -> None:
    context.trace.setdefault("checkpoint_events", []).append(
        {"stage": stage_name, "action": action}
    )


def _reset_checkpoint_context(context: PipelineContext) -> None:
    baseline = PipelineContext.from_company(context.company)
    context.company_website_url = baseline.company_website_url
    context.hiring_entity_name = baseline.hiring_entity_name
    context.hiring_identity_evidence = None
    context.career_root_url = baseline.career_root_url
    context.homepage_navigation_evidence = None
    context.career_page_url = None
    context.job_list_page_url = None
    context.discovered_job_board = None
    context.provider_identity = None
    context.job_board_portfolio = None
    context.open_position_url = None
    context.opening_identity = None
    context.opening_selection_evidence = None
    context.provider = None
    context.stage_results = []
    context.stage_evidence_lineage = {}
    context.trace["stages"] = {}
    context.trace["checkpoint_events"] = []


def _not_run(stage_name: str, detail: str, *, scheduler_reason: str) -> StageExecution:
    return StageExecution(
        result=StageResult(stage=stage_name, status="not_run", detail=detail),
        trace={
            "scheduler": {
                "status": "not_run",
                "reason": scheduler_reason,
            }
        },
    )
