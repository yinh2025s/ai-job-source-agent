from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.models import PIPELINE_STAGES


STAGE_LABELS = {
    "linkedin_discovery": "S1",
    "website_resolution": "S2",
    "hiring_identity_resolution": "S3",
    "career_discovery": "S4",
    "job_board_discovery": "S5",
    "opening_match": "S6",
    "result_validation": "S7",
}

STATUS_LABELS = {
    "success": "OK",
    "partial": "PART",
    "failed": "FAIL",
    "not_run": "NR",
    "not_applicable": "NA",
    "unsupported": "UNSUP",
    "not_recorded": "-",
}

STATUS_ORDER = tuple(STATUS_LABELS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a benchmark/live summary JSON as a Markdown report.")
    parser.add_argument("--summary", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--title", default="AI Job Source Agent Report")
    parser.add_argument("--max-matrix-rows", type=int, default=50)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    report = render_markdown_report(summary, title=args.title, max_matrix_rows=args.max_matrix_rows)
    Path(args.output).write_text(report, encoding="utf-8")
    print(f"report: {args.output}", flush=True)


def render_markdown_report(summary: dict, title: str = "AI Job Source Agent Report", max_matrix_rows: int = 50) -> str:
    lines = [f"# {title}", ""]
    lines.extend(_overview(summary))
    lines.extend(_rates(summary))
    lines.extend(
        _simple_count_table(
            "Terminal Outcomes",
            summary.get("terminal_outcome_counts", {}),
            "Outcome",
        )
    )
    lines.extend(_regression(summary, max_rows=max_matrix_rows))
    lines.extend(_stage_funnel(summary))
    lines.extend(_stage_durations(summary))
    lines.extend(_simple_count_table("Provider Distribution", summary.get("provider_counts", {}), "Provider"))
    lines.extend(_provider_stage_reliability(summary))
    lines.extend(_provider_reason_codes(summary))
    lines.extend(_failure_clusters(summary))
    lines.extend(_simple_count_table("Reason Codes", summary.get("reason_code_counts", {}), "Reason"))
    lines.extend(
        _simple_count_table(
            "Opening Availability Diagnostics",
            summary.get("availability_diagnostic_counts", {}),
            "Disposition",
        )
    )
    lines.extend(_checkpoint_activity(summary))
    lines.extend(_expectations(summary, max_rows=max_matrix_rows))
    lines.extend(_company_matrix(summary, max_rows=max_matrix_rows))
    return "\n".join(lines).rstrip() + "\n"


def _overview(summary: dict) -> list[str]:
    return [
        "## Overview",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total companies | {summary.get('total', 0)} |",
        f"| Pipeline success | {summary.get('pipeline_success', 0)} |",
        f"| Pipeline partial | {summary.get('pipeline_partial', 0)} |",
        f"| Pipeline failed | {summary.get('pipeline_failed', 0)} |",
        f"| With job list | {summary.get('with_job_list', 0)} |",
        f"| With exact opening | {summary.get('with_opening', 0)} |",
        f"| Elapsed seconds | {summary.get('elapsed_sec', '-')} |",
        "",
    ]


def _rates(summary: dict) -> list[str]:
    lines = ["## Rates", "", "| Stage | Rate |", "| --- | ---: |"]
    for key, value in (summary.get("rates") or {}).items():
        lines.append(f"| {key} | {_percent(value)} |")
    lines.append("")
    return lines


def _regression(summary: dict, max_rows: int = 50) -> list[str]:
    regression = summary.get("regression") or {}
    if not regression:
        return []
    lines = ["## Regression", ""]
    rates_delta = regression.get("rates_delta") or {}
    if rates_delta:
        lines.extend(["| Rate | Delta |", "| --- | ---: |"])
        for key, value in sorted(rates_delta.items()):
            lines.append(f"| {key} | {_signed_number(value)} |")
        lines.append("")
    pipeline_delta = regression.get("pipeline_status_delta") or {}
    if pipeline_delta:
        lines.extend(["| Pipeline status | Delta |", "| --- | ---: |"])
        for key, value in sorted(pipeline_delta.items()):
            lines.append(f"| {key} | {_signed_number(value)} |")
        lines.append("")
    terminal_outcome_delta = regression.get("terminal_outcome_delta") or {}
    if terminal_outcome_delta:
        lines.extend(["| Terminal outcome | Delta |", "| --- | ---: |"])
        for key, value in sorted(terminal_outcome_delta.items()):
            lines.append(f"| {key} | {_signed_number(value)} |")
        lines.append("")
    stage_delta = regression.get("stage_success_delta") or {}
    if stage_delta:
        lines.extend(["| Stage success | Delta |", "| --- | ---: |"])
        for stage in PIPELINE_STAGES:
            if stage in stage_delta:
                lines.append(f"| {STAGE_LABELS.get(stage, stage)} {stage} | {_signed_number(stage_delta[stage])} |")
        lines.append("")
    lines.extend(_company_identity_drift(regression.get("company_identity_drift"), max_rows=max_rows))
    return lines


def _company_identity_drift(drift: object, max_rows: int) -> list[str]:
    lines = ["### Company Identity Drift", ""]
    if not isinstance(drift, dict) or drift.get("comparison_status") != "available":
        return lines + ["Not available: the baseline does not contain company identity data.", ""]

    lines.extend([
        "| Change | Count | Companies |",
        "| --- | ---: | --- |",
    ])
    for label, key in (
        ("Added", "added_companies"),
        ("Removed", "removed_companies"),
        ("Changed", "changed_companies"),
    ):
        companies = _sorted_strings(drift.get(key))
        lines.append(f"| {label} | {len(companies)} | {_bounded_values(companies, max_rows)} |")
    lines.append("")

    changed_fields = drift.get("changed_fields")
    if isinstance(changed_fields, dict) and changed_fields:
        lines.extend([
            "| Changed field | Companies | Names |",
            "| --- | ---: | --- |",
        ])
        for field, names in sorted(changed_fields.items(), key=lambda item: str(item[0])):
            companies = _sorted_strings(names)
            lines.append(
                f"| {_escape(field)} | {len(companies)} | {_bounded_values(companies, max_rows)} |"
            )
        lines.append("")
    return lines


def _stage_funnel(summary: dict) -> list[str]:
    lines = [
        "## Stage Funnel",
        "",
        "| Stage | Success | Partial | Failed | Not run | Not applicable | Unsupported |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    funnel = summary.get("stage_funnel") or {}
    for stage in PIPELINE_STAGES:
        counts = funnel.get(stage, {})
        lines.append(
            "| {stage} | {success} | {partial} | {failed} | {not_run} | {not_applicable} | {unsupported} |".format(
                stage=f"{STAGE_LABELS.get(stage, stage)} {stage}",
                success=counts.get("success", 0),
                partial=counts.get("partial", 0),
                failed=counts.get("failed", 0),
                not_run=counts.get("not_run", 0),
                not_applicable=counts.get("not_applicable", 0),
                unsupported=counts.get("unsupported", 0),
            )
        )
    lines.append("")
    return lines


def _stage_durations(summary: dict) -> list[str]:
    durations = summary.get("stage_duration_ms") or {}
    if not durations:
        return []
    lines = [
        "## Stage Durations",
        "",
        "| Stage | Count | P50 ms | P95 ms |",
        "| --- | ---: | ---: | ---: |",
    ]
    for stage in PIPELINE_STAGES:
        values = durations.get(stage, {})
        lines.append(
            "| {stage} | {count} | {p50} | {p95} |".format(
                stage=f"{STAGE_LABELS.get(stage, stage)} {stage}",
                count=values.get("count", 0),
                p50=_number_or_dash(values.get("p50")),
                p95=_number_or_dash(values.get("p95")),
            )
        )
    lines.append("")
    return lines


def _simple_count_table(title: str, counts: dict, label: str) -> list[str]:
    lines = [f"## {title}", "", f"| {label} | Count |", "| --- | ---: |"]
    for key, value in sorted(counts.items(), key=lambda item: (-int(item[1]), str(item[0]))):
        lines.append(f"| {key} | {value} |")
    if not counts:
        lines.append("| none | 0 |")
    lines.append("")
    return lines


def _provider_stage_reliability(summary: dict) -> list[str]:
    provider_counts = summary.get("provider_stage_status_counts") or {}
    stage_headers = [STAGE_LABELS.get(stage, stage) for stage in PIPELINE_STAGES]
    lines = [
        "## Provider Stage Reliability",
        "",
        f"| Provider | {' | '.join(stage_headers)} |",
        f"| --- | {' | '.join('---' for _ in stage_headers)} |",
    ]
    for provider, stages in sorted(provider_counts.items(), key=lambda item: str(item[0])):
        stage_cells = [_format_status_counts((stages or {}).get(stage, {})) for stage in PIPELINE_STAGES]
        lines.append(f"| {_escape(provider)} | {' | '.join(stage_cells)} |")
    if not provider_counts:
        lines.append(f"| none | {' | '.join('-' for _ in stage_headers)} |")
    lines.append("")
    return lines


def _provider_reason_codes(summary: dict) -> list[str]:
    provider_counts = summary.get("provider_reason_code_counts") or {}
    lines = [
        "## Provider Reason Codes",
        "",
        "| Provider | Reason | Count |",
        "| --- | --- | ---: |",
    ]
    has_reasons = False
    for provider, reason_counts in sorted(provider_counts.items(), key=lambda item: str(item[0])):
        for reason, count in sorted((reason_counts or {}).items(), key=lambda item: (-int(item[1]), str(item[0]))):
            lines.append(f"| {_escape(provider)} | {_escape(reason)} | {count} |")
            has_reasons = True
    if not has_reasons:
        lines.append("| none | none | 0 |")
    lines.append("")
    return lines


def _failure_clusters(summary: dict) -> list[str]:
    clusters = summary.get("failure_clusters") or []
    lines = [
        "## Actionable Failure Clusters",
        "",
        "| Rank | Stage | Provider | Reason | Companies | Retryable | Outcomes | Dispositions | Examples |",
        "| ---: | --- | --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for rank, cluster in enumerate(clusters, start=1):
        dispositions = ", ".join(
            f"{name}:{count}"
            for name, count in sorted(
                (cluster.get("inventory_disposition_counts") or {}).items()
            )
        ) or "-"
        examples = ", ".join(str(name) for name in cluster.get("company_names") or []) or "-"
        outcomes = ", ".join(
            f"{name}:{count}"
            for name, count in sorted((cluster.get("terminal_outcome_counts") or {}).items())
        ) or "-"
        lines.append(
            "| {rank} | {stage} | {provider} | {reason} | {companies} | {retryable} | {outcomes} | {dispositions} | {examples} |".format(
                rank=rank,
                stage=_escape(cluster.get("stage") or "unknown"),
                provider=_escape(cluster.get("provider") or "unknown"),
                reason=_escape(cluster.get("reason_code") or "unknown"),
                companies=cluster.get("company_count", 0),
                retryable=cluster.get("retryable_count", 0),
                outcomes=_escape(outcomes),
                dispositions=_escape(dispositions),
                examples=_escape(examples),
            )
        )
    if not clusters:
        lines.append("| 0 | none | none | none | 0 | 0 | - | - | - |")
    lines.append("")
    return lines


def _checkpoint_activity(summary: dict) -> list[str]:
    action_counts = summary.get("checkpoint_action_counts") or {}
    stage_counts = summary.get("checkpoint_stage_counts") or {}
    lines = [
        "## Checkpoint Activity",
        "",
        "| Dimension | Value | Count |",
        "| --- | --- | ---: |",
    ]
    for action, count in sorted(action_counts.items(), key=lambda item: (-int(item[1]), str(item[0]))):
        lines.append(f"| Action | {_escape(action)} | {count} |")
    for stage, count in sorted(stage_counts.items(), key=lambda item: (-int(item[1]), str(item[0]))):
        label = f"{STAGE_LABELS.get(str(stage), str(stage))} {stage}"
        lines.append(f"| Stage | {_escape(label)} | {count} |")
    if not action_counts and not stage_counts:
        lines.append("| none | none | 0 |")
    lines.append("")
    return lines


def _format_status_counts(counts: dict) -> str:
    if not counts:
        return "-"
    ordered_statuses = [status for status in STATUS_ORDER if status in counts]
    ordered_statuses.extend(sorted(status for status in counts if status not in STATUS_LABELS))
    return ", ".join(f"{counts[status]} {STATUS_LABELS.get(status, status)}" for status in ordered_statuses)


def _expectations(summary: dict, max_rows: int = 50) -> list[str]:
    checks = summary.get("expectation_checks") or {}
    if not checks:
        return []
    lines = [
        "## Expectations",
        "",
        "| Total | Passed | Failed |",
        "| ---: | ---: | ---: |",
        f"| {checks.get('total', 0)} | {checks.get('passed', 0)} | {checks.get('failed', 0)} |",
        "",
    ]
    failed_identity_checks = []
    for check in checks.get("checks") or []:
        if not isinstance(check, dict) or check.get("passed", False):
            continue
        failure_codes = sorted(
            {
                str(code)
                for code in check.get("failures") or []
                if str(code).startswith("identity:")
            }
        )
        if failure_codes:
            failed_identity_checks.append((str(check.get("company_name") or "unknown"), failure_codes))
    failed_identity_checks.sort(key=lambda item: (item[0].casefold(), item[0], item[1]))
    if failed_identity_checks:
        lines.extend([
            "### Failed Identity Expectations",
            "",
            "| Company | Failure codes |",
            "| --- | --- |",
        ])
        for company, failure_codes in failed_identity_checks[:max_rows]:
            lines.append(f"| {_escape(company)} | {_bounded_values(failure_codes, max_rows)} |")
        if len(failed_identity_checks) > max_rows:
            lines.append(f"| ... {len(failed_identity_checks) - max_rows} more rows | |")
        lines.append("")
    return lines


def _company_matrix(summary: dict, max_rows: int) -> list[str]:
    rows = summary.get("company_stage_matrix") or []
    lines = [
        "## Company Stage Matrix",
        "",
        "| Company | Provider | Pipeline | Reason | S1 | S2 | S3 | S4 | S5 | S6 | S7 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows[:max_rows]:
        statuses = [STATUS_LABELS.get(str(row.get(stage)), str(row.get(stage, "-"))) for stage in PIPELINE_STAGES]
        lines.append(
            "| {company} | {provider} | {pipeline} | {reason} | {statuses} |".format(
                company=_escape(row.get("company_name") or ""),
                provider=_escape(row.get("provider") or ""),
                pipeline=_escape(row.get("pipeline_status") or ""),
                reason=_escape(row.get("reason_code") or ""),
                statuses=" | ".join(statuses),
            )
        )
    if len(rows) > max_rows:
        lines.append(f"| ... {len(rows) - max_rows} more rows | | | | | | | | | | |")
    lines.append("")
    return lines


def _percent(value) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


def _escape(value: str) -> str:
    return str(value).replace("|", "\\|")


def _sorted_strings(values: object) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    return sorted((str(value) for value in values), key=lambda value: (value.casefold(), value))


def _bounded_values(values: list[str], max_rows: int) -> str:
    visible = [_escape(value) for value in values[:max_rows]]
    if len(values) > max_rows:
        visible.append(f"... {len(values) - max_rows} more")
    return ", ".join(visible) or "-"


def _number_or_dash(value) -> str:
    return "-" if value is None else str(value)


def _signed_number(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number > 0:
        return f"+{value}"
    return str(value)


if __name__ == "__main__":
    main()
