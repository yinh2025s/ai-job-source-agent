import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from job_source_agent.replay_record_plan import build_replay_record_plans
from job_source_agent.run_configuration import AgentConfig, DeterministicRunConfig
from scripts.audit_strict_replay import (
    StrictReplayAuditError,
    _canonical_digest,
    audit_strict_replay,
    main,
)
from scripts.export_replay_input import export_replay_records


def manifest(count: int) -> dict:
    return {
        "status": "success",
        "adapter_version": "test-adapter",
        "record_integrity": {
            "status": "passed",
            "counts": {
                "comparison_count": count,
                "export_attempted_count": count,
                "exported_count": count,
                "filter_matched_count": count,
                "limit_omitted_count": 0,
                "replayability_dropped_count": 0,
                "result_count": count,
                "selected_count": count,
                "source_result_count": count,
                "trace_count": count,
            },
        },
        "outcome_gate": {
            "status": "passed",
            "classification_counts": {
                "budget_recovery": 0,
                "expected_transition": 0,
                "fixture_gap": 0,
                "mismatch": 0,
                "reproduced": count,
            },
        },
    }


def deterministic_config() -> dict:
    return DeterministicRunConfig.from_agent_config(
        AgentConfig(
            max_candidates=6,
            max_job_pages=8,
            max_career_candidate_fetches=5,
            max_career_search_queries=5,
            max_ats_board_fetches=5,
            career_search_timeout=6.0,
            max_career_discovery_transport_calls=32,
            max_job_board_attempts=3,
            enable_parallel_candidate_discovery=True,
            evaluate_all_candidate_routes=True,
        )
    ).to_payload()


def diagnostic_config(count: int) -> dict:
    return {"schema_version": "1.0", "cohort_size": count}


def measurement_fixture(count: int = 3) -> dict:
    cohort = []
    live_results = []
    live_trace = []
    run_payload = deterministic_config()
    run_digest = DeterministicRunConfig.from_payload(run_payload).digest
    for index in range(count):
        job_id = str(8800000 + index)
        url = f"https://www.linkedin.com/jobs/view/role-{job_id}"
        cohort.append(
            {
                "company_name": f"Company {index}",
                "linkedin_job_url": url,
                "linkedin_company_url": (
                    f"https://www.linkedin.com/company/company-{index}"
                ),
                "job_title": f"Engineer {index}",
                "job_location": "United States",
            }
        )
        result = {
            "company_name": f"Company {index}",
            "company_website_url": f"https://company-{index}.example",
            "linkedin_job_url": url,
            "linkedin_company_url": (
                f"https://www.linkedin.com/company/company-{index}"
            ),
            "linkedin_job_title": f"Engineer {index}",
            "linkedin_job_location": "United States",
            "pipeline_status": "partial",
            "run_configuration": run_payload,
            "run_configuration_digest": run_digest,
            "execution_fingerprint": hashlib.sha256(
                f"execution-{index}".encode()
            ).hexdigest(),
            "stages": [
                {
                    "stage": "linkedin_discovery",
                    "status": "success",
                    "reason_code": None,
                },
                {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
            ],
        }
        live_results.append(result)
        live_trace.append({**result, "trace": {"source_trace": {}}})

    replay_input = export_replay_records(
        live_results,
        SimpleNamespace(
            input="results.json",
            pipeline_status=None,
            stage=None,
            stage_status=None,
            reason_code=None,
            provider=None,
            limit=count,
            include_missing_website=False,
        ),
    )
    plans = build_replay_record_plans(live_results, replay_input)
    for record, plan in zip(replay_input, plans, strict=True):
        record.setdefault("source_trace", {}).setdefault("replay", {})[
            "record_id"
        ] = plan.record_id
        record.pop("hiring_entity_name", None)
    replay_results = [dict(record) for record in replay_input]
    replay_trace = [
        {**record, "trace": {"source_trace": record["source_trace"]}}
        for record in replay_input
    ]

    bundle = manifest(count)
    bundle["run_configuration"] = run_payload
    bundle["run_configuration_digest"] = run_digest
    bundle["record_plans"] = [
        {
            "source_ordinal": plan.source_ordinal,
            "record_id": plan.record_id,
            "evidence_mode": plan.evidence_mode,
        }
        for plan in plans
    ]
    bundle["outcome_gate"]["records"] = [
        {"record_id": plan.record_id, "classification": "reproduced"}
        for plan in plans
    ]

    run_config = diagnostic_config(count)
    cohort_bytes = json.dumps(cohort).encode()
    config_bytes = json.dumps(run_config).encode()
    preflight = {
        "measurement_kind": "development_diagnostic_nonsealed",
        "record_count": count,
        "cohort_sha256": _canonical_digest(cohort),
        "cohort_file_sha256": hashlib.sha256(cohort_bytes).hexdigest(),
        "run_configuration_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "runtime_code_commit": "abc123",
        "adapter_version": "test-adapter",
    }
    digests = {
        "preflight": "1" * 64,
        "cohort_file": preflight["cohort_file_sha256"],
        "live_results": "2" * 64,
        "live_trace": "3" * 64,
        "run_config": preflight["run_configuration_sha256"],
        "replay_manifest": "4" * 64,
    }
    return {
        "manifest": bundle,
        "preflight": preflight,
        "frozen_cohort": cohort,
        "live_results": live_results,
        "live_trace": live_trace,
        "run_config": run_config,
        "replay_input": replay_input,
        "replay_results": replay_results,
        "replay_trace": replay_trace,
        "artifact_digests": digests,
    }


def audit_fixture(fixture: dict, count: int = 3) -> dict:
    return audit_strict_replay(
        fixture["manifest"],
        expected_records=count,
        preflight=fixture["preflight"],
        frozen_cohort=fixture["frozen_cohort"],
        live_results=fixture["live_results"],
        live_trace=fixture["live_trace"],
        run_config=fixture["run_config"],
        replay_input=fixture["replay_input"],
        replay_results=fixture["replay_results"],
        replay_trace=fixture["replay_trace"],
        artifact_digests=fixture["artifact_digests"],
    )


class AuditStrictReplayTests(unittest.TestCase):
    def test_accepts_measurement_bound_full_replay(self):
        report = audit_fixture(measurement_fixture())
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["mode"], "measurement")
        self.assertEqual(report["issues"], [])

    def test_legacy_requires_explicit_mode(self):
        self.assertEqual(
            audit_strict_replay(
                manifest(3), expected_records=3, legacy=True
            )["status"],
            "passed",
        )
        report = audit_strict_replay(manifest(3), expected_records=3)
        self.assertEqual(report["status"], "failed")
        self.assertIn("preflight_missing_or_invalid", report["issues"])

    def test_budget_recovery_is_a_strict_failure(self):
        fixture = measurement_fixture()
        payload = fixture["manifest"]
        payload["outcome_gate"]["classification_counts"]["reproduced"] = 2
        payload["outcome_gate"]["classification_counts"]["budget_recovery"] = 1
        report = audit_fixture(fixture)
        self.assertIn("budget_recovery_nonzero", report["issues"])
        self.assertIn("reproduced_count_mismatch", report["issues"])

    def test_digest_and_adapter_tampering_are_rejected(self):
        fixture = measurement_fixture()
        fixture["artifact_digests"]["cohort_file"] = "f" * 64
        fixture["manifest"]["adapter_version"] = "other"
        report = audit_fixture(fixture)
        self.assertIn("cohort_file_digest_mismatch", report["issues"])
        self.assertIn("replay_adapter_mismatch", report["issues"])

    def test_linkedin_id_reordering_is_rejected(self):
        fixture = measurement_fixture()
        fixture["live_trace"][0], fixture["live_trace"][1] = (
            fixture["live_trace"][1],
            fixture["live_trace"][0],
        )
        report = audit_fixture(fixture)
        self.assertIn(
            "live_trace_linkedin_job_id_order_mismatch", report["issues"]
        )

    def test_unrelated_green_manifest_is_rejected(self):
        fixture = measurement_fixture()
        unrelated = manifest(3)
        unrelated["adapter_version"] = "test-adapter"
        unrelated["run_configuration"] = fixture["manifest"]["run_configuration"]
        unrelated["run_configuration_digest"] = fixture["manifest"][
            "run_configuration_digest"
        ]
        unrelated["record_plans"] = [
            {
                "source_ordinal": index + 1,
                "record_id": "a" * 64,
                "evidence_mode": "legacy_global_latest",
            }
            for index in range(3)
        ]
        unrelated["outcome_gate"]["records"] = [
            {"record_id": "a" * 64, "classification": "reproduced"}
            for _ in range(3)
        ]
        fixture["manifest"] = unrelated
        report = audit_fixture(fixture)
        self.assertEqual(report["status"], "failed")
        self.assertIn("replay_record_plans_mismatch", report["issues"])
        self.assertIn("replay_outcome_record_ids_mismatch", report["issues"])

    def test_cli_measurement_mode_loads_and_binds_bundle_files(self):
        fixture = measurement_fixture(2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "replay"
            bundle.mkdir()
            paths = {
                "preflight": root / "preflight.json",
                "cohort": root / "cohort.json",
                "live_results": root / "results.json",
                "live_trace": root / "trace.json",
                "run_config": root / "run-config.json",
                "manifest": bundle / "bundle-manifest.json",
                "replay_input": bundle / "replay-input.json",
                "replay_results": bundle / "replay-results.json",
                "replay_trace": bundle / "replay-trace.json",
                "output": root / "audit.json",
            }
            payloads = {
                "preflight": fixture["preflight"],
                "cohort": fixture["frozen_cohort"],
                "live_results": fixture["live_results"],
                "live_trace": fixture["live_trace"],
                "run_config": fixture["run_config"],
                "replay_input": fixture["replay_input"],
                "replay_results": fixture["replay_results"],
                "replay_trace": fixture["replay_trace"],
            }
            for key, payload in payloads.items():
                paths[key].write_text(json.dumps(payload), encoding="utf-8")
            fixture["preflight"]["cohort_file_sha256"] = hashlib.sha256(
                paths["cohort"].read_bytes()
            ).hexdigest()
            fixture["preflight"]["run_configuration_sha256"] = hashlib.sha256(
                paths["run_config"].read_bytes()
            ).hexdigest()
            paths["preflight"].write_text(
                json.dumps(fixture["preflight"]), encoding="utf-8"
            )
            fixture["manifest"]["paths"] = {
                "input": "replay-input.json",
                "results": "replay-results.json",
                "trace": "replay-trace.json",
            }
            paths["manifest"].write_text(
                json.dumps(fixture["manifest"]), encoding="utf-8"
            )

            main(
                [
                    "--manifest",
                    str(paths["manifest"]),
                    "--expected-records",
                    "2",
                    "--preflight",
                    str(paths["preflight"]),
                    "--cohort",
                    str(paths["cohort"]),
                    "--live-results",
                    str(paths["live_results"]),
                    "--live-trace",
                    str(paths["live_trace"]),
                    "--run-config",
                    str(paths["run_config"]),
                    "--output",
                    str(paths["output"]),
                ]
            )
            self.assertEqual(
                json.loads(paths["output"].read_text())["status"], "passed"
            )

    def test_invalid_inputs_fail_closed(self):
        with self.assertRaises(StrictReplayAuditError):
            audit_strict_replay([], expected_records=3)
        with self.assertRaises(StrictReplayAuditError):
            audit_strict_replay({}, expected_records=0)


if __name__ == "__main__":
    unittest.main()
