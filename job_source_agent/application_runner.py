from __future__ import annotations

from collections.abc import Iterable

from .contracts import PipelineContext, Stage, StageExecution
from .models import PIPELINE_STAGES, StageResult


class ApplicationRunner:
    """Run injected pipeline stages with a canonical, resumable result shape.

    The runner owns orchestration only. Stage implementations retain ownership of
    domain failures and context updates, while this class guarantees that every
    run produces one result per standard pipeline stage in standard order.
    """

    def __init__(self, stages: Iterable[Stage]) -> None:
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

    def run(
        self,
        context: PipelineContext,
        *,
        start_at: str | None = None,
        stop_after: str | None = None,
    ) -> PipelineContext:
        """Execute an inclusive stage range and return the mutated context.

        Results before ``start_at`` are reused when present, which lets a caller
        resume from a hydrated context. Results in the selected range are always
        recomputed. Results after ``stop_after`` are deterministically marked
        ``not_run`` so stale downstream checkpoint results cannot leak into a run.
        """

        start_at = start_at or PIPELINE_STAGES[0]
        stop_after = stop_after or PIPELINE_STAGES[-1]
        start_index = _stage_index(start_at, option="start_at")
        stop_index = _stage_index(stop_after, option="stop_after")
        if start_index > stop_index:
            raise ValueError(
                f"start_at stage {start_at!r} comes after stop_after stage {stop_after!r}"
            )

        previous_results = _index_existing_results(context)
        previous_traces = dict(context.trace.get("stages", {}))
        context.stage_results = []
        context.trace["stages"] = {}

        for index, stage_name in enumerate(PIPELINE_STAGES):
            if index < start_index:
                previous = previous_results.get(stage_name)
                if previous is not None:
                    context.apply(
                        StageExecution(
                            result=previous,
                            trace=previous_traces.get(stage_name, {}),
                        )
                    )
                else:
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

            execution = stage.run(context)
            if not isinstance(execution, StageExecution):
                raise TypeError(
                    f"Stage {stage_name!r} returned {type(execution).__name__}, "
                    "expected StageExecution"
                )
            if execution.result.stage != stage_name:
                raise ValueError(
                    f"Stage {stage_name!r} returned a result for "
                    f"{execution.result.stage!r}"
                )
            context.apply(execution)

        return context


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
