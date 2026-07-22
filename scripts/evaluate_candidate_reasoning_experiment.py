from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.candidate_reasoning_evaluation import (
    CandidateReasoningABObservation,
    evaluate_candidate_reasoning_ab,
    evaluate_candidate_reasoning_gate,
)
from job_source_agent.candidate_reasoning_experiment import (
    EXPERIMENT_SCHEMA_VERSION,
    ExperimentIntegrityError,
    extract_deterministic_candidate_urls,
    load_evaluator_labels,
    load_public_cohort,
    load_ranker_evidence_urls,
    reasoning_input_digest,
    verify_sealed_files,
    write_json_atomic,
)


FLASH_INPUT_PRICE_PER_MILLION_USD = 0.14
FLASH_OUTPUT_PRICE_PER_MILLION_USD = 0.28
LEGACY_CAUSAL_DEFAULTS = {
    "llm_plan_used": False,
    "llm_rank_used": False,
    "llm_causal_contribution": "none",
}


def evaluate_experiment(root: Path, labels_path: Path) -> dict[str, Any]:
    manifest_path = root / "capture-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != EXPERIMENT_SCHEMA_VERSION
        or manifest.get("status") != "sealed"
    ):
        raise ExperimentIntegrityError("capture is not sealed")
    verify_sealed_files(root, manifest)

    cohort = load_public_cohort(root / "cohort.json")
    labels = load_evaluator_labels(labels_path)
    if set(labels) != {record["record_id"] for record in cohort}:
        raise ExperimentIntegrityError("label IDs do not match the frozen cohort")

    baseline = _records_by_id(root / "baseline" / "trace.json")
    treatment = _records_by_id(root / "treatment" / "trace.json")
    reasoning = _reasoning_by_digest(root / "treatment" / "candidate-records.json")
    evidence = load_ranker_evidence_urls(
        root / "treatment" / "decisions" / "llm-decisions.jsonl"
    )
    usage = _decision_usage_by_digest(
        root / "treatment" / "decisions" / "llm-decisions.jsonl"
    )
    replay = json.loads((root / "replay" / "bundle-manifest.json").read_text(encoding="utf-8"))
    replay_by_company = {
        item.get("company_name"): item.get("classification")
        for item in replay.get("outcome_gate", {}).get("records", [])
        if isinstance(item, dict)
    }

    observations: list[CandidateReasoningABObservation] = []
    supplemental: list[dict[str, Any]] = []
    for record in cohort:
        record_id = record["record_id"]
        company_name = record["company_name"]
        baseline_record = baseline[record_id]
        treatment_record = treatment[record_id]
        digest = reasoning_input_digest(record)
        reasoning_record = reasoning.get(digest)
        reference = labels[record_id]
        baseline_top = _normalize_candidates_for_reference(
            extract_deterministic_candidate_urls(baseline_record), reference
        )
        treatment_top = _normalize_candidates_for_reference(
            (
                tuple(item["url"] for item in reasoning_record["candidates"][:3])
                if reasoning_record is not None
                else extract_deterministic_candidate_urls(treatment_record)
            ),
            reference,
        )
        frozen_evidence = evidence.get(digest)
        if frozen_evidence is None:
            frozen_evidence = tuple(
                dict.fromkeys(
                    [
                        *extract_deterministic_candidate_urls(treatment_record),
                        *treatment_top,
                    ]
                )
            )
        frozen_evidence = _normalize_candidates_for_reference(
            frozen_evidence, reference
        )
        usage_record = usage.get(digest, _empty_usage())
        baseline_result = _result_record(baseline_record)
        treatment_result = _result_record(treatment_record)
        normalized_reference = _evaluation_url(reference)
        observations.append(
            CandidateReasoningABObservation(
                record_id=record_id,
                eligible_g=True,
                reference_candidate_url=normalized_reference,
                reference_website_url=normalized_reference,
                frozen_search_evidence_urls=frozen_evidence,
                baseline_top_candidate_urls=baseline_top,
                treatment_top_candidate_urls=treatment_top,
                baseline_verified_website_url=(
                    _normalize_candidate_for_reference(
                        baseline_result["company_website_url"], reference
                    )
                    if baseline_result.get("company_website_url")
                    else None
                ),
                treatment_verified_website_url=(
                    _normalize_candidate_for_reference(
                        treatment_result["company_website_url"], reference
                    )
                    if treatment_result.get("company_website_url")
                    else None
                ),
                treatment_cross_company=_verified_website_conflicts(
                    treatment_result.get("company_website_url"), reference
                ),
                treatment_cross_tenant=_published_identity_conflict(treatment_result),
                replay_mismatch=replay_by_company.get(company_name) != "reproduced",
                llm_calls=usage_record["calls"],
                prompt_tokens=usage_record["prompt_tokens"],
                completion_tokens=usage_record["completion_tokens"],
                estimated_cost_usd=usage_record["cost_usd"],
                llm_latency_ms=usage_record["latency_ms"],
                advisory_failure=bool(
                    reasoning_record is not None
                    and reasoning_record.get("advisory_failure") is not None
                ),
                # Legacy captures do not persist evidence that an advisory
                # decision was adopted. Calls and arm differences alone are
                # not causal evidence, so historical evaluations fail closed.
                **LEGACY_CAUSAL_DEFAULTS,
            )
        )
        supplemental.append(
            {
                "record_id": record_id,
                "company_name": company_name,
                "job_title": record.get("job_title"),
                "job_location": record.get("job_location"),
                "reference_website_url": reference,
                "baseline_website_url": baseline_result.get("company_website_url") or None,
                "treatment_website_url": treatment_result.get("company_website_url") or None,
                "baseline_opening_url": baseline_result.get("open_position_url"),
                "treatment_opening_url": treatment_result.get("open_position_url"),
                "treatment_job_list_url": treatment_result.get("job_list_page_url"),
                "treatment_identity_assertion": treatment_result.get("identity_assertion"),
                "replay_classification": replay_by_company.get(company_name),
                "llm_calls": usage_record["calls"],
                **LEGACY_CAUSAL_DEFAULTS,
                "advisory_failure": (
                    reasoning_record.get("advisory_failure")
                    if reasoning_record is not None
                    else None
                ),
            }
        )

    report = evaluate_candidate_reasoning_ab(observations)
    gate = evaluate_candidate_reasoning_gate(report)
    output = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "capture_manifest_sha256": _sha256(manifest_path),
        "model": manifest["model"],
        "prompt_version": manifest["prompt_version"],
        "report": _to_json(report),
        "promotion_gate": {
            "passed": gate.passed,
            "failures": list(gate.failures),
        },
        "supplemental": {
            "baseline_exact": sum(bool(item["baseline_opening_url"]) for item in supplemental),
            "treatment_exact": sum(bool(item["treatment_opening_url"]) for item in supplemental),
            "replay_reproduced": sum(
                item["replay_classification"] == "reproduced" for item in supplemental
            ),
            "records": supplemental,
        },
    }
    write_json_atomic(root / "evaluation-report.json", output)
    (root / "manual-identity-review.md").write_text(
        _manual_review(supplemental), encoding="utf-8"
    )
    return output


def _records_by_id(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ExperimentIntegrityError(f"{path.name} must be a list")
    indexed: dict[str, dict[str, Any]] = {}
    for item in payload:
        record_id = item.get("experiment_record_id") if isinstance(item, dict) else None
        if not isinstance(record_id, str) or record_id in indexed:
            raise ExperimentIntegrityError(f"{path.name} record identity is invalid")
        indexed[record_id] = item
    return indexed


def _reasoning_by_digest(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ExperimentIntegrityError("candidate records must be a list")
    return {
        item["input_evidence_digest"]: item
        for item in payload
        if isinstance(item, dict) and isinstance(item.get("input_evidence_digest"), str)
    }


def _decision_usage_by_digest(path: Path) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return indexed
    for line in path.read_text(encoding="utf-8").splitlines():
        envelope = json.loads(line)
        record = envelope.get("record") if isinstance(envelope, dict) else None
        if not isinstance(record, dict):
            raise ExperimentIntegrityError("decision usage envelope is invalid")
        key = record.get("key")
        request = record.get("sanitized_request")
        if not isinstance(key, dict) or not isinstance(request, dict):
            raise ExperimentIntegrityError("decision usage record is invalid")
        digest = (
            key.get("input_evidence_digest")
            if key.get("decision_kind") == "query_plan"
            else request.get("invocation_input_evidence_digest")
        )
        token_usage = record.get("token_usage")
        if not isinstance(digest, str) or not isinstance(token_usage, dict):
            raise ExperimentIntegrityError("decision usage linkage is invalid")
        item = indexed.setdefault(digest, _empty_usage())
        prompt = int(token_usage.get("prompt_tokens", 0))
        completion = int(token_usage.get("completion_tokens", 0))
        item["calls"] += 1
        item["prompt_tokens"] += prompt
        item["completion_tokens"] += completion
        item["latency_ms"] += float(record.get("duration_ms", 0.0))
        item["cost_usd"] += (
            prompt * FLASH_INPUT_PRICE_PER_MILLION_USD
            + completion * FLASH_OUTPUT_PRICE_PER_MILLION_USD
        ) / 1_000_000
    return indexed


def _empty_usage() -> dict[str, Any]:
    return {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "latency_ms": 0.0,
        "cost_usd": 0.0,
    }


def _result_record(trace_record: Mapping[str, Any]) -> Mapping[str, Any]:
    return trace_record


def _verified_website_conflicts(actual: object, reference: str) -> bool:
    return (
        isinstance(actual, str)
        and bool(actual)
        and _evaluation_host(actual) != _evaluation_host(reference)
    )


def _published_identity_conflict(result: Mapping[str, Any]) -> bool:
    if not result.get("open_position_url"):
        return False
    assertion = result.get("identity_assertion")
    return not isinstance(assertion, dict) or assertion.get("verdict") != "verified"


def _canonical(url: str) -> str:
    return _evaluation_url(url).rstrip("/").casefold()


def _evaluation_host(url: str) -> str:
    return urlsplit(_evaluation_url(url)).netloc


def _evaluation_url(url: str) -> str:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(("https", hostname + port, path, parsed.query, ""))


def _normalize_candidate_for_reference(url: str, reference: str) -> str:
    candidate = urlsplit(_evaluation_url(url))
    expected = urlsplit(_evaluation_url(reference))
    expected_path = expected.path.rstrip("/") or "/"
    candidate_path = candidate.path.rstrip("/") or "/"
    if candidate.netloc == expected.netloc and (
        expected_path == "/"
        or candidate_path == expected_path
        or candidate_path.startswith(expected_path + "/")
    ):
        return _evaluation_url(reference)
    return _evaluation_url(url)


def _normalize_candidates_for_reference(
    urls: tuple[str, ...], reference: str
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(_normalize_candidate_for_reference(url, reference) for url in urls)
    )


def _to_json(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _to_json(item) for key, item in value.__dict__.items()}
    if isinstance(value, tuple):
        return [_to_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_json(item) for key, item in value.items()}
    return value


def _manual_review(records: list[dict[str, Any]]) -> str:
    lines = [
        "# LLM Candidate Reasoning Manual Identity Review",
        "",
        "Every treatment opening must be checked against company, title, location, provider, tenant and URL.",
        "",
    ]
    for record in records:
        lines.extend(
            [
                f"## {record['record_id']} {record['company_name']}",
                "",
                f"- Target: {record.get('job_title')} | {record.get('job_location')}",
                f"- Reference website: {record.get('reference_website_url')}",
                f"- Treatment website: {record.get('treatment_website_url')}",
                f"- Job list: {record.get('treatment_job_list_url')}",
                f"- Opening: {record.get('treatment_opening_url')}",
                f"- Identity assertion: `{json.dumps(record.get('treatment_identity_assertion'), sort_keys=True)}`",
                f"- Replay: {record.get('replay_classification')}",
                "- [ ] Company identity verified",
                "- [ ] Title verified",
                "- [ ] Location verified",
                "- [ ] Provider and tenant verified",
                "- [ ] Opening URL verified",
                "",
            ]
        )
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    args = parser.parse_args()
    report = evaluate_experiment(args.root, args.labels)
    print(json.dumps(report["promotion_gate"], sort_keys=True))


if __name__ == "__main__":
    main()
