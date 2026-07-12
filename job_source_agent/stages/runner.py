from __future__ import annotations

from collections.abc import Iterable

from ..contracts import PipelineContext, Stage


class PipelineStageRunner:
    """Execute ordered stages through their shared context contract."""

    def __init__(self, stages: Iterable[Stage]) -> None:
        self.stages = tuple(stages)

    def run(self, context: PipelineContext) -> PipelineContext:
        for stage in self.stages:
            execution = stage.run(context)
            context.apply(execution)
        return context
