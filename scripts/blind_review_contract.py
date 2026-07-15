from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


class BlindChainError(ValueError):
    pass


def verify_execution_chain(
    *, results_path: Path, trace_path: Path, summary_path: Path,
    cohort_path: Path, holdout_manifest_path: Path, execution_manifest_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    source_paths = {"results": results_path, "trace": trace_path, "summary": summary_path}
    source_bytes = {name: path.read_bytes() for name, path in source_paths.items()}
    results, traces, summary = (
        json.loads(source_bytes["results"]), json.loads(source_bytes["trace"]),
        json.loads(source_bytes["summary"]),
    )
    cohort_bytes = cohort_path.read_bytes()
    cohort = json.loads(cohort_bytes)
    holdout_bytes = holdout_manifest_path.read_bytes()
    holdout = json.loads(holdout_bytes)
    execution = json.loads(execution_manifest_path.read_text(encoding="utf-8"))
    if not isinstance(results, list) or not isinstance(traces, list) or not isinstance(summary, dict):
        raise BlindChainError("source artifacts have invalid top-level types")
    if not isinstance(cohort, list) or not isinstance(holdout, dict) or not isinstance(execution, dict):
        raise BlindChainError("freeze/execution artifacts have invalid top-level types")
    cohort_sha = hashlib.sha256(_canonical_json_bytes(cohort)).hexdigest()
    if holdout.get("cohort_sha256") != cohort_sha or execution.get("cohort_sha256") != cohort_sha:
        raise BlindChainError("cohort digest chain is invalid")
    if execution.get("holdout_manifest_sha256") != hashlib.sha256(holdout_bytes).hexdigest():
        raise BlindChainError("execution is not bound to the frozen holdout manifest")
    if execution.get("status") != "complete" or execution.get("live_execution_count") != 1:
        raise BlindChainError("execution is not one complete live run")
    if execution.get("cohort_provenance_before_execution") != "blind_unseen":
        raise BlindChainError("execution did not consume a blind_unseen cohort")
    if execution.get("cohort_provenance_after_execution") != "blind_observed":
        raise BlindChainError("execution did not mark the cohort observed")
    for name, content in source_bytes.items():
        if execution.get("artifact_sha256", {}).get(name) != hashlib.sha256(content).hexdigest():
            raise BlindChainError(f"execution {name} digest does not match")
    if len(results) != len(cohort) or len(traces) != len(cohort):
        raise BlindChainError("cohort/result/trace record counts differ")

    cohort_keys = {_cohort_key(record) for record in cohort}
    frozen_keys = {_frozen_key(record) for record in holdout.get("records", [])}
    result_keys = {_result_key(record) for record in results}
    trace_keys = {_result_key(record) for record in traces}
    if (
        len(cohort_keys) != len(cohort)
        or len(frozen_keys) != len(cohort)
        or cohort_keys != frozen_keys
        or frozen_keys != result_keys
        or result_keys != trace_keys
    ):
        raise BlindChainError("frozen/result/trace cohort identities differ")
    trace_by_key = {_result_key(record): record for record in traces}
    for result in results:
        trace = trace_by_key[_result_key(result)]
        for field in (
            "company_name", "linkedin_job_url", "linkedin_job_title",
            "linkedin_job_location", "company_website_url", "career_page_url",
            "job_list_page_url", "open_position_url", "candidate_open_position_url",
            "provider", "status", "pipeline_status", "stages",
        ):
            if result.get(field) != trace.get(field):
                raise BlindChainError(f"result/trace semantic drift in {field}")
    provenance = {
        "cohort_provenance": "blind_unseen_at_execution",
        "run_id": execution.get("run_id"),
        "source_results_sha256": hashlib.sha256(source_bytes["results"]).hexdigest(),
        "source_trace_sha256": hashlib.sha256(source_bytes["trace"]).hexdigest(),
        "source_summary_sha256": hashlib.sha256(source_bytes["summary"]).hexdigest(),
        "cohort_sha256": cohort_sha,
        "holdout_manifest_sha256": hashlib.sha256(holdout_bytes).hexdigest(),
        "execution_manifest_sha256": hashlib.sha256(execution_manifest_path.read_bytes()).hexdigest(),
    }
    return results, traces, summary, provenance


def verify_ssh_signature(
    *, content: bytes, signature_path: Path, allowed_signers_path: Path,
    signer_identity: str,
) -> None:
    completed = subprocess.run(
        [
            "ssh-keygen", "-Y", "verify", "-f", str(allowed_signers_path),
            "-I", signer_identity, "-n", "ai-job-source-human-review",
            "-s", str(signature_path),
        ],
        input=content,
        capture_output=True,
        timeout=15,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise BlindChainError(f"human review SSH signature is invalid: {detail}")


def _frozen_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("company_name"), record.get("linkedin_job_url"),
        record.get("job_title"), record.get("job_location"),
    )


def _cohort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("company_name"), record.get("linkedin_job_url"),
        record.get("job_title"), record.get("job_location"),
    )


def _result_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("company_name"), record.get("linkedin_job_url"),
        record.get("linkedin_job_title"), record.get("linkedin_job_location"),
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
