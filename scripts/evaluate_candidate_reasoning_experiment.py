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
    reasoning_input_digest,
    verify_sealed_files,
    write_json_atomic,
)
from job_source_agent.candidate_reasoning_contracts import SearchQuerySpec
from job_source_agent.candidate_reasoning_frozen_search import (
    FilesystemFrozenQueryStore,
)


FLASH_INPUT_PRICE_PER_MILLION_USD = 0.14
FLASH_OUTPUT_PRICE_PER_MILLION_USD = 0.28
MIN_CANDIDATE_RECALL_DELTA_PP = 25.0
MIN_STRICT_CAUSAL_RECOVERY_FRACTION = 0.40
MAX_CALLS_PER_COMPANY = 2
URL_HYPOTHESIS_EVALUATOR_VERSION = "1.2"


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
    decision_artifacts = _decision_artifacts_by_digest(
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
    source_metrics: list[dict[str, Any]] = []
    for record in cohort:
        record_id = record["record_id"]
        company_name = record["company_name"]
        baseline_record = baseline[record_id]
        treatment_record = treatment[record_id]
        digest = reasoning_input_digest(record)
        reasoning_record = reasoning.get(digest)
        artifacts = decision_artifacts.get(digest, _empty_decision_artifacts())
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
        frozen_search = _frozen_search_urls_for_queries(
            root / "treatment" / "query-responses",
            artifacts["queries"],
            artifacts["executed_query_ids"],
        )
        frozen_search = _normalize_candidates_for_reference(
            frozen_search, reference
        )
        deterministic_treatment = _normalize_candidates_for_reference(
            extract_deterministic_candidate_urls(treatment_record), reference
        )
        known_non_llm = set((*deterministic_treatment, *frozen_search))
        frozen_non_llm_evidence = tuple(
            dict.fromkeys(
                [
                    *(url for url in treatment_top if url in known_non_llm),
                    *deterministic_treatment,
                    *frozen_search,
                ]
            )
        )[:10]
        frozen_hypotheses = _normalize_candidates_for_reference(
            tuple(item["url"] for item in artifacts["url_hypotheses"]),
            reference,
        )
        usage_record = usage.get(digest, _empty_usage())
        baseline_result = _result_record(baseline_record)
        treatment_result = _result_record(treatment_record)
        normalized_reference = _evaluation_url(reference)
        causal = _strict_causal_recovery(
            reference=reference,
            baseline_result=baseline_result,
            treatment_result=treatment_result,
            treatment_trace=treatment_record,
            reasoning_record=reasoning_record,
            frozen_search_urls=frozen_search,
            url_hypotheses=artifacts["url_hypotheses"],
        )
        adopted_llm_urls = _llm_adopted_candidate_urls(treatment_record)
        normalized_hypothesis_urls = {
            _evaluation_url(item["url"]) for item in artifacts["url_hypotheses"]
        }
        invented_adopted_urls = tuple(
            url
            for url in adopted_llm_urls
            if _evaluation_url(url) not in normalized_hypothesis_urls
        )
        observation_contribution = (
            "url_hypothesis_recovery"
            if causal["strict"] and causal["surface"] in {"website", "career"}
            else "none"
        )
        observations.append(
            CandidateReasoningABObservation(
                record_id=record_id,
                eligible_g=True,
                reference_candidate_url=normalized_reference,
                reference_website_url=normalized_reference,
                frozen_search_evidence_urls=frozen_non_llm_evidence,
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
                llm_plan_used=bool(
                    reasoning_record is not None
                    and reasoning_record.get("llm_plan_used") is True
                ),
                llm_rank_used=bool(
                    reasoning_record is not None
                    and reasoning_record.get("llm_rank_used") is True
                ),
                llm_causal_contribution=observation_contribution,
                frozen_llm_hypothesis_urls=frozen_hypotheses,
            )
        )
        source_metrics.append(
            {
                "search_at_3": normalized_reference in frozen_search[:3],
                "search_at_10": normalized_reference in frozen_search[:10],
                "hypothesis_at_3": normalized_reference in frozen_hypotheses[:3],
                "strict_causal": causal["strict"],
                "causal_surface": causal["surface"],
            }
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
                "planner_calls": usage_record["planner_calls"],
                "ranker_calls": usage_record["ranker_calls"],
                "failed_calls": usage_record["failed_calls"],
                "prompt_tokens": usage_record["prompt_tokens"],
                "completion_tokens": usage_record["completion_tokens"],
                "llm_latency_ms": usage_record["latency_ms"],
                "estimated_cost_usd": usage_record["cost_usd"],
                "llm_plan_used": bool(
                    reasoning_record is not None
                    and reasoning_record.get("llm_plan_used") is True
                ),
                "llm_rank_used": bool(
                    reasoning_record is not None
                    and reasoning_record.get("llm_rank_used") is True
                ),
                "llm_hypothesis_used": bool(
                    reasoning_record is not None
                    and reasoning_record.get("llm_hypothesis_used") is True
                ),
                "llm_causal_contribution": (
                    f"{causal['surface']}_url_hypothesis_recovery"
                    if causal["strict"]
                    else "none"
                ),
                "causal_evidence": causal["evidence"],
                "adopted_llm_candidate_urls": list(adopted_llm_urls),
                "invented_adopted_urls": list(invented_adopted_urls),
                "treatment_cross_brand": _verified_website_conflicts(
                    treatment_result.get("company_website_url"), reference
                ),
                "frozen_search_urls": list(frozen_search),
                "frozen_url_hypotheses": list(artifacts["url_hypotheses"]),
                "advisory_failure": (
                    reasoning_record.get("advisory_failure")
                    if reasoning_record is not None
                    else None
                ),
            }
        )

    report = evaluate_candidate_reasoning_ab(observations)
    legacy_gate = evaluate_candidate_reasoning_gate(report)
    url_hypothesis_metrics = _url_hypothesis_metrics(
        report=report,
        source_metrics=source_metrics,
        supplemental=supplemental,
        replay=replay,
    )
    gate = _url_hypothesis_promotion_gate(
        report=report,
        metrics=url_hypothesis_metrics,
        manifest=manifest,
    )
    output = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "evaluator_version": URL_HYPOTHESIS_EVALUATOR_VERSION,
        "capture_manifest_sha256": _sha256(manifest_path),
        "model": manifest["model"],
        "prompt_version": manifest["prompt_version"],
        "report": _to_json(report),
        "promotion_gate": {
            "passed": gate["passed"],
            "failures": gate["failures"],
            "contract": {
                "minimum_candidate_recall_delta_percentage_points": (
                    MIN_CANDIDATE_RECALL_DELTA_PP
                ),
                "minimum_strict_causal_recovery_fraction": (
                    MIN_STRICT_CAUSAL_RECOVERY_FRACTION
                ),
                "maximum_calls_per_company": MAX_CALLS_PER_COMPANY,
                "zero_tolerance": [
                    "wrong_verified_url",
                    "invented_or_modified_candidate_url",
                    "cross_company",
                    "cross_brand",
                    "cross_tenant",
                    "invented_adopted_url",
                    "replay_mismatch",
                    "replay_fixture_gap",
                    "budget_overrun",
                ],
            },
        },
        "legacy_candidate_reasoning_gate": {
            "passed": legacy_gate.passed,
            "failures": list(legacy_gate.failures),
        },
        "url_hypothesis_metrics": url_hypothesis_metrics,
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


def _decision_artifacts_by_digest(path: Path) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return indexed
    for line in path.read_text(encoding="utf-8").splitlines():
        envelope = json.loads(line)
        record = envelope.get("record") if isinstance(envelope, dict) else None
        if not isinstance(record, dict):
            raise ExperimentIntegrityError("decision artifact envelope is invalid")
        key = record.get("key")
        request = record.get("sanitized_request")
        response = record.get("sanitized_response")
        if not isinstance(key, dict) or not isinstance(request, dict):
            raise ExperimentIntegrityError("decision artifact record is invalid")
        decision_kind = key.get("decision_kind")
        digest = (
            key.get("input_evidence_digest")
            if decision_kind == "query_plan"
            else request.get("invocation_input_evidence_digest")
        )
        if not isinstance(digest, str):
            raise ExperimentIntegrityError("decision artifact linkage is invalid")
        item = indexed.setdefault(digest, _empty_decision_artifacts())
        if decision_kind == "query_plan":
            if record.get("status") != "success":
                continue
            if not isinstance(response, dict):
                raise ExperimentIntegrityError("planner response artifact is invalid")
            queries = response.get("queries", [])
            hypotheses = response.get("url_hypotheses", [])
            if not isinstance(queries, list) or not isinstance(hypotheses, list):
                raise ExperimentIntegrityError("planner source artifacts are invalid")
            item["queries"] = tuple(
                _validated_query_artifact(value) for value in queries
            )
            item["url_hypotheses"] = tuple(
                _validated_hypothesis_artifact(value) for value in hypotheses
            )
        elif decision_kind == "candidate_rank":
            candidates = request.get("candidates")
            if not isinstance(candidates, list):
                raise ExperimentIntegrityError("ranker candidate artifacts are invalid")
            item["ranker_candidates"] = tuple(
                _validated_ranker_candidate(value) for value in candidates
            )
            item["executed_query_ids"] = tuple(
                dict.fromkeys(
                    value["query_id"]
                    for value in candidates
                    if isinstance(value, dict)
                    and isinstance(value.get("query_id"), str)
                    and value["query_id"].startswith("llm-query-")
                )
            )
            ranked = (
                response.get("ranked_candidates", [])
                if isinstance(response, dict)
                else []
            )
            if not isinstance(ranked, list):
                raise ExperimentIntegrityError("ranker response artifact is invalid")
            item["ranked_candidate_ids"] = tuple(
                value["candidate_id"]
                for value in ranked
                if isinstance(value, dict)
                and isinstance(value.get("candidate_id"), str)
            )
    return indexed


def _empty_decision_artifacts() -> dict[str, tuple[Any, ...]]:
    return {
        "queries": (),
        "url_hypotheses": (),
        "ranker_candidates": (),
        "ranked_candidate_ids": (),
        "executed_query_ids": (),
    }


def _validated_query_artifact(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ExperimentIntegrityError("planner query artifact is invalid")
    query = value.get("query")
    purpose = value.get("purpose")
    try:
        validated = SearchQuerySpec(query=query, purpose=purpose)
    except (TypeError, ValueError) as error:
        raise ExperimentIntegrityError("planner query artifact is invalid") from error
    return {"query": validated.query, "purpose": validated.purpose}


def _validated_hypothesis_artifact(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ExperimentIntegrityError("URL hypothesis artifact is invalid")
    url = value.get("url")
    purpose = value.get("purpose")
    confidence = value.get("confidence")
    if (
        not isinstance(url, str)
        or not url.startswith("https://")
        or purpose not in {"official_website", "career_site", "provider_site"}
        or confidence not in {"high", "medium", "low"}
    ):
        raise ExperimentIntegrityError("URL hypothesis artifact is invalid")
    return {"url": url, "purpose": purpose, "confidence": confidence}


def _validated_ranker_candidate(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ExperimentIntegrityError("ranker candidate artifact is invalid")
    required = ("candidate_id", "url", "source")
    if any(not isinstance(value.get(field), str) for field in required):
        raise ExperimentIntegrityError("ranker candidate artifact is invalid")
    return {field: value[field] for field in required}


def _frozen_search_urls_for_queries(
    store_root: Path,
    queries: tuple[dict[str, str], ...],
    executed_query_ids: tuple[str, ...],
) -> tuple[str, ...]:
    if not queries or not executed_query_ids:
        return ()
    store = FilesystemFrozenQueryStore(store_root)
    urls: list[str] = []
    for index, payload in enumerate(queries, start=1):
        if f"llm-query-{index}" not in executed_query_ids:
            continue
        response = store.load(SearchQuerySpec(**payload))
        if response is None:
            raise ExperimentIntegrityError(
                "executed planner query is missing its frozen query response"
            )
        for candidate in response.candidates:
            if candidate.url not in urls:
                urls.append(candidate.url)
            if len(urls) >= 10:
                return tuple(urls)
    return tuple(urls)


def _strict_causal_recovery(
    *,
    reference: str,
    baseline_result: Mapping[str, Any],
    treatment_result: Mapping[str, Any],
    treatment_trace: Mapping[str, Any],
    reasoning_record: Mapping[str, Any] | None,
    frozen_search_urls: tuple[str, ...],
    url_hypotheses: tuple[dict[str, str], ...],
) -> dict[str, Any]:
    none = {"strict": False, "surface": "none", "evidence": {}}
    if (
        reasoning_record is None
        or reasoning_record.get("llm_plan_used") is not True
        or reasoning_record.get("llm_hypothesis_used") is not True
    ):
        return none
    normalized_search = _normalize_candidates_for_reference(
        frozen_search_urls, reference
    )

    baseline_opening = baseline_result.get("open_position_url")
    treatment_opening = treatment_result.get("open_position_url")
    identity = treatment_result.get("identity_assertion")
    board_trace = _stage_trace(treatment_trace, "job_board_discovery")
    board_selected = board_trace.get("selected")
    if (
        not baseline_opening
        and isinstance(treatment_opening, str)
        and treatment_opening
        and isinstance(identity, dict)
        and identity.get("verdict") == "verified"
        and isinstance(board_selected, dict)
        and board_selected.get("source_kind") == "llm_url_hypothesis"
    ):
        selected_url = board_selected.get("url")
        provider_hypotheses = tuple(
            item for item in url_hypotheses if item["purpose"] == "provider_site"
        )
        matched_provider = next(
            (
                item
                for item in provider_hypotheses
                if isinstance(selected_url, str)
                and _same_or_descendant_url(selected_url, item["url"])
            ),
            None,
        )
        search_supplied_provider = (
            isinstance(selected_url, str)
            and any(
                _same_or_descendant_url(selected_url, search_url)
                or _same_or_descendant_url(search_url, selected_url)
                for search_url in frozen_search_urls
            )
        )
        if matched_provider is not None and not search_supplied_provider:
            return {
                "strict": True,
                "surface": "ats",
                "evidence": {
                    "hypothesis_url": matched_provider["url"],
                    "hypothesis_purpose": "provider_site",
                    "selected_job_board_url": selected_url,
                    "opening_url": treatment_opening,
                    "identity_verdict": "verified",
                },
            }

    matching = tuple(
        item
        for item in url_hypotheses
        if _normalize_candidate_for_reference(item["url"], reference)
        == _evaluation_url(reference)
    )
    if not matching:
        return none
    if _evaluation_url(reference) in normalized_search:
        # A search result independently supplied the labelled URL, so the
        # URL hypothesis is not uniquely causal.
        return none

    baseline_website = baseline_result.get("company_website_url")
    treatment_website = treatment_result.get("company_website_url")
    website_recovered = (
        (
            not isinstance(baseline_website, str)
            or _normalize_candidate_for_reference(baseline_website, reference)
            != _evaluation_url(reference)
        )
        and isinstance(treatment_website, str)
        and _normalize_candidate_for_reference(treatment_website, reference)
        == _evaluation_url(reference)
    )
    website_trace = _stage_trace(treatment_trace, "website_resolution")
    selected = website_trace.get("selected")
    if website_recovered and isinstance(selected, dict):
        selected_url = selected.get("url")
        selected_reason = selected.get("reason")
        if (
            isinstance(selected_url, str)
            and _normalize_candidate_for_reference(selected_url, reference)
            == _evaluation_url(reference)
            and isinstance(selected_reason, str)
            and "candidate reasoning" in selected_reason.casefold()
        ):
            purpose = matching[0]["purpose"]
            surface = "career" if purpose == "career_site" else "website"
            return {
                "strict": True,
                "surface": surface,
                "evidence": {
                    "hypothesis_url": matching[0]["url"],
                    "hypothesis_purpose": purpose,
                    "selected_url": selected_url,
                    "selection_reason": selected_reason,
                },
            }

    return none


def _stage_trace(trace_record: Mapping[str, Any], stage: str) -> Mapping[str, Any]:
    trace = trace_record.get("trace")
    stages = trace.get("stages") if isinstance(trace, Mapping) else None
    value = stages.get(stage) if isinstance(stages, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def _same_or_descendant_url(actual: str, hypothesis: str) -> bool:
    actual_parts = urlsplit(_evaluation_url(actual))
    hypothesis_parts = urlsplit(_evaluation_url(hypothesis))
    if actual_parts.netloc != hypothesis_parts.netloc:
        return False
    hypothesis_path = hypothesis_parts.path.rstrip("/") or "/"
    actual_path = actual_parts.path.rstrip("/") or "/"
    return (
        hypothesis_path == "/"
        or actual_path == hypothesis_path
        or actual_path.startswith(hypothesis_path + "/")
    )


def _llm_adopted_candidate_urls(
    treatment_trace: Mapping[str, Any],
) -> tuple[str, ...]:
    adopted: list[str] = []
    website_selected = _stage_trace(treatment_trace, "website_resolution").get(
        "selected"
    )
    if isinstance(website_selected, Mapping):
        url = website_selected.get("url")
        reason = website_selected.get("reason")
        if (
            isinstance(url, str)
            and isinstance(reason, str)
            and "candidate reasoning" in reason.casefold()
        ):
            adopted.append(url)
    board_selected = _stage_trace(treatment_trace, "job_board_discovery").get(
        "selected"
    )
    if (
        isinstance(board_selected, Mapping)
        and board_selected.get("source_kind") == "llm_url_hypothesis"
    ):
        url = board_selected.get("url")
        if isinstance(url, str) and url not in adopted:
            adopted.append(url)
    return tuple(adopted)


def _url_hypothesis_metrics(
    *,
    report: Any,
    source_metrics: list[dict[str, Any]],
    supplemental: list[dict[str, Any]],
    replay: Mapping[str, Any],
) -> dict[str, Any]:
    total = len(source_metrics)

    def metric(field: str) -> dict[str, float | int]:
        count = sum(item[field] is True for item in source_metrics)
        return {
            "count": count,
            "denominator": total,
            "percentage": count / total * 100.0 if total else 0.0,
        }

    strict_count = sum(item["strict_causal"] is True for item in source_metrics)
    surface_counts = {
        surface: sum(
            item["strict_causal"] is True
            and item["causal_surface"] == surface
            for item in source_metrics
        )
        for surface in ("website", "career", "ats")
    }
    classifications = replay.get("outcome_gate", {}).get(
        "classification_counts", {}
    )
    return {
        "search_candidate_recall_at_3": metric("search_at_3"),
        "search_candidate_recall_at_10": metric("search_at_10"),
        "llm_url_hypothesis_recall_at_3": metric("hypothesis_at_3"),
        "combined_candidate_recall_at_3": _to_json(
            report.treatment_candidate_recall_at_3
        ),
        "baseline_candidate_recall_at_3": _to_json(
            report.baseline_candidate_recall_at_3
        ),
        "candidate_recall_delta_percentage_points": (
            report.candidate_recall_delta_percentage_points
        ),
        "baseline_verified_website_recall": _to_json(
            report.baseline_verified_website_recall
        ),
        "treatment_verified_website_recall": _to_json(
            report.treatment_verified_website_recall
        ),
        "baseline_exact": sum(
            bool(item["baseline_opening_url"]) for item in supplemental
        ),
        "treatment_exact": sum(
            bool(item["treatment_opening_url"]) for item in supplemental
        ),
        "strict_causal_recoveries": {
            "count": strict_count,
            "denominator": total,
            "fraction": strict_count / total if total else 0.0,
        },
        "eligible_recovery_rate": {
            "count": strict_count,
            "denominator": total,
            "fraction": strict_count / total if total else 0.0,
        },
        "causal_contribution_counts": surface_counts,
        "wrong_verified_url_count": report.treatment_wrong_verified_url_count,
        "cross_brand_count": sum(
            item["treatment_cross_brand"] is True for item in supplemental
        ),
        "invented_or_modified_candidate_url_count": (
            report.invented_or_modified_treatment_url_count
        ),
        "invented_adopted_url_count": sum(
            len(item["invented_adopted_urls"]) for item in supplemental
        ),
        "cross_company_count": report.cross_company_count,
        "cross_tenant_count": report.cross_tenant_count,
        "replay_mismatch_count": report.replay_mismatch_count,
        "replay_fixture_gap_count": sum(
            int(count)
            for name, count in classifications.items()
            if name != "reproduced"
        ),
        "calls_per_company_mean": report.calls_per_company_mean,
        "calls_per_company_max": report.calls_per_company_max,
        "planner_call_count": sum(item["planner_calls"] for item in supplemental),
        "ranker_call_count": sum(item["ranker_calls"] for item in supplemental),
        "failed_call_count": sum(item["failed_calls"] for item in supplemental),
        "total_prompt_tokens": report.total_prompt_tokens,
        "total_completion_tokens": report.total_completion_tokens,
        "total_estimated_cost_usd": report.total_estimated_cost_usd,
        "latency_p50_ms": report.latency_p50_ms,
        "latency_p95_ms": report.latency_p95_ms,
        "latency_max_ms": max(
            (float(item["llm_latency_ms"]) for item in supplemental),
            default=0.0,
        ),
    }


def _url_hypothesis_promotion_gate(
    *,
    report: Any,
    metrics: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    if report.record_count != 18:
        failures.append("fixed cohort must contain exactly 18 records")
    if (
        metrics["candidate_recall_delta_percentage_points"]
        < MIN_CANDIDATE_RECALL_DELTA_PP
    ):
        failures.append("candidate recall uplift is below 25 percentage points")
    if (
        metrics["strict_causal_recoveries"]["fraction"]
        < MIN_STRICT_CAUSAL_RECOVERY_FRACTION
    ):
        failures.append("strict causal recovery is below 40 percent")
    zero_tolerance = {
        "wrong_verified_url_count": "wrong verified URL",
        "invented_or_modified_candidate_url_count": "invented candidate URL",
        "invented_adopted_url_count": "invented adopted URL",
        "cross_company_count": "cross-company adoption",
        "cross_brand_count": "cross-brand adoption",
        "cross_tenant_count": "cross-tenant adoption",
        "replay_mismatch_count": "replay mismatch",
        "replay_fixture_gap_count": "replay fixture gap",
    }
    for field, label in zero_tolerance.items():
        if metrics[field]:
            failures.append(f"{label} must remain zero")
    if metrics["calls_per_company_max"] > MAX_CALLS_PER_COMPANY:
        failures.append("calls per company exceed the frozen maximum")
    if int(manifest.get("actual_call_count", 0)) > int(
        manifest.get("call_limit", 0)
    ):
        failures.append("capture exceeded the call budget")
    if float(manifest.get("actual_cost_usd", 0.0)) > float(
        manifest.get("hard_cost_cap_usd", 0.0)
    ):
        failures.append("capture exceeded the cost budget")
    return {"passed": not failures, "failures": failures}


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
        if key.get("decision_kind") == "query_plan":
            item["planner_calls"] += 1
        elif key.get("decision_kind") == "candidate_rank":
            item["ranker_calls"] += 1
        if record.get("status") != "success":
            item["failed_calls"] += 1
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
        "planner_calls": 0,
        "ranker_calls": 0,
        "failed_calls": 0,
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
