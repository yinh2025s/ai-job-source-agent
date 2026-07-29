import json
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_diagnostic_cohort import prepare_diagnostic_cohort
from scripts.prepare_diagnostic_measurement import (
    DiagnosticMeasurementError,
    prepare_measurement,
)
from scripts.live_batch_eval import build_parser as build_live_parser
from scripts.live_batch_eval import validate_artifact_args


def candidate(index: int) -> dict:
    return {
        "company_name": f"Measurement Company {index}",
        "linkedin_job_url": (
            f"https://www.linkedin.com/jobs/view/measurement-role-{9200000 + index}"
        ),
        "linkedin_company_url": (
            f"https://www.linkedin.com/company/measurement-{index}"
        ),
        "job_title": f"Software Engineer {index}",
        "job_location": "United States",
        "source_trace": {
            "candidate_collection": {
                "matched_keywords": ["Software Engineer"],
                "query_contract_sha256": "a" * 64,
            }
        },
    }


def run_config(size: int) -> dict:
    return {
        "schema_version": "1.0",
        "cohort_size": size,
        "candidate_discovery_engine": "stage_v1",
        "search_backend": "legacy",
        "workers": 1,
        "fetch_timeout_seconds": 8,
        "fetch_retries": 1,
        "retry_base_delay_seconds": 0.2,
        "career_search_timeout_seconds": 6,
        "max_career_search_queries": 5,
        "verify_limit": 3,
        "max_career_candidates": 6,
        "max_career_fetches": 5,
        "max_career_transport_calls": 32,
        "max_ats_board_fetches": 5,
        "max_job_pages": 8,
        "max_job_board_attempts": 3,
        "company_time_budget_seconds": 180,
        "website_time_budget_seconds": 45,
        "evaluate_all_candidate_routes": True,
        "render_js": False,
        "skip_sitemap": False,
        "full_outcome_replay_after_live": True,
    }


class PrepareDiagnosticMeasurementTests(unittest.TestCase):
    def _contract(self, root: Path, size: int = 2):
        pool_path = root / "pool.json"
        pool_path.write_text(
            json.dumps([candidate(index) for index in range(size)]),
            encoding="utf-8",
        )
        cohort, manifest = prepare_diagnostic_cohort(
            candidate_paths=[pool_path],
            excluded_paths=[],
            quotas=[("Software Engineer", size)],
            cohort_name="measurement-diagnostic",
        )
        cohort_path = root / "cohort.json"
        manifest_path = root / "manifest.json"
        config_path = root / "config.json"
        cohort_path.write_text(json.dumps(cohort), encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        config_path.write_text(json.dumps(run_config(size)), encoding="utf-8")
        return cohort_path, manifest_path, config_path

    def test_prepares_disjoint_layout_and_frozen_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cohort, manifest, config = self._contract(root)
            artifact_root = root / "run"
            prepared = prepare_measurement(
                cohort_path=cohort,
                cohort_manifest_path=manifest,
                run_config_path=config,
                artifact_root=artifact_root,
                repo_root=Path(__file__).resolve().parents[1],
                runtime_version=(3, 12),
                git_identity=("abc123", "tree123"),
            )

            self.assertEqual(prepared["status"], "prepared_not_executed")
            self.assertEqual(prepared["record_count"], 2)
            self.assertTrue((artifact_root / "contract" / "preflight.json").is_file())
            self.assertTrue((artifact_root / "state" / "checkpoints").is_dir())
            self.assertTrue((artifact_root / "replay" / "full").is_dir())
            command = prepared["command"]
            self.assertEqual(
                command[1],
                "scripts/run_prepared_diagnostic_measurement.py",
            )
            live_command = prepared["live_command"]
            self.assertIn("--enable-parallel-candidate-discovery", live_command)
            self.assertIn("--require-full-cohort", live_command)
            self.assertIn("--no-resume", live_command)
            self.assertIn("--evaluate-all-candidate-routes", live_command)
            self.assertEqual(
                live_command[live_command.index("--replay-bundle-limit") + 1],
                "2",
            )
            live_args = build_live_parser().parse_args(live_command[2:])
            validate_artifact_args(live_args)

    def test_rejects_existing_artifact_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cohort, manifest, config = self._contract(root)
            artifact_root = root / "run"
            artifact_root.mkdir()
            with self.assertRaisesRegex(
                DiagnosticMeasurementError, "must not already exist"
            ):
                prepare_measurement(
                    cohort_path=cohort,
                    cohort_manifest_path=manifest,
                    run_config_path=config,
                    artifact_root=artifact_root,
                    repo_root=root,
                    runtime_version=(3, 12),
                    git_identity=("abc123", "tree123"),
                )

    def test_rejects_manifest_and_config_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cohort, manifest, config = self._contract(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["cohort_sha256"] = "0" * 64
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                DiagnosticMeasurementError, "digest does not match"
            ):
                prepare_measurement(
                    cohort_path=cohort,
                    cohort_manifest_path=manifest,
                    run_config_path=config,
                    artifact_root=root / "run",
                    repo_root=root,
                    runtime_version=(3, 12),
                    git_identity=("abc123", "tree123"),
                )

    def test_requires_release_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cohort, manifest, config = self._contract(root)
            with self.assertRaisesRegex(
                DiagnosticMeasurementError, "CPython 3.12"
            ):
                prepare_measurement(
                    cohort_path=cohort,
                    cohort_manifest_path=manifest,
                    run_config_path=config,
                    artifact_root=root / "run",
                    repo_root=root,
                    runtime_version=(3, 14),
                    git_identity=("abc123", "tree123"),
                )

    def test_rejects_unsafe_parallelism_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cohort, manifest, config = self._contract(root)
            payload = json.loads(config.read_text(encoding="utf-8"))
            payload["workers"] = 5
            config.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                DiagnosticMeasurementError, "exceeds diagnostic bounds"
            ):
                prepare_measurement(
                    cohort_path=cohort,
                    cohort_manifest_path=manifest,
                    run_config_path=config,
                    artifact_root=root / "run",
                    repo_root=root,
                    runtime_version=(3, 12),
                    git_identity=("abc123", "tree123"),
                )

    def test_rejects_unbound_query_collection_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cohort, manifest, config = self._contract(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["candidate_collection_contract"] = {
                "status": "unbound",
                "sha256": None,
                "bound_record_count": 0,
            }
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                DiagnosticMeasurementError, "not bound"
            ):
                prepare_measurement(
                    cohort_path=cohort,
                    cohort_manifest_path=manifest,
                    run_config_path=config,
                    artifact_root=root / "run",
                    repo_root=root,
                    runtime_version=(3, 12),
                    git_identity=("abc123", "tree123"),
                )


if __name__ == "__main__":
    unittest.main()
