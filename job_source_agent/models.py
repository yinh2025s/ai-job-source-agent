from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


RESULT_SCHEMA_VERSION = "2.1"

STAGE_LINKEDIN_DISCOVERY = "linkedin_discovery"
STAGE_WEBSITE_RESOLUTION = "website_resolution"
STAGE_HIRING_IDENTITY_RESOLUTION = "hiring_identity_resolution"
STAGE_CAREER_DISCOVERY = "career_discovery"
STAGE_JOB_BOARD_DISCOVERY = "job_board_discovery"
STAGE_OPENING_MATCH = "opening_match"
STAGE_RESULT_VALIDATION = "result_validation"

PIPELINE_STAGES = (
    STAGE_LINKEDIN_DISCOVERY,
    STAGE_WEBSITE_RESOLUTION,
    STAGE_HIRING_IDENTITY_RESOLUTION,
    STAGE_CAREER_DISCOVERY,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
    STAGE_RESULT_VALIDATION,
)


@dataclass
class StageResult:
    """A machine-readable outcome for one fixed pipeline stage."""

    stage: str
    status: str
    reason_code: str | None = None
    retryable: bool = False
    owner: str | None = None
    provider: str | None = None
    duration_ms: int = 0
    input_count: int = 0
    output_count: int = 0
    evidence: list[dict[str, Any]] = field(default_factory=list)
    detail: str | None = None


@dataclass
class CompanyInput:
    linkedin_job_url: str = ""
    external_apply_url: str | None = None
    company_name: str = ""
    company_website_url: str = ""
    hiring_entity_name: str | None = None
    career_root_url: str | None = None
    linkedin_html_path: str | None = None
    linkedin_company_url: str | None = None
    job_title: str | None = None
    job_location: str | None = None
    source: str = "input"
    source_trace: dict[str, Any] = field(default_factory=dict)


@dataclass
class LinkCandidate:
    url: str
    text: str
    source_url: str
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    origin: str = "unknown"


@dataclass
class DiscoveryResult:
    company_name: str
    company_website_url: str
    hiring_entity_name: str | None = None
    career_root_url: str | None = None
    linkedin_job_url: str = ""
    external_apply_url: str | None = None
    linkedin_company_url: str | None = None
    linkedin_job_title: str | None = None
    linkedin_job_location: str | None = None
    career_page_url: str | None = None
    job_list_page_url: str | None = None
    open_position_url: str | None = None
    # `status` and `error` remain for existing demo consumers. New callers
    # should use `pipeline_status`, `error_code`, and `stage_results`.
    status: str = "failed"
    error: str | None = None
    error_code: str | None = None
    pipeline_status: str = "failed"
    stage_results: list[StageResult] = field(default_factory=list)
    run_configuration: dict[str, Any] = field(default_factory=dict)
    run_configuration_digest: str | None = None
    execution_fingerprint: str | None = None
    result_schema_version: str = RESULT_SCHEMA_VERSION
    trace: dict[str, Any] = field(default_factory=dict)

    def stage_status(self, stage: str) -> str:
        for result in self.stage_results:
            if result.stage == stage:
                return result.status
        return "not_run"

    def result_record(self) -> dict[str, Any]:
        return {
            "result_schema_version": self.result_schema_version,
            "company_name": self.company_name,
            "company_website_url": self.company_website_url,
            "hiring_entity_name": self.hiring_entity_name,
            "career_root_url": self.career_root_url,
            "linkedin_job_url": self.linkedin_job_url,
            "external_apply_url": self.external_apply_url,
            "linkedin_company_url": self.linkedin_company_url,
            "linkedin_job_title": self.linkedin_job_title,
            "linkedin_job_location": self.linkedin_job_location,
            "career_page_url": self.career_page_url,
            "job_list_page_url": self.job_list_page_url,
            "open_position_url": self.open_position_url,
            "status": self.status,
            "error": self.error,
            "error_code": self.error_code,
            "pipeline_status": self.pipeline_status,
            "run_configuration": self.run_configuration,
            "run_configuration_digest": self.run_configuration_digest,
            "execution_fingerprint": self.execution_fingerprint,
            "career_page_status": self.stage_status(STAGE_CAREER_DISCOVERY),
            "job_board_status": self.stage_status(STAGE_JOB_BOARD_DISCOVERY),
            "opening_match_status": self.stage_status(STAGE_OPENING_MATCH),
            "output_validation_status": self.stage_status(STAGE_RESULT_VALIDATION),
            "stages": dataclass_to_dict(self.stage_results),
        }

    def trace_record(self) -> dict[str, Any]:
        record = self.result_record()
        record["trace"] = self.trace
        return record


def dataclass_to_dict(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, list):
        return [dataclass_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: dataclass_to_dict(item) for key, item in value.items()}
    return value
