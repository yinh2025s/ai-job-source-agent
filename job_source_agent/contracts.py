from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .models import CompanyInput, StageResult
from .web import Page


CONTRACT_SCHEMA_VERSION = "1.0"


@runtime_checkable
class FetchClient(Protocol):
    """Small network boundary shared by live, browser, retry, and fixture clients."""

    def fetch(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Page:
        ...


@dataclass
class PipelineContext:
    """Versioned data exchanged between pipeline stages."""

    company: CompanyInput
    company_website_url: str = ""
    hiring_entity_name: str | None = None
    career_root_url: str | None = None
    career_page_url: str | None = None
    job_list_page_url: str | None = None
    open_position_url: str | None = None
    provider: str | None = None
    stage_results: list[StageResult] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    schema_version: str = CONTRACT_SCHEMA_VERSION

    @classmethod
    def from_company(cls, company: CompanyInput) -> PipelineContext:
        return cls(
            company=company,
            company_website_url=company.company_website_url,
            hiring_entity_name=company.hiring_entity_name,
            career_root_url=company.career_root_url,
            trace={"source": company.source, "stages": {}},
        )

    def apply(self, execution: StageExecution) -> None:
        for field_name, value in execution.updates.items():
            if field_name not in _CONTEXT_UPDATE_FIELDS:
                raise ValueError(f"Stage attempted to update unsupported context field: {field_name}")
            setattr(self, field_name, value)
        self.stage_results.append(execution.result)
        self.trace.setdefault("stages", {})[execution.result.stage] = execution.trace


@dataclass
class StageExecution:
    """One stage result plus declared updates for downstream stages."""

    result: StageResult
    updates: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    schema_version: str = CONTRACT_SCHEMA_VERSION


@runtime_checkable
class Stage(Protocol):
    name: str

    def run(self, context: PipelineContext) -> StageExecution:
        ...


@runtime_checkable
class CheckpointStore(Protocol):
    """Persistence boundary for reusable stage executions."""

    def load(self, input_fingerprint: str, stage: str) -> StageExecution | None:
        ...

    def save(self, input_fingerprint: str, execution: StageExecution) -> None:
        ...

    def invalidate_from(self, input_fingerprint: str, stage: str) -> None:
        ...


_CONTEXT_UPDATE_FIELDS = {
    "company_website_url",
    "hiring_entity_name",
    "career_root_url",
    "career_page_url",
    "job_list_page_url",
    "open_position_url",
    "provider",
}
