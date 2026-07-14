from __future__ import annotations

from collections import Counter
from math import ceil
from urllib.parse import urlparse

from .models import PIPELINE_STAGES
from .opening_matcher import detect_provider
from .result_identity import (
    canonicalize_identity_url,
    identity_urls_equivalent,
    public_result_identity,
    tenant_locator,
)


FAILURE_CLUSTER_COMPANY_LIMIT = 20
EVALUATION_SCHEMA_VERSION = "1.0"
EVALUATION_DISPOSITIONS = frozenset(
    {
        "exact_public",
        "verified_closed",
        "no_public_opening",
        "recruiter_client_undisclosed",
        "external_blocked",
        "system_gap",
    }
)
EVALUATION_ELIGIBILITY_VALUES = frozenset({True, False, "unknown"})
_IDENTITY_VERDICTS = frozenset(
    {"verified", "rejected", "unreviewed", "not_applicable"}
)
_FAILURE_STATUSES = {"failed", "partial", "unsupported"}
_VERIFIED_NO_MATCH_DISPOSITIONS = {
    "verified_inventory_no_match",
    "verified_inventory_empty",
}
_EXTERNAL_BLOCKED_REASONS = {
    "BOT_PROTECTION",
    "CAPTCHA_REQUIRED",
    "HTTP_FORBIDDEN",
    "LOGIN_REQUIRED",
}
_UNSUPPORTED_REASONS = {
    "PROVIDER_UNKNOWN",
    "PROVIDER_UNSUPPORTED",
    "PROVIDER_VARIANT_UNSUPPORTED",
}
_REPLAY_INFRASTRUCTURE_REASONS = {
    "OFFLINE_FIXTURE_MISSING",
    "OFFLINE_TAPE_DIVERGENCE",
}
_DISCOVERY_UNRESOLVED_REASONS = {
    "CAREER_PAGE_NOT_FOUND",
    "EMPTY_PROVIDER_RESPONSE",
    "JOB_BOARD_NOT_FOUND",
    "LOCATION_MISMATCH",
    "OPENING_DISCOVERY_INCOMPLETE",
    "OPENING_NOT_FOUND",
    "TITLE_MISMATCH",
    "WEBSITE_NOT_RESOLVED",
}
_TERMINAL_OUTCOME_ORDER = (
    "exact_opening",
    "verified_no_match",
    "no_public_openings",
    "identity_ambiguous",
    "retryable_failure",
    "linkedin_native_only",
    "external_blocked",
    "replay_infrastructure_failure",
    "unsupported_capability",
    "discovery_unresolved",
    "source_closed",
    "other_non_success",
)
_TERMINAL_OUTCOME_RANK = {
    outcome: index for index, outcome in enumerate(_TERMINAL_OUTCOME_ORDER)
}


def summarize_results(results: list[dict], elapsed_sec: float | None = None) -> dict:
    total = len(results)
    status_counts = Counter(str(result.get("status") or "unknown") for result in results)
    pipeline_status_counts = Counter(
        str(result.get("pipeline_status") or result.get("status") or "unknown") for result in results
    )
    error_counts = Counter(str(result.get("error") or "none") for result in results)
    provider_counts = Counter(_result_provider(result) for result in results)
    failure_stage_counts = Counter(_failure_stage(result) for result in results if result.get("error"))
    stage_funnel = _stage_funnel(results)
    provider_stage_status_counts = _provider_stage_status_counts(results)
    reason_code_counts = _reason_code_counts(results)
    provider_reason_code_counts = _provider_reason_code_counts(results)
    stage_duration_ms = _stage_duration_ms(results)
    checkpoint_action_counts, checkpoint_stage_counts = _checkpoint_activity_counts(results)
    source_posting_disposition_counts = _source_posting_disposition_counts(results)
    availability_diagnostic_counts = _availability_diagnostic_counts(results)
    terminal_outcomes = [_terminal_outcome(result) for result in results]
    terminal_outcome_counts = Counter(terminal_outcomes)
    failure_clusters = _failure_clusters(results)
    evaluation_metrics = summarize_evaluation_metrics(results)

    summary = {
        "total": total,
        "success": status_counts.get("success", 0),
        "partial": status_counts.get("partial", 0),
        "failed": status_counts.get("failed", 0),
        "pipeline_success": pipeline_status_counts.get("success", 0),
        "pipeline_partial": pipeline_status_counts.get("partial", 0),
        "pipeline_failed": pipeline_status_counts.get("failed", 0),
        "pipeline_unsupported": pipeline_status_counts.get("unsupported", 0),
        "with_website": sum(1 for result in results if result.get("company_website_url")),
        "with_career_page": sum(1 for result in results if result.get("career_page_url")),
        "with_job_list": sum(1 for result in results if result.get("job_list_page_url")),
        "with_opening": sum(1 for result in results if result.get("open_position_url")),
        "rates": _rates(results),
        "status_counts": dict(status_counts),
        "pipeline_status_counts": dict(pipeline_status_counts),
        "error_counts": dict(error_counts),
        "provider_counts": dict(provider_counts),
        "failure_stage_counts": dict(failure_stage_counts),
        "stage_funnel": stage_funnel,
        "provider_stage_status_counts": provider_stage_status_counts,
        "reason_code_counts": reason_code_counts,
        "provider_reason_code_counts": provider_reason_code_counts,
        "stage_duration_ms": stage_duration_ms,
        "checkpoint_action_counts": checkpoint_action_counts,
        "checkpoint_stage_counts": checkpoint_stage_counts,
        "source_posting_disposition_counts": source_posting_disposition_counts,
        "availability_diagnostic_counts": availability_diagnostic_counts,
        "terminal_outcome_counts": dict(terminal_outcome_counts),
        "failure_clusters": failure_clusters,
        "company_stage_matrix": _company_stage_matrix(results),
        "company_identity_matrix": _company_identity_matrix(results),
        "evaluation_schema_version": EVALUATION_SCHEMA_VERSION,
        "evaluation_metrics": evaluation_metrics,
        "record_disposition_counts": evaluation_metrics["record_disposition_counts"],
        "evaluation_annotation_coverage": evaluation_metrics["annotation_coverage"],
    }
    if elapsed_sec is not None:
        summary["elapsed_sec"] = elapsed_sec
    return summary


def validate_evaluation_record(record: dict) -> dict:
    """Validate one externally annotated result without inferring eligibility."""

    if not isinstance(record, dict):
        raise ValueError("evaluation record must be a mapping")
    annotation = record.get("evaluation")
    if not isinstance(annotation, dict):
        raise ValueError("evaluation annotation must be a mapping")
    if annotation.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        raise ValueError(
            f"evaluation schema_version must be {EVALUATION_SCHEMA_VERSION!r}"
        )
    disposition = annotation.get("record_disposition")
    if disposition not in EVALUATION_DISPOSITIONS:
        raise ValueError("evaluation record_disposition is invalid")
    eligibility = annotation.get("eligible_exact_opening")
    if (
        eligibility is not True
        and eligibility is not False
        and eligibility != "unknown"
    ):
        raise ValueError(
            "evaluation eligible_exact_opening must be true, false, or 'unknown'"
        )
    identity_verdict = annotation.get("identity_verdict")
    if identity_verdict not in _IDENTITY_VERDICTS:
        raise ValueError("evaluation identity_verdict is invalid")

    exact_output = bool(record.get("open_position_url"))
    if disposition == "exact_public":
        if not exact_output:
            raise ValueError("exact_public requires an open_position_url")
        if eligibility is not True:
            raise ValueError("exact_public requires eligible_exact_opening=true")
        if identity_verdict != "verified":
            raise ValueError("exact_public requires a verified identity verdict")
        if _s7_failed(record):
            raise ValueError("an S7 failure with an URL cannot be exact_public")
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "record_disposition": disposition,
        "eligible_exact_opening": eligibility,
        "identity_verdict": identity_verdict,
        "exact_output": exact_output,
    }


def summarize_evaluation_metrics(records: list[dict]) -> dict:
    """Aggregate trustworthy metrics while making missing review explicit."""

    total = len(records)
    annotated = [record for record in records if isinstance(record.get("evaluation"), dict)]
    validated = [validate_evaluation_record(record) for record in annotated]
    exact_outputs = [record for record in validated if record["exact_output"]]
    reviewed_exact = [
        record
        for record in exact_outputs
        if record["identity_verdict"] in {"verified", "rejected"}
    ]
    correct_exact = sum(
        record["record_disposition"] == "exact_public" for record in reviewed_exact
    )
    eligible = [record for record in validated if record["eligible_exact_opening"] is True]
    disposition_counts = Counter(
        record["record_disposition"] for record in validated
    )
    annotation_missing = total - len(validated)
    exact_output_count = sum(bool(record.get("open_position_url")) for record in records)

    precision_status = (
        "available"
        if annotation_missing == 0 and len(reviewed_exact) == len(exact_outputs)
        else "not_reportable"
    )
    recall_status = "available" if annotation_missing == 0 and eligible else "not_reportable"
    disposition_status = "available" if annotation_missing == 0 else "not_reportable"
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "annotation_coverage": _metric(len(validated), total),
        "record_disposition_counts": {
            disposition: disposition_counts.get(disposition, 0)
            for disposition in sorted(EVALUATION_DISPOSITIONS)
        },
        "record_disposition_status": disposition_status,
        "raw_exact_rate": _metric(exact_output_count, total),
        "exact_precision": _metric(
            correct_exact,
            len(reviewed_exact),
            unknown_count=(
                annotation_missing + len(exact_outputs) - len(reviewed_exact)
            ),
            status=precision_status,
        ),
        "conditional_exact_recall": _metric(
            sum(record["record_disposition"] == "exact_public" for record in eligible),
            len(eligible),
            unknown_count=(
                annotation_missing
                + sum(
                    record["eligible_exact_opening"] == "unknown"
                    for record in validated
                )
            ),
            status=recall_status,
        ),
        "system_defect_rate": _metric(
            disposition_counts.get("system_gap", 0),
            total,
            unknown_count=annotation_missing,
            status=disposition_status,
        ),
    }


def _metric(
    numerator: int,
    denominator: int,
    unknown_count: int = 0,
    status: str | None = None,
) -> dict:
    metric_status = status or ("available" if denominator else "not_reportable")
    return {
        "value": (
            round(numerator / denominator, 3)
            if denominator and metric_status == "available"
            else None
        ),
        "numerator": numerator,
        "denominator": denominator,
        "unknown_count": unknown_count,
        "status": metric_status,
    }


def _s7_failed(record: dict) -> bool:
    stage = _stage_by_name(record).get("result_validation")
    return isinstance(stage, dict) and stage.get("status") in _FAILURE_STATUSES


def compare_summaries(current: dict, baseline: dict) -> dict:
    """Return signed deltas for the fields used to decide whether a change regressed."""

    current_rates = current.get("rates", {})
    baseline_rates = baseline.get("rates", {})
    rate_keys = sorted(set(current_rates) | set(baseline_rates))
    stage_keys = sorted(set(current.get("stage_funnel", {})) | set(baseline.get("stage_funnel", {})))
    return {
        "rates_delta": {
            key: round(float(current_rates.get(key, 0)) - float(baseline_rates.get(key, 0)), 3)
            for key in rate_keys
        },
        "pipeline_status_delta": _count_deltas(
            current.get("pipeline_status_counts", {}),
            baseline.get("pipeline_status_counts", {}),
        ),
        "terminal_outcome_delta": _count_deltas(
            current.get("terminal_outcome_counts", {}),
            baseline.get("terminal_outcome_counts", {}),
        ),
        "stage_success_delta": {
            stage: int(current.get("stage_funnel", {}).get(stage, {}).get("success", 0))
            - int(baseline.get("stage_funnel", {}).get(stage, {}).get("success", 0))
            for stage in stage_keys
        },
        "company_identity_drift": _company_identity_drift(current, baseline),
    }


def _company_identity_drift(current: dict, baseline: dict) -> dict:
    empty = {
        "added_companies": [],
        "removed_companies": [],
        "changed_companies": [],
        "changed_fields": {},
    }
    current_matrix = current.get("company_identity_matrix")
    baseline_matrix = baseline.get("company_identity_matrix")
    if not isinstance(current_matrix, list) or not isinstance(baseline_matrix, list):
        return {"comparison_status": "not_available", **empty}

    current_by_company = _identity_matrix_by_company(current_matrix)
    baseline_by_company = _identity_matrix_by_company(baseline_matrix)
    current_names = set(current_by_company)
    baseline_names = set(baseline_by_company)
    changed_fields: dict[str, list[str]] = {}
    changed_companies: set[str] = set()
    for company_name in current_names & baseline_names:
        current_fields = _public_identity_fields(current_by_company[company_name])
        baseline_fields = _public_identity_fields(baseline_by_company[company_name])
        for field in current_fields:
            if current_fields[field] == baseline_fields[field]:
                continue
            changed_companies.add(company_name)
            changed_fields.setdefault(field, []).append(company_name)

    return {
        "comparison_status": "available",
        "added_companies": _sorted_company_names(current_names - baseline_names),
        "removed_companies": _sorted_company_names(baseline_names - current_names),
        "changed_companies": _sorted_company_names(changed_companies),
        "changed_fields": {
            field: _sorted_company_names(names)
            for field, names in sorted(changed_fields.items())
        },
    }


def _identity_matrix_by_company(matrix: list) -> dict[str, dict]:
    return {
        str(row.get("company_name")): row
        for row in matrix
        if isinstance(row, dict) and row.get("company_name")
    }


def _public_identity_fields(row: dict) -> dict[str, object]:
    job_board = row.get("job_board") if isinstance(row.get("job_board"), dict) else {}
    opening = row.get("opening") if isinstance(row.get("opening"), dict) else {}
    return {
        "website_url": row.get("website_url"),
        "career_page_url": row.get("career_page_url"),
        "job_board.provider": job_board.get("provider"),
        "job_board.tenant": job_board.get("tenant"),
        "job_board.canonical_url": job_board.get("canonical_url"),
        "opening.canonical_url": opening.get("canonical_url"),
    }


def _sorted_company_names(names) -> list[str]:
    return sorted(names, key=lambda name: (name.casefold(), name))


def _rates(results: list[dict]) -> dict[str, float]:
    total = len(results) or 1
    return {
        "website": round(sum(1 for result in results if result.get("company_website_url")) / total, 3),
        "career_page": round(sum(1 for result in results if result.get("career_page_url")) / total, 3),
        "job_list": round(sum(1 for result in results if result.get("job_list_page_url")) / total, 3),
        "opening": round(sum(1 for result in results if result.get("open_position_url")) / total, 3),
    }


def result_provider(result: dict) -> str:
    stage_by_name = _stage_by_name(result)
    for stage_name in ("opening_match", "job_board_discovery", "career_discovery"):
        provider = stage_by_name.get(stage_name, {}).get("provider")
        if isinstance(provider, str) and provider:
            return provider
    for field in (
        "open_position_url",
        "job_list_page_url",
        "career_page_url",
        "career_root_url",
    ):
        url = result.get(field)
        if isinstance(url, str) and url:
            provider = detect_provider(url)
            if provider != "generic":
                return provider
            host = urlparse(url).netloc.lower().removeprefix("www.")
            return host or "unknown"
    return "unknown"


_result_provider = result_provider


def _failure_stage(result: dict) -> str:
    if not result.get("company_website_url"):
        return "website"
    if not result.get("career_page_url"):
        return "career_page"
    if not result.get("job_list_page_url"):
        return "job_list"
    if not result.get("open_position_url"):
        return "opening"
    return "unknown"


def _stage_funnel(results: list[dict]) -> dict[str, dict[str, int]]:
    funnel = {stage: Counter() for stage in PIPELINE_STAGES}
    for result in results:
        stage_by_name = _stage_by_name(result)
        for stage in PIPELINE_STAGES:
            stage_result = stage_by_name.get(stage)
            funnel[stage][str(stage_result.get("status") if stage_result else "not_recorded")] += 1
    return {stage: dict(counts) for stage, counts in funnel.items()}


def _provider_stage_status_counts(results: list[dict]) -> dict[str, dict[str, dict[str, int]]]:
    counts: dict[str, dict[str, Counter]] = {}
    for result in results:
        provider = _result_provider(result)
        provider_counts = counts.setdefault(provider, {stage: Counter() for stage in PIPELINE_STAGES})
        stage_by_name = _stage_by_name(result)
        for stage in PIPELINE_STAGES:
            stage_result = stage_by_name.get(stage)
            provider_counts[stage][str(stage_result.get("status") if stage_result else "not_recorded")] += 1
    return {
        provider: {stage: dict(status_counts) for stage, status_counts in stages.items()}
        for provider, stages in counts.items()
    }


def _reason_code_counts(results: list[dict]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for result in results:
        for stage in _stage_by_name(result).values():
            reason_code = stage.get("reason_code")
            if reason_code:
                counts[str(reason_code)] += 1
    return dict(counts)


def _provider_reason_code_counts(results: list[dict]) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = {}
    for result in results:
        provider_counts = counts.setdefault(_result_provider(result), Counter())
        for stage in _stage_by_name(result).values():
            reason_code = stage.get("reason_code")
            if reason_code:
                provider_counts[str(reason_code)] += 1
    return {provider: dict(reason_counts) for provider, reason_counts in counts.items()}


def _failure_clusters(results: list[dict]) -> list[dict]:
    clusters: dict[tuple[str, str, str], dict[str, dict]] = {}
    for result in results:
        company_name = str(result.get("company_name") or "Unknown Company")
        for stage_name, stage in _stage_by_name(result).items():
            reason_code = stage.get("reason_code")
            if stage.get("status") not in _FAILURE_STATUSES or not reason_code:
                continue

            key = (stage_name, _failure_cluster_provider(result, stage), str(reason_code))
            companies = clusters.setdefault(key, {})
            company = companies.setdefault(
                company_name,
                {
                    "retryable": False,
                    "inventory_dispositions": set(),
                    "terminal_outcomes": set(),
                },
            )
            company["retryable"] = company["retryable"] or stage.get("retryable") is True
            company["terminal_outcomes"].add(_terminal_outcome(result))
            if stage_name == "opening_match":
                disposition = _opening_inventory_disposition(result, stage)
                if disposition:
                    company["inventory_dispositions"].add(disposition)

    stage_order = {stage: index for index, stage in enumerate(PIPELINE_STAGES)}
    ordered_clusters = []
    for (stage, provider, reason_code), companies in clusters.items():
        disposition_counts: Counter[str] = Counter()
        terminal_outcome_counts: Counter[str] = Counter()
        for company in companies.values():
            for disposition in company["inventory_dispositions"]:
                disposition_counts[disposition] += 1
            terminal_outcome_counts[
                min(
                    company["terminal_outcomes"],
                    key=lambda outcome: _TERMINAL_OUTCOME_RANK.get(
                        outcome, len(_TERMINAL_OUTCOME_RANK)
                    ),
                )
            ] += 1
        ordered_names = sorted(companies, key=lambda name: (name.casefold(), name))
        ordered_clusters.append(
            {
                "stage": stage,
                "provider": provider,
                "reason_code": reason_code,
                "company_count": len(companies),
                "retryable_count": sum(
                    1 for company in companies.values() if company["retryable"]
                ),
                "company_names": ordered_names[:FAILURE_CLUSTER_COMPANY_LIMIT],
                "inventory_disposition_counts": dict(sorted(disposition_counts.items())),
                "terminal_outcome_counts": dict(sorted(terminal_outcome_counts.items())),
            }
        )
    return sorted(
        ordered_clusters,
        key=lambda cluster: (
            -cluster["company_count"],
            stage_order.get(cluster["stage"], len(stage_order)),
            cluster["stage"],
            cluster["provider"],
            cluster["reason_code"],
        ),
    )


def _failure_cluster_provider(result: dict, stage: dict) -> str:
    provider = stage.get("provider")
    if isinstance(provider, str) and provider.strip():
        return provider.strip()

    saw_generic_url = False
    for field in ("open_position_url", "job_list_page_url", "career_page_url", "career_root_url"):
        url = result.get(field)
        if not isinstance(url, str) or not url:
            continue
        detected = detect_provider(url)
        if detected != "generic":
            return detected
        saw_generic_url = True
    return "generic" if saw_generic_url else "unknown"


def _opening_inventory_disposition(result: dict, opening_stage: dict) -> str | None:
    evidence = opening_stage.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict) or item.get("type") != "availability_diagnostic":
                continue
            disposition = item.get("disposition")
            if isinstance(disposition, str) and disposition:
                return disposition

    return None


def _terminal_outcome(result: dict) -> str:
    if result.get("open_position_url"):
        return "exact_opening"

    stage_by_name = _stage_by_name(result)
    opening_stage = stage_by_name.get("opening_match", {})
    disposition = _opening_inventory_disposition(result, opening_stage)
    if disposition in _VERIFIED_NO_MATCH_DISPOSITIONS:
        return "verified_no_match"
    if disposition == "source_posting_closed":
        return "source_closed"

    terminal_stage = _terminal_non_success_stage(stage_by_name)
    if terminal_stage is None:
        return "other_non_success"
    reason_code = str(terminal_stage.get("reason_code") or "")
    if reason_code == "NO_PUBLIC_OPENINGS":
        return "no_public_openings"
    if reason_code == "COMPANY_IDENTITY_AMBIGUOUS":
        return "identity_ambiguous"
    if terminal_stage.get("retryable") is True:
        return "retryable_failure"
    if reason_code == "LINKEDIN_NATIVE_ONLY":
        return "linkedin_native_only"
    if reason_code in _EXTERNAL_BLOCKED_REASONS:
        return "external_blocked"
    if reason_code in _REPLAY_INFRASTRUCTURE_REASONS:
        return "replay_infrastructure_failure"
    if terminal_stage.get("status") == "unsupported" or reason_code in _UNSUPPORTED_REASONS:
        return "unsupported_capability"
    if reason_code in _DISCOVERY_UNRESOLVED_REASONS:
        return "discovery_unresolved"
    if reason_code == "OPENING_CLOSED":
        return "source_closed"
    return "other_non_success"


def _terminal_non_success_stage(stage_by_name: dict[str, dict]) -> dict | None:
    for stage_name in PIPELINE_STAGES:
        stage = stage_by_name.get(stage_name)
        if isinstance(stage, dict) and stage.get("reason_code") == "LINKEDIN_NATIVE_ONLY":
            return stage
    for stage_name in reversed(PIPELINE_STAGES):
        stage = stage_by_name.get(stage_name)
        if isinstance(stage, dict) and stage.get("status") in _FAILURE_STATUSES:
            return stage
    return None


def _checkpoint_activity_counts(results: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    action_counts: Counter[str] = Counter()
    stage_counts: Counter[str] = Counter()
    for result in results:
        trace = result.get("trace")
        if not isinstance(trace, dict):
            continue
        events = trace.get("checkpoint_events")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            action = event.get("action")
            stage = event.get("stage")
            if isinstance(action, str) and action:
                action_counts[action] += 1
            if isinstance(stage, str) and stage:
                stage_counts[stage] += 1
    return dict(action_counts), dict(stage_counts)


def _availability_diagnostic_counts(results: list[dict]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for result in results:
        opening_stage = _stage_by_name(result).get("opening_match", {})
        evidence = opening_stage.get("evidence")
        if not isinstance(evidence, list):
            continue
        for item in evidence:
            if not isinstance(item, dict) or item.get("type") != "availability_diagnostic":
                continue
            disposition = item.get("disposition")
            if isinstance(disposition, str) and disposition:
                counts[disposition] += 1
                break
    return dict(counts)


def _source_posting_disposition_counts(results: list[dict]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for result in results:
        job_board_stage = _stage_by_name(result).get("job_board_discovery", {})
        evidence = job_board_stage.get("evidence")
        if not isinstance(evidence, list):
            continue
        for item in evidence:
            if not isinstance(item, dict) or item.get("type") != "source_posting_availability":
                continue
            disposition = item.get("disposition")
            if isinstance(disposition, str) and disposition:
                counts[disposition] += 1
                break
    return dict(counts)


def _company_stage_matrix(results: list[dict]) -> list[dict]:
    matrix = []
    for result in results:
        stage_by_name = _stage_by_name(result)
        reason_code = next(
            (
                str(stage.get("reason_code"))
                for stage in stage_by_name.values()
                if stage.get("status") in {"failed", "partial", "unsupported"} and stage.get("reason_code")
            ),
            None,
        )
        row = {
            "company_name": result.get("company_name") or "Unknown Company",
            "provider": _result_provider(result),
            "pipeline_status": result.get("pipeline_status") or result.get("status") or "unknown",
            "reason_code": reason_code,
        }
        row.update(
            {
                stage: stage_by_name.get(stage, {}).get("status", "not_recorded")
                for stage in PIPELINE_STAGES
            }
        )
        matrix.append(row)
    return matrix


def _company_identity_matrix(results: list[dict]) -> list[dict]:
    return [
        {
            "company_name": result.get("company_name") or "Unknown Company",
            **public_result_identity(result, _result_provider(result)),
        }
        for result in results
    ]


def _stage_by_name(result: dict) -> dict[str, dict]:
    stages = result.get("stages")
    if not isinstance(stages, list):
        return {}
    return {
        str(stage.get("stage")): stage
        for stage in stages
        if isinstance(stage, dict) and stage.get("stage")
    }


def _stage_duration_ms(results: list[dict]) -> dict[str, dict[str, int | None]]:
    durations = {stage: [] for stage in PIPELINE_STAGES}
    for result in results:
        for stage_name, stage_result in _stage_by_name(result).items():
            if stage_name not in durations or stage_result.get("status") in {"not_run", "not_applicable"}:
                continue
            duration = stage_result.get("duration_ms")
            if isinstance(duration, (int, float)):
                durations[stage_name].append(int(duration))
    return {
        stage: {
            "count": len(values),
            "p50": _percentile(values, 0.50),
            "p95": _percentile(values, 0.95),
        }
        for stage, values in durations.items()
    }


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _count_deltas(current: dict, baseline: dict) -> dict[str, int]:
    keys = sorted(set(current) | set(baseline))
    return {key: int(current.get(key, 0)) - int(baseline.get(key, 0)) for key in keys}


def evaluate_expectations(results: list[dict], expectations: dict[str, dict]) -> dict:
    """Validate a deterministic benchmark against its declared acceptance floor."""

    results_by_company: dict[str, dict] = {}
    duplicate_company_names: set[str] = set()
    for result in results:
        company_name = str(result.get("company_name"))
        if company_name in results_by_company:
            duplicate_company_names.add(company_name)
        else:
            results_by_company[company_name] = result
    checks = []
    for company_name, expected in expectations.items():
        result = results_by_company.get(company_name)
        failures: list[str] = []
        if result is None:
            failures.append("missing_company_result")
            checks.append(
                {
                    "company_name": company_name,
                    "expected_provider": expected.get("expected_provider"),
                    "actual_provider": None,
                    "expected_minimum_stage": str(
                        expected.get("expected_minimum_stage") or "job_board_discovery"
                    ),
                    "expected_identity": expected.get("expected_identity"),
                    "actual_identity": None,
                    "passed": False,
                    "failures": failures,
                }
            )
            continue

        if company_name in duplicate_company_names:
            failures.append("duplicate_company_result")

        actual_provider = _result_provider(result)
        expected_provider = expected.get("expected_provider")
        if expected_provider and actual_provider != expected_provider:
            failures.append(f"provider:{actual_provider}")

        expected_stage = str(expected.get("expected_minimum_stage") or "job_board_discovery")
        actual_stage = _stage_by_name(result).get(expected_stage, {})
        if actual_stage.get("status") != "success":
            failures.append(f"stage:{expected_stage}={actual_stage.get('status', 'not_recorded')}")

        if expected.get("require_exact_opening") and not result.get("open_position_url"):
            failures.append("opening:not_found")
        if not expected.get("allow_job_board_fallback", True) and not result.get("job_list_page_url"):
            failures.append("job_board:not_found")

        expected_identity = expected.get("expected_identity")
        actual_identity = None
        normalized_expected_identity = None
        if expected_identity is not None:
            actual_identity = public_result_identity(result, actual_provider)
            normalized_expected_identity, identity_failures = _evaluate_result_identity(
                result,
                actual_identity,
                expected_identity,
            )
            failures.extend(identity_failures)

        checks.append(
            {
                "company_name": company_name,
                "expected_provider": expected_provider,
                "actual_provider": actual_provider,
                "expected_minimum_stage": expected_stage,
                "expected_identity": normalized_expected_identity,
                "actual_identity": actual_identity,
                "passed": not failures,
                "failures": failures,
            }
        )

    for company_name in _sorted_company_names(duplicate_company_names - set(expectations)):
        checks.append(
            {
                "company_name": company_name,
                "expected_provider": None,
                "actual_provider": _result_provider(results_by_company[company_name]),
                "expected_minimum_stage": None,
                "expected_identity": None,
                "actual_identity": None,
                "passed": False,
                "failures": ["duplicate_company_result"],
            }
        )

    failed = [check for check in checks if not check["passed"]]
    return {
        "total": len(checks),
        "passed": len(checks) - len(failed),
        "failed": len(failed),
        "checks": checks,
        "duplicate_company_names": sorted(duplicate_company_names),
    }


def _evaluate_result_identity(
    result: dict,
    actual: dict,
    expected: object,
) -> tuple[dict | None, list[str]]:
    failures: list[str] = []
    if not isinstance(expected, dict):
        return None, ["identity:expected_identity_invalid"]

    normalized: dict = {}
    declared_fields = 0
    website_declared = any(
        field in expected for field in ("website_url", "website_url_aliases")
    )
    website_urls: list[str] = []
    if website_declared:
        declared_fields += 1
        website_urls = _expected_url_set(
            expected.get("website_url"),
            expected.get("website_url_aliases", []),
            "website_url",
            failures,
        )
        normalized["website_url"] = website_urls[0] if website_urls else None
        normalized["website_url_aliases"] = website_urls[1:]

    career_declared = any(
        field in expected for field in ("career_page_url", "career_page_url_aliases")
    )
    career_urls: list[str] = []
    if career_declared:
        declared_fields += 1
        career_urls = _expected_url_set(
            expected.get("career_page_url"),
            expected.get("career_page_url_aliases", []),
            "career_page_url",
            failures,
        )
        normalized["career_page_url"] = career_urls[0] if career_urls else None
        normalized["career_page_url_aliases"] = career_urls[1:]

    board_declared = "job_board" in expected
    expected_board = expected.get("job_board")
    board_urls: list[str] = []
    if board_declared:
        declared_fields += 1
        if not isinstance(expected_board, dict):
            failures.append("identity:expected_job_board_invalid")
            normalized["job_board"] = None
        else:
            board_urls = _expected_url_set(
                expected_board.get("canonical_url"),
                expected_board.get("aliases", []),
                "job_board",
                failures,
            )
            expected_provider = expected_board.get("provider")
            expected_tenant = expected_board.get("tenant")
            canonical_tenant = tenant_locator(board_urls[0]) if board_urls else None
            if not isinstance(expected_provider, str) or not expected_provider:
                failures.append("identity:expected_job_board_provider_invalid")
            if expected_tenant != canonical_tenant:
                failures.append("identity:expected_job_board_tenant_invalid")
            normalized["job_board"] = {
                "provider": expected_provider,
                "tenant": expected_tenant,
                "canonical_url": board_urls[0] if board_urls else None,
                "aliases": board_urls[1:],
            }

    opening_declared = "opening" in expected
    expected_opening = expected.get("opening")
    opening_urls: list[str] = []
    if opening_declared:
        declared_fields += 1
        if not isinstance(expected_opening, dict):
            failures.append("identity:expected_opening_invalid")
            normalized["opening"] = None
        else:
            opening_urls = _expected_url_set(
                expected_opening.get("canonical_url"),
                expected_opening.get("aliases", []),
                "opening",
                failures,
            )
            normalized["opening"] = {
                "canonical_url": opening_urls[0] if opening_urls else None,
                "aliases": opening_urls[1:],
            }

    if declared_fields == 0:
        failures.append("identity:expected_identity_empty")

    if website_declared:
        _check_actual_url(result.get("company_website_url"), "website_url", failures)
    if career_declared:
        _check_actual_url(result.get("career_page_url"), "career_page_url", failures)
    if board_declared:
        _check_actual_url(result.get("job_list_page_url"), "job_board", failures)
    if opening_declared:
        _check_actual_url(result.get("open_position_url"), "opening", failures)

    if website_declared and website_urls and not _matches_website(actual["website_url"], website_urls):
        failures.append("identity:website_url_mismatch")
    if career_declared and career_urls and not _matches_any(actual["career_page_url"], career_urls):
        failures.append("identity:career_page_url_mismatch")
    if board_declared and isinstance(expected_board, dict):
        if actual["job_board"]["provider"] != expected_board.get("provider"):
            failures.append("identity:job_board_provider_mismatch")
        accepted_tenants = {tenant_locator(url) for url in board_urls}
        if actual["job_board"]["tenant"] not in accepted_tenants:
            failures.append("identity:job_board_tenant_mismatch")
        if board_urls and not _matches_any(actual["job_board"]["canonical_url"], board_urls):
            failures.append("identity:job_board_url_mismatch")
    if opening_declared and opening_urls and not _matches_any(
        actual["opening"]["canonical_url"], opening_urls
    ):
        failures.append("identity:opening_url_mismatch")
    return normalized, failures


def _expected_url_set(
    canonical: object,
    aliases: object,
    field: str,
    failures: list[str],
) -> list[str]:
    if not isinstance(aliases, list) or len(aliases) > 100:
        failures.append(f"identity:expected_{field}_aliases_invalid")
        aliases = []
    values = [canonical, *aliases]
    normalized = []
    for value in values:
        try:
            normalized.append(canonicalize_identity_url(value))
        except ValueError:
            failures.append(f"identity:expected_{field}_url_invalid")
    return normalized


def _check_actual_url(value: object, field: str, failures: list[str]) -> None:
    try:
        canonicalize_identity_url(value)
    except ValueError:
        failures.append(f"identity:actual_{field}_url_invalid")


def _matches_any(actual: str | None, expected: list[str], *, allow_www: bool = False) -> bool:
    return actual is not None and any(
        identity_urls_equivalent(actual, candidate, allow_www=allow_www)
        for candidate in expected
    )


def _matches_website(actual: str | None, expected: list[str]) -> bool:
    if actual is None:
        return False
    return actual == expected[0] or any(
        identity_urls_equivalent(actual, alias, allow_www=True)
        for alias in expected[1:]
    )
