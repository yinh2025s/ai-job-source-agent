from __future__ import annotations

from dataclasses import asdict

from .application_runner import ApplicationRunner
from .checkpoint import input_fingerprint
from .contracts import PipelineContext
from .models import (
    STAGE_CAREER_DISCOVERY,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
    STAGE_RESULT_VALIDATION,
    CompanyInput,
    DiscoveryResult,
)


class PipelineApplication:
    """Product-level use case for running the versioned seven-stage pipeline."""

    def __init__(self, runner: ApplicationRunner) -> None:
        self.runner = runner

    def discover(
        self,
        company: CompanyInput,
        *,
        start_at: str | None = None,
        stop_after: str | None = None,
        rerun_from: str | None = None,
    ) -> DiscoveryResult:
        context = PipelineContext.from_company(company)
        run_options: dict = {
            "start_at": rerun_from or start_at,
            "stop_after": stop_after,
        }
        if self.runner.checkpointing_enabled:
            run_options["input_fingerprint"] = input_fingerprint(asdict(company))
            run_options["rerun_from"] = rerun_from
        elif rerun_from is not None:
            raise ValueError("rerun_from requires a checkpoint-enabled application runner")
        self.runner.run(context, **run_options)
        return discovery_result_from_context(context)


def discovery_result_from_context(context: PipelineContext) -> DiscoveryResult:
    company = context.company
    result = DiscoveryResult(
        company_name=company.company_name,
        company_website_url=context.company_website_url,
        hiring_entity_name=context.hiring_entity_name,
        career_root_url=context.career_root_url,
        linkedin_job_url=company.linkedin_job_url,
        linkedin_company_url=company.linkedin_company_url,
        linkedin_job_title=company.job_title,
        linkedin_job_location=company.job_location,
        career_page_url=context.career_page_url,
        job_list_page_url=context.job_list_page_url,
        open_position_url=context.open_position_url,
        stage_results=list(context.stage_results),
        trace={
            "source": company.source,
            "linkedin_job_url": company.linkedin_job_url,
            "linkedin_company_url": company.linkedin_company_url,
            "linkedin_job_title": company.job_title,
            "source_trace": company.source_trace,
            "stages": context.trace.get("stages", {}),
            "steps": [],
        },
    )

    for stage_result in result.stage_results:
        stage_trace = context.trace.get("stages", {}).get(stage_result.stage, {})
        if stage_result.stage in {
            STAGE_CAREER_DISCOVERY,
            STAGE_JOB_BOARD_DISCOVERY,
            STAGE_OPENING_MATCH,
        }:
            result.trace["steps"].append(
                {"name": _legacy_step_name(stage_result.stage), **stage_trace}
            )
        if stage_result.status == "failed" and result.error_code is None:
            result.error_code = stage_result.reason_code
            result.error = _legacy_error(stage_result.stage, stage_result.reason_code)
            result.trace["failure_detail"] = stage_result.detail

    result.pipeline_status = _pipeline_status(context)
    if result.job_list_page_url:
        result.status = "success"
    elif result.career_page_url:
        result.status = "partial"
    else:
        result.status = "failed"
    return result


def _pipeline_status(context: PipelineContext) -> str:
    validation_trace = context.trace.get("stages", {}).get(STAGE_RESULT_VALIDATION, {})
    if validation_trace.get("pipeline_status"):
        return str(validation_trace["pipeline_status"])
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


def _legacy_step_name(stage: str) -> str:
    return {
        STAGE_CAREER_DISCOVERY: "find_career_page",
        STAGE_JOB_BOARD_DISCOVERY: "find_job_board",
        STAGE_OPENING_MATCH: "match_opening",
    }[stage]


def _legacy_error(stage: str, reason_code: str | None) -> str:
    if stage == STAGE_CAREER_DISCOVERY and reason_code == "CAREER_PAGE_NOT_FOUND":
        return "career_page_not_found"
    if stage == STAGE_JOB_BOARD_DISCOVERY and reason_code == "JOB_BOARD_NOT_FOUND":
        return "job_board_not_found"
    if reason_code in {
        "NETWORK_TIMEOUT",
        "DNS_FAILED",
        "CONNECTION_FAILED",
        "HTTP_FORBIDDEN",
        "RATE_LIMITED",
        "SERVER_ERROR",
    }:
        return "fetch_failed"
    return (reason_code or "discovery_failed").lower()
