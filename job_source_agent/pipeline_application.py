from __future__ import annotations

import re
from dataclasses import asdict

from .application_runner import ApplicationRunner
from .checkpoint import execution_fingerprint
from .contracts import PipelineContext
from .evidence_scope import new_capture_attempt_id
from .models import (
    PIPELINE_STAGES,
    STAGE_CAREER_DISCOVERY,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
    STAGE_RESULT_VALIDATION,
    CompanyInput,
    DiscoveryResult,
)
from .pipeline_status import derive_pipeline_status
from .identity_continuity import IDENTITY_CONTRACT_VERSION
from .run_configuration import AgentConfig, DeterministicRunConfig


class PipelineApplication:
    """Product-level use case for running the versioned seven-stage pipeline."""

    def __init__(
        self,
        runner: ApplicationRunner,
        *,
        run_configuration: DeterministicRunConfig | None = None,
    ) -> None:
        self.runner = runner
        self.run_configuration = run_configuration or DeterministicRunConfig.from_agent_config(
            AgentConfig()
        )

    def discover(
        self,
        company: CompanyInput,
        *,
        start_at: str | None = None,
        stop_after: str | None = None,
        rerun_from: str | None = None,
        capture_attempt_id: str | None = None,
        execution_fingerprint_override: str | None = None,
    ) -> DiscoveryResult:
        context = PipelineContext.from_company(company)
        run_options: dict = {
            "start_at": rerun_from or start_at,
            "stop_after": stop_after,
        }
        fingerprint = execution_fingerprint(asdict(company), self.run_configuration.digest)
        if execution_fingerprint_override is not None:
            if re.fullmatch(r"[0-9a-f]{64}", execution_fingerprint_override) is None:
                raise ValueError(
                    "execution_fingerprint_override must be a lowercase SHA-256 digest"
                )
            fingerprint = execution_fingerprint_override
        attempt_id = capture_attempt_id or new_capture_attempt_id()
        run_options["execution_fingerprint"] = fingerprint
        run_options["producer_attempt_id"] = attempt_id
        if self.runner.checkpointing_enabled:
            run_options["input_fingerprint"] = fingerprint
            run_options["rerun_from"] = rerun_from
        elif rerun_from is not None:
            raise ValueError("rerun_from requires a checkpoint-enabled application runner")
        self.runner.run(context, **run_options)
        return discovery_result_from_context(
            context,
            run_configuration=self.run_configuration,
            execution_fingerprint_value=fingerprint,
        )


def discovery_result_from_context(
    context: PipelineContext,
    *,
    run_configuration: DeterministicRunConfig | None = None,
    execution_fingerprint_value: str | None = None,
) -> DiscoveryResult:
    company = context.company
    settings = run_configuration or DeterministicRunConfig.from_agent_config(AgentConfig())
    validation_result = next(
        (
            item
            for item in context.stage_results
            if item.stage == STAGE_RESULT_VALIDATION
        ),
        None,
    )
    validation_trace = context.trace.get("stages", {}).get(
        STAGE_RESULT_VALIDATION, {}
    )
    identity_issues = (
        list(validation_trace.get("issues", []))
        if isinstance(validation_trace, dict)
        and isinstance(validation_trace.get("issues", []), list)
        else []
    )
    validation_failed = bool(
        validation_result is not None and validation_result.status != "success"
    )
    identity_rejected = bool(context.open_position_url and validation_failed)
    job_list_identity_rejected = bool(
        context.job_list_page_url
        and validation_failed
        and any(
            issue.startswith(("HIRING_", "PROVIDER_"))
            for issue in identity_issues
        )
    )
    public_opening_url = None if identity_rejected else context.open_position_url
    public_job_list_url = (
        None if job_list_identity_rejected else context.job_list_page_url
    )
    identity_assertion = _identity_assertion(context, identity_issues)
    result = DiscoveryResult(
        company_name=company.company_name,
        company_website_url=context.company_website_url,
        hiring_entity_name=context.hiring_entity_name,
        career_root_url=context.career_root_url,
        linkedin_job_url=company.linkedin_job_url,
        external_apply_url=company.external_apply_url,
        linkedin_company_url=company.linkedin_company_url,
        linkedin_job_title=company.job_title,
        linkedin_job_location=company.job_location,
        career_page_url=context.career_page_url,
        job_list_page_url=public_job_list_url,
        open_position_url=public_opening_url,
        identity_assertion=identity_assertion,
        stage_results=list(context.stage_results),
        run_configuration=settings.to_payload(),
        run_configuration_digest=settings.digest,
        execution_fingerprint=execution_fingerprint_value
        or execution_fingerprint(asdict(company), settings.digest),
        trace={
            "source": company.source,
            "linkedin_job_url": company.linkedin_job_url,
            "external_apply_url": company.external_apply_url,
            "linkedin_company_url": company.linkedin_company_url,
            "linkedin_job_title": company.job_title,
            "source_trace": company.source_trace,
            "stages": context.trace.get("stages", {}),
            "checkpoint_events": context.trace.get("checkpoint_events", []),
            **(
                {"checkpoint_prefix": context.trace["checkpoint_prefix"]}
                if "checkpoint_prefix" in context.trace
                else {}
            ),
            "run_configuration_digest": settings.digest,
            "execution_fingerprint": execution_fingerprint_value
            or execution_fingerprint(asdict(company), settings.digest),
            "stage_evidence_lineage": [
                asdict(context.stage_evidence_lineage[stage])
                for stage in PIPELINE_STAGES
                if stage in context.stage_evidence_lineage
            ],
            "steps": [],
        },
    )

    first_terminal_stage = None
    source_terminal_stage = None
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
        if (
            stage_result.status in {"failed", "unsupported"}
            and stage_result.reason_code
            and first_terminal_stage is None
        ):
            first_terminal_stage = stage_result

        if stage_result.reason_code == "LINKEDIN_NATIVE_ONLY":
            source_terminal_stage = stage_result

    terminal_stage = source_terminal_stage or first_terminal_stage
    if not context.job_list_page_url and terminal_stage is not None:
        result.error_code = terminal_stage.reason_code
        result.error = _legacy_error(terminal_stage.stage, terminal_stage.reason_code)
        result.trace["failure_detail"] = terminal_stage.detail

    result.pipeline_status = _pipeline_status(context)
    if result.pipeline_status == "failed" and result.job_list_page_url:
        result.status = "partial"
    elif result.job_list_page_url:
        result.status = "success"
    elif result.pipeline_status == "partial":
        result.status = "partial"
    elif _has_public_upstream_result(result):
        result.status = "partial"
    else:
        result.status = "failed"
    return result


def _pipeline_status(context: PipelineContext) -> str:
    return derive_pipeline_status(context.stage_results)


def _has_public_upstream_result(result: DiscoveryResult) -> bool:
    return any(
        (
            result.company_website_url,
            result.career_root_url,
            result.career_page_url,
            result.job_list_page_url,
        )
    )


def _identity_assertion(
    context: PipelineContext,
    failure_codes: list[str],
) -> dict:
    hiring = context.hiring_identity_evidence
    provider = context.provider_identity
    opening = context.opening_identity
    selection = context.opening_selection_evidence
    has_candidate = bool(context.open_position_url)
    if not has_candidate:
        verdict = "not_applicable"
    elif failure_codes:
        verdict = "rejected"
    elif hiring is not None and provider is not None and opening is not None:
        verdict = "verified"
    else:
        verdict = "unavailable"
    return {
        "schema_version": IDENTITY_CONTRACT_VERSION,
        "verdict": verdict,
        "failure_codes": failure_codes,
        "hiring": asdict(hiring) if hiring is not None else None,
        "provider": asdict(provider) if provider is not None else None,
        "opening": asdict(opening) if opening is not None else None,
        "selection": asdict(selection) if selection is not None else None,
        "location_classification": context.trace.get("stages", {})
        .get(STAGE_RESULT_VALIDATION, {})
        .get("location_classification"),
        "candidate_opening_url": context.open_position_url,
    }


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
