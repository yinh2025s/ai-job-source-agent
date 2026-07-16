from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .job_board import DiscoveredJobBoard, JobBoardPortfolio
from .homepage_navigation import HomepageNavigationEvidence
from .evidence_scope import EvidenceScopeRef, StageEvidenceLineage
from .models import CompanyInput, StageResult
from .identity_continuity import (
    HiringIdentityEvidence,
    OpeningIdentity,
    OpeningSelectionEvidence,
    ProviderIdentity,
)
from .web import Page


CONTRACT_SCHEMA_VERSION = "1.6"


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


@runtime_checkable
class FetchBudget(Protocol):
    """Optional capability for clients with a cooperative fetch deadline."""

    def remaining_fetch_seconds(self) -> float | None:
        """Return non-negative remaining time, or None when the client is unbounded."""

        ...


@runtime_checkable
class EvidenceCaptureCoordinator(Protocol):
    """Optional stage-boundary capability for scoped terminal fetch capture."""

    def begin_stage(
        self,
        attempt_id: str,
        execution_fingerprint: str,
        stage: str,
    ) -> str:
        ...

    def finalize(self) -> EvidenceScopeRef:
        ...

    def abort_stage(self) -> None:
        ...


@dataclass
class PipelineContext:
    """Versioned data exchanged between pipeline stages."""

    company: CompanyInput
    company_website_url: str = ""
    hiring_entity_name: str | None = None
    hiring_identity_evidence: HiringIdentityEvidence | None = None
    career_root_url: str | None = None
    homepage_navigation_evidence: HomepageNavigationEvidence | None = None
    career_page_url: str | None = None
    job_list_page_url: str | None = None
    discovered_job_board: DiscoveredJobBoard | None = None
    provider_identity: ProviderIdentity | None = None
    job_board_portfolio: JobBoardPortfolio | None = None
    open_position_url: str | None = None
    opening_identity: OpeningIdentity | None = None
    opening_selection_evidence: OpeningSelectionEvidence | None = None
    provider: str | None = None
    stage_results: list[StageResult] = field(default_factory=list)
    stage_evidence_lineage: dict[str, StageEvidenceLineage] = field(default_factory=dict)
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
        if (
            execution.evidence_lineage is not None
            and execution.evidence_lineage.stage != execution.result.stage
        ):
            raise ValueError("Stage evidence lineage does not match stage result")
        for field_name, value in execution.updates.items():
            if field_name not in _CONTEXT_UPDATE_FIELDS:
                raise ValueError(f"Stage attempted to update unsupported context field: {field_name}")
            if field_name == "discovered_job_board" and not isinstance(
                value, DiscoveredJobBoard
            ):
                raise TypeError("discovered_job_board update must use DiscoveredJobBoard")
            if field_name == "job_board_portfolio" and not isinstance(
                value, JobBoardPortfolio
            ):
                raise TypeError("job_board_portfolio update must use JobBoardPortfolio")
            if field_name == "homepage_navigation_evidence" and not isinstance(
                value, HomepageNavigationEvidence
            ):
                raise TypeError(
                    "homepage_navigation_evidence update must use HomepageNavigationEvidence"
                )
            if field_name == "hiring_identity_evidence" and not isinstance(
                value, HiringIdentityEvidence
            ):
                raise TypeError(
                    "hiring_identity_evidence update must use HiringIdentityEvidence"
                )
            if field_name == "provider_identity" and not isinstance(
                value, ProviderIdentity
            ):
                raise TypeError("provider_identity update must use ProviderIdentity")
            if field_name == "opening_identity" and not isinstance(
                value, OpeningIdentity
            ):
                raise TypeError("opening_identity update must use OpeningIdentity")
            if field_name == "opening_selection_evidence" and not isinstance(
                value, OpeningSelectionEvidence
            ):
                raise TypeError(
                    "opening_selection_evidence update must use OpeningSelectionEvidence"
                )
            setattr(self, field_name, value)
        self.stage_results.append(execution.result)
        self.trace.setdefault("stages", {})[execution.result.stage] = execution.trace
        if execution.evidence_lineage is not None:
            self.stage_evidence_lineage[execution.result.stage] = execution.evidence_lineage


@dataclass
class StageExecution:
    """One stage result plus declared updates for downstream stages."""

    result: StageResult
    updates: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    evidence_lineage: StageEvidenceLineage | None = None
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
    "hiring_identity_evidence",
    "career_root_url",
    "homepage_navigation_evidence",
    "career_page_url",
    "job_list_page_url",
    "discovered_job_board",
    "provider_identity",
    "job_board_portfolio",
    "open_position_url",
    "opening_identity",
    "opening_selection_evidence",
    "provider",
}
