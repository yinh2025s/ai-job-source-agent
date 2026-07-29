import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.prepare_diagnostic_cohort import prepare_diagnostic_cohort
from scripts.prepare_diagnostic_measurement import prepare_measurement
from scripts.run_prepared_diagnostic_measurement import (
    PreparedMeasurementRunError,
    load_and_validate_preflight,
    run_prepared_measurement,
)
from tests.test_prepare_diagnostic_measurement import candidate, run_config


class RunPreparedDiagnosticMeasurementTests(unittest.TestCase):
    def _prepared(self, root: Path):
        pool = root / "pool.json"
        pool.write_text(json.dumps([candidate(1), candidate(2)]), encoding="utf-8")
        cohort, manifest = prepare_diagnostic_cohort(
            candidate_paths=[pool],
            excluded_paths=[],
            quotas=[("Software Engineer", 2)],
            cohort_name="execution-guard",
        )
        cohort_path = root / "cohort.json"
        manifest_path = root / "manifest.json"
        config_path = root / "config.json"
        cohort_path.write_text(json.dumps(cohort), encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        config_path.write_text(json.dumps(run_config(2)), encoding="utf-8")
        artifact_root = root / "run"
        prepared = prepare_measurement(
            cohort_path=cohort_path,
            cohort_manifest_path=manifest_path,
            run_config_path=config_path,
            artifact_root=artifact_root,
            repo_root=Path(__file__).resolve().parents[1],
            runtime_version=(3, 12),
            git_identity=("commit123", "tree123"),
        )
        return prepared, artifact_root / "contract" / "preflight.json"

    def _identity_patches(self):
        return (
            patch(
                "scripts.run_prepared_diagnostic_measurement._clean_git_identity",
                return_value=("commit123", "tree123"),
            ),
            patch(
                "scripts.run_prepared_diagnostic_measurement._adapter_version",
                return_value="2026-07-29.286",
            ),
        )

    def test_executes_only_after_runtime_identity_and_contract_revalidation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            prepared, preflight = self._prepared(root)
            first, second = self._identity_patches()
            with first, second:
                status = run_prepared_measurement(
                    preflight,
                    command_runner=lambda *args, **kwargs: subprocess.CompletedProcess(
                        args[0], 0
                    ),
                    finalizer=lambda _path: {"status": "accepted"},
                )

            self.assertEqual(status["status"], "accepted")
            saved = json.loads(
                Path(prepared["paths"]["measurement_status"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(saved["status"], "live_completed_unverified")

    def test_records_nonzero_live_exit_and_refuses_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            prepared, preflight = self._prepared(root)
            first, second = self._identity_patches()
            with first, second, self.assertRaisesRegex(
                PreparedMeasurementRunError, "status 7"
            ):
                run_prepared_measurement(
                    preflight,
                    command_runner=lambda *args, **kwargs: subprocess.CompletedProcess(
                        args[0], 7
                    ),
                )
            status_path = Path(prepared["paths"]["measurement_status"])
            self.assertEqual(
                json.loads(status_path.read_text(encoding="utf-8"))["status"],
                "live_failed",
            )
            first, second = self._identity_patches()
            with first, second, self.assertRaisesRegex(
                PreparedMeasurementRunError, "already exists"
            ):
                run_prepared_measurement(preflight)

    def test_records_finalization_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            prepared, preflight = self._prepared(root)
            first, second = self._identity_patches()

            def fail_finalization(_path):
                raise RuntimeError("synthetic finalization failure")

            with first, second, self.assertRaises(RuntimeError):
                run_prepared_measurement(
                    preflight,
                    command_runner=lambda *args, **kwargs: subprocess.CompletedProcess(
                        args[0], 0
                    ),
                    finalizer=fail_finalization,
                )

            saved = json.loads(
                Path(prepared["paths"]["measurement_status"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(saved["status"], "verification_failed")
            self.assertEqual(
                saved["issues"],
                ["finalization_exception:RuntimeError"],
            )

    def test_rejects_changed_contract_and_runtime_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            prepared, preflight = self._prepared(root)
            Path(prepared["paths"]["cohort"]).write_text("[]", encoding="utf-8")
            first, second = self._identity_patches()
            with first, second, self.assertRaisesRegex(
                PreparedMeasurementRunError, "cohort_file_sha256"
            ):
                load_and_validate_preflight(preflight)

    def test_rejects_tampered_live_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _prepared, preflight = self._prepared(root)
            payload = json.loads(preflight.read_text(encoding="utf-8"))
            payload["live_command"].extend(("--workers", "4"))
            preflight.write_text(json.dumps(payload), encoding="utf-8")
            first, second = self._identity_patches()
            with first, second, self.assertRaisesRegex(
                PreparedMeasurementRunError, "differs from frozen contract"
            ):
                load_and_validate_preflight(preflight)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _prepared, preflight = self._prepared(root)
            with (
                patch(
                    "scripts.run_prepared_diagnostic_measurement._clean_git_identity",
                    return_value=("different", "tree123"),
                ),
                patch(
                    "scripts.run_prepared_diagnostic_measurement._adapter_version",
                    return_value="2026-07-29.286",
                ),
                self.assertRaisesRegex(
                    PreparedMeasurementRunError, "commit changed"
                ),
            ):
                load_and_validate_preflight(preflight)


if __name__ == "__main__":
    unittest.main()
