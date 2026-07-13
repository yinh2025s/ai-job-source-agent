from __future__ import annotations

from collections import Counter
from math import ceil
from urllib.parse import urlparse

from .models import PIPELINE_STAGES
from .opening_matcher import detect_provider


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
        "company_stage_matrix": _company_stage_matrix(results),
    }
    if elapsed_sec is not None:
        summary["elapsed_sec"] = elapsed_sec
    return summary


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
        "stage_success_delta": {
            stage: int(current.get("stage_funnel", {}).get(stage, {}).get("success", 0))
            - int(baseline.get("stage_funnel", {}).get(stage, {}).get("success", 0))
            for stage in stage_keys
        },
    }


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
    for field in ("open_position_url", "job_list_page_url", "career_page_url", "career_root_url"):
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

    results_by_company = {str(result.get("company_name")): result for result in results}
    checks = []
    for company_name, expected in expectations.items():
        result = results_by_company.get(company_name)
        failures: list[str] = []
        if result is None:
            failures.append("missing_company_result")
            checks.append({"company_name": company_name, "passed": False, "failures": failures})
            continue

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

        checks.append(
            {
                "company_name": company_name,
                "expected_provider": expected_provider,
                "actual_provider": actual_provider,
                "expected_minimum_stage": expected_stage,
                "passed": not failures,
                "failures": failures,
            }
        )

    failed = [check for check in checks if not check["passed"]]
    return {
        "total": len(checks),
        "passed": len(checks) - len(failed),
        "failed": len(failed),
        "checks": checks,
    }
