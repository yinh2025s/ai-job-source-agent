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
    lines.extend(_regression(summary))
    lines.extend(_stage_funnel(summary))
    lines.extend(_stage_durations(summary))
    lines.extend(_simple_count_table("Provider Distribution", summary.get("provider_counts", {}), "Provider"))
    lines.extend(_provider_stage_reliability(summary))
    lines.extend(_provider_reason_codes(summary))
    lines.extend(_simple_count_table("Reason Codes", summary.get("reason_code_counts", {}), "Reason"))
    lines.extend(_expectations(summary))
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


def _regression(summary: dict) -> list[str]:
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
    stage_delta = regression.get("stage_success_delta") or {}
    if stage_delta:
        lines.extend(["| Stage success | Delta |", "| --- | ---: |"])
        for stage in PIPELINE_STAGES:
            if stage in stage_delta:
                lines.append(f"| {STAGE_LABELS.get(stage, stage)} {stage} | {_signed_number(stage_delta[stage])} |")
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


def _format_status_counts(counts: dict) -> str:
    if not counts:
        return "-"
    ordered_statuses = [status for status in STATUS_ORDER if status in counts]
    ordered_statuses.extend(sorted(status for status in counts if status not in STATUS_LABELS))
    return ", ".join(f"{counts[status]} {STATUS_LABELS.get(status, status)}" for status in ordered_statuses)


def _expectations(summary: dict) -> list[str]:
    checks = summary.get("expectation_checks") or {}
    if not checks:
        return []
    return [
        "## Expectations",
        "",
        "| Total | Passed | Failed |",
        "| ---: | ---: | ---: |",
        f"| {checks.get('total', 0)} | {checks.get('passed', 0)} | {checks.get('failed', 0)} |",
        "",
    ]


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
