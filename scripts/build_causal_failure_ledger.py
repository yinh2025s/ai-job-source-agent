from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.causal_evidence import build_causal_ledger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Merge ordered development trace artifacts and build an evidence-backed "
            "causal failure ledger."
        )
    )
    parser.add_argument(
        "--artifact",
        action="append",
        required=True,
        help=(
            "Trace/results JSON or batch completion directory in ascending "
            "precedence order; repeat for focused replacements."
        ),
    )
    parser.add_argument(
        "--cohort",
        help="Optional frozen cohort JSON used to reject missing or out-of-cohort observations.",
    )
    parser.add_argument(
        "--accepted-terminals",
        help=(
            "Optional reviewed terminal manifest. Focused terminal upgrades not "
            "listed here are ignored."
        ),
    )
    parser.add_argument(
        "--reviewed-clusters",
        help=(
            "Optional manifest of cluster signatures that passed current-version "
            "reproduction and nonzero batch-recovery review."
        ),
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--markdown-output")
    parser.add_argument("--minimum-company-count", type=int, default=3)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    artifacts = []
    for value in args.artifact:
        path = Path(value)
        records = _load_records(path)
        artifacts.append((str(path), records))
    cohort_records = None
    if args.cohort:
        cohort_payload = json.loads(Path(args.cohort).read_text(encoding="utf-8"))
        cohort_records = (
            cohort_payload.get("postings")
            if isinstance(cohort_payload, dict)
            else cohort_payload
        )
    accepted_terminals = None
    if args.accepted_terminals:
        accepted_payload = json.loads(
            Path(args.accepted_terminals).read_text(encoding="utf-8")
        )
        accepted_terminals = (
            accepted_payload.get("accepted_terminals")
            if isinstance(accepted_payload, dict)
            else None
        )
    reviewed_cluster_signatures = None
    if args.reviewed_clusters:
        reviewed_payload = json.loads(
            Path(args.reviewed_clusters).read_text(encoding="utf-8")
        )
        reviewed_cluster_signatures = set(
            reviewed_payload.get("reviewed_clusters", [])
            if isinstance(reviewed_payload, dict)
            else []
        )
    ledger = build_causal_ledger(
        artifacts,
        minimum_company_count=args.minimum_company_count,
        cohort_records=cohort_records,
        accepted_terminals=accepted_terminals,
        reviewed_cluster_signatures=reviewed_cluster_signatures,
    )
    expected_outcomes = (
        accepted_payload.get("expected_outcome_counts")
        if args.accepted_terminals and isinstance(accepted_payload, dict)
        else None
    )
    if expected_outcomes is not None and ledger["durable_outcome_counts"] != expected_outcomes:
        raise SystemExit(
            "durable outcome assertion failed: "
            f"expected={expected_outcomes} actual={ledger['durable_outcome_counts']}"
        )
    expected_record_count = (
        accepted_payload.get("expected_record_count")
        if args.accepted_terminals and isinstance(accepted_payload, dict)
        else None
    )
    if expected_record_count is not None and ledger["record_count"] != expected_record_count:
        raise SystemExit(
            "record count assertion failed: "
            f"expected={expected_record_count} actual={ledger['record_count']}"
        )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    if args.markdown_output:
        markdown = Path(args.markdown_output)
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(render_markdown(ledger), encoding="utf-8")
    print(
        "causal ledger: "
        f"records={ledger['record_count']} "
        f"backlog={ledger['causal_backlog_count']} "
        f"outcomes={ledger['durable_outcome_counts']} "
        f"candidate_clusters={ledger['candidate_cluster_count']} "
        f"qualified_clusters={ledger['qualified_cluster_count']}",
        flush=True,
    )
    print(f"output: {output}", flush=True)


def render_markdown(ledger: dict) -> str:
    lines = [
        "# Causal Failure Ledger",
        "",
        f"- Records: {ledger['record_count']}",
        f"- Causal backlog: {ledger['causal_backlog_count']}",
        f"- Minimum independent companies: {ledger['minimum_company_count']}",
        f"- Qualified clusters: {ledger['qualified_cluster_count']}",
        f"- Count-qualified candidate clusters: {ledger['candidate_cluster_count']}",
        "",
        "## Clusters",
        "",
        "| Qualified | Companies | Records | Category | Trigger | Code path | Blockers |",
        "| --- | ---: | ---: | --- | --- | --- | --- |",
    ]
    for cluster in ledger["clusters"]:
        lines.append(
            "| {qualified} | {companies} | {records} | {category} | {trigger} | {path} | {blockers} |".format(
                qualified="yes" if cluster["qualified_for_implementation"] else "no",
                companies=cluster["company_count"],
                records=cluster["record_count"],
                category=_escape(cluster["category"]),
                trigger=_escape(cluster["trigger"]),
                path=_escape(cluster["code_path"]),
                blockers=_escape(", ".join(cluster["qualification_blockers"])),
            )
        )
    lines.extend(
        [
            "",
            "## Records",
            "",
            "| Company | Job ID | Outcome | Category | Trigger | Artifact |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for record in ledger["records"]:
        lines.append(
            "| {company} | {job_id} | {outcome} | {category} | {trigger} | {artifact} |".format(
                company=_escape(record["company_name"]),
                job_id=record["linkedin_job_id"],
                outcome=_escape(record["durable_outcome"]),
                category=_escape(record["category"]),
                trigger=_escape(record["trigger"]),
                artifact=_escape(record["artifact_label"]),
            )
        )
    return "\n".join(lines) + "\n"


def _escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _load_records(path: Path) -> list[dict]:
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"artifact file must contain a JSON list: {path}")
        return payload
    if not path.is_dir():
        raise ValueError(f"artifact does not exist: {path}")
    records = []
    for completion_path in sorted(path.rglob("*.json")):
        payload = json.loads(completion_path.read_text(encoding="utf-8"))
        result = payload.get("result") if isinstance(payload, dict) else None
        trace = payload.get("trace") if isinstance(payload, dict) else None
        if not isinstance(trace, dict) and isinstance(result, dict):
            trace = result.get("trace")
        if not isinstance(trace, dict):
            raise ValueError(
                f"completion does not contain result.trace: {completion_path}"
            )
        records.append(trace)
    return records


if __name__ == "__main__":
    main()
