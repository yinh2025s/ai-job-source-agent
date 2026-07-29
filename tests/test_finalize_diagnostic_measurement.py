import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.audit_strict_replay import _canonical_digest
from scripts.finalize_diagnostic_measurement import finalize_measurement
from scripts.prepare_diagnostic_cohort import prepare_diagnostic_cohort
from scripts.prepare_diagnostic_measurement import prepare_measurement
from scripts.run_prepared_diagnostic_measurement import (
    _status_payload,
    _write_json_atomic,
)
from tests.test_audit_strict_replay import measurement_fixture
from tests.test_prepare_diagnostic_measurement import run_config


class FinalizeDiagnosticMeasurementTests(unittest.TestCase):
    def _measurement(self, root: Path, count: int = 2):
        fixture = measurement_fixture(count)
        pool = root / "pool.json"
        pool_records = []
        for source in fixture["frozen_cohort"]:
            record = dict(source)
            record["source_trace"] = {
                "candidate_collection": {
                    "matched_keywords": ["Engineer"],
                    "query_contract_sha256": "a" * 64,
                }
            }
            pool_records.append(record)
        pool.write_text(json.dumps(pool_records), encoding="utf-8")
        cohort, manifest = prepare_diagnostic_cohort(
            candidate_paths=[pool],
            excluded_paths=[],
            quotas=[("Engineer", count)],
            cohort_name="finalizer-fixture",
        )
        cohort_path = root / "cohort.json"
        manifest_path = root / "manifest.json"
        config_path = root / "config.json"
        cohort_path.write_text(json.dumps(cohort), encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        config_path.write_text(json.dumps(run_config(count)), encoding="utf-8")
        prepared = prepare_measurement(
            cohort_path=cohort_path,
            cohort_manifest_path=manifest_path,
            run_config_path=config_path,
            artifact_root=root / "run",
            repo_root=Path(__file__).resolve().parents[1],
            runtime_version=(3, 12),
            git_identity=("commit123", "tree123"),
        )
        paths = {key: Path(value) for key, value in prepared["paths"].items()}

        fixture["manifest"]["adapter_version"] = prepared["adapter_version"]
        fixture["manifest"]["paths"] = {
            "input": "replay-input.json",
            "results": "replay-results.json",
            "trace": "replay-trace.json",
        }
        payloads = {
            paths["live"] / "results.json": fixture["live_results"],
            paths["live"] / "trace.json": fixture["live_trace"],
            paths["replay"] / "bundle-manifest.json": fixture["manifest"],
            paths["replay"] / "replay-input.json": fixture["replay_input"],
            paths["replay"] / "replay-results.json": fixture["replay_results"],
            paths["replay"] / "replay-trace.json": fixture["replay_trace"],
        }
        for path, payload in payloads.items():
            path.write_text(json.dumps(payload), encoding="utf-8")

        companies_sha = _canonical_digest(cohort)
        summary = {
            "total": count,
            "run_configuration_digest": fixture["manifest"][
                "run_configuration_digest"
            ],
            "evaluation_manifest": {"companies_sha256": companies_sha},
        }
        routes = {
            "record_count": count,
            "cohort_companies_sha256": companies_sha,
            "run_configuration_digest": summary["run_configuration_digest"],
            "records": [{} for _ in range(count)],
        }
        (paths["live"] / "summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
        (paths["live"] / "route-evaluation.json").write_text(
            json.dumps(routes), encoding="utf-8"
        )
        status = _status_payload(
            prepared,
            "live_completed_unverified",
            live_return_code=0,
        )
        _write_json_atomic(paths["measurement_status"], status)
        return prepared, fixture

    def test_accepts_only_fully_bound_live_replay_exact_and_privacy_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            prepared, _fixture = self._measurement(root)
            preflight = Path(prepared["paths"]["preflight"])
            with patch(
                "scripts.finalize_diagnostic_measurement.load_and_validate_preflight",
                return_value=prepared,
            ):
                report = finalize_measurement(preflight)

            self.assertEqual(report["status"], "accepted")
            self.assertEqual(report["exact_count"], 0)
            status = json.loads(
                Path(prepared["paths"]["measurement_status"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(status["status"], "accepted")
            self.assertEqual(status["issues"], [])

    def test_privacy_match_prevents_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            prepared, _fixture = self._measurement(root)
            marker = b"xox" + b"b-1234567890-abcdefghijklmnop"
            (Path(prepared["paths"]["live"]) / "credential.bin").write_bytes(marker)
            preflight = Path(prepared["paths"]["preflight"])
            with patch(
                "scripts.finalize_diagnostic_measurement.load_and_validate_preflight",
                return_value=prepared,
            ):
                report = finalize_measurement(preflight)

            self.assertEqual(report["status"], "verification_failed")
            self.assertIn("artifact_privacy_matches", report["issues"])


if __name__ == "__main__":
    unittest.main()
