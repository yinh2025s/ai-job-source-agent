from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_source_agent.evaluation_history import (
    CorruptEvaluationHistoryError,
    EvaluationHistory,
    EvaluationHistoryError,
    cohort_identities_compatible,
    derive_cohort_identity,
)


ROOT = Path(__file__).resolve().parents[1]


def _summary(
    opening: float,
    successes: int,
    *,
    companies_sha256: str | None = None,
    total: int = 10,
) -> dict:
    summary = {
        "total": total,
        "rates": {"opening": opening, "job_list": 1.0},
        "pipeline_status_counts": {"success": successes, "failed": total - successes},
        "stage_funnel": {"opening_match": {"success": successes}},
    }
    if companies_sha256 is not None:
        summary["evaluation_manifest"] = {"companies_sha256": companies_sha256}
    return summary


class EvaluationHistoryTests(unittest.TestCase):
    def test_archives_content_addressed_summary_and_updates_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            history = EvaluationHistory(directory)
            first = history.archive(_summary(0.7, 7), label="before")
            second = history.archive(_summary(0.9, 9), label="after")

            self.assertEqual(history.latest(), second)
            self.assertEqual(second.baseline_run_id, first.run_id)
            self.assertEqual(second.regression["rates_delta"]["opening"], 0.2)
            self.assertEqual(second.regression["pipeline_status_delta"]["success"], 2)
            self.assertRegex(second.run_id, r"^\d{8}T\d{6}\.\d{6}Z-")
            object_path = Path(directory) / "objects" / second.summary_sha256[:2] / f"{second.summary_sha256}.json"
            self.assertEqual(json.loads(object_path.read_text()), second.summary)
            manifest = json.loads((Path(directory) / "manifest.json").read_text())
            self.assertEqual(manifest["latest_run_id"], second.run_id)
            self.assertEqual(manifest["runs"], [first.run_id, second.run_id])

    def test_run_metadata_is_validated_and_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            history = EvaluationHistory(directory)
            run = history.archive(
                _summary(0.7, 7),
                metadata={"commit_sha": "abc123", "adapter_version": "test"},
            )

            self.assertEqual(history.load(run.run_id).metadata["commit_sha"], "abc123")
            with self.assertRaises(ValueError):
                history.archive(_summary(0.8, 8), metadata={"Bad Key": "value"})

    def test_identical_summaries_share_object_but_create_distinct_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            history = EvaluationHistory(directory)
            first = history.archive(_summary(1.0, 10))
            second = history.archive(_summary(1.0, 10))

            self.assertEqual(first.summary_sha256, second.summary_sha256)
            self.assertNotEqual(first.run_id, second.run_id)
            self.assertEqual(len(list((Path(directory) / "objects").rglob("*.json"))), 1)

    def test_archived_run_is_not_changed_by_caller_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = _summary(0.7, 7)
            history = EvaluationHistory(directory)
            run = history.archive(summary)
            summary["rates"]["opening"] = 0.1

            self.assertEqual(run.summary["rates"]["opening"], 0.7)
            self.assertEqual(history.latest().summary["rates"]["opening"], 0.7)

    def test_no_baseline_preserves_summary_and_omits_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            history = EvaluationHistory(directory)
            history.archive(_summary(0.5, 5))
            run = history.archive(_summary(0.4, 4), compare_with_latest=False)

            self.assertIsNone(run.baseline_run_id)
            self.assertIsNone(run.regression)
            self.assertNotIn("regression", run.summary)

    def test_selects_latest_baseline_from_same_cohort(self):
        with tempfile.TemporaryDirectory() as directory:
            history = EvaluationHistory(directory)
            same_cohort = history.archive(_summary(0.5, 5, companies_sha256="a" * 64))
            history.archive(_summary(0.2, 2, companies_sha256="b" * 64))
            current = history.archive(_summary(0.8, 8, companies_sha256="a" * 64))

            self.assertEqual(current.baseline_run_id, same_cohort.run_id)
            self.assertEqual(current.regression["rates_delta"]["opening"], 0.3)
            self.assertEqual(
                current.cohort_identity,
                {"companies_sha256": "a" * 64, "observed_result_total": "10"},
            )

    def test_partial_result_is_not_compatible_with_full_cohort(self):
        digest = "a" * 64
        full = _summary(0.8, 24, companies_sha256=digest, total=30)
        partial = _summary(0.75, 6, companies_sha256=digest, total=8)
        for summary in (full, partial):
            summary["evaluation_manifest"].update(
                {
                    "run_configuration_digest": "b" * 64,
                    "batch_execution_configuration_digest": "c" * 64,
                }
            )

        full_identity = derive_cohort_identity(full)
        partial_identity = derive_cohort_identity(partial)

        self.assertEqual(full_identity["observed_result_total"], "30")
        self.assertEqual(partial_identity["observed_result_total"], "8")
        self.assertFalse(cohort_identities_compatible(full_identity, partial_identity))

        with tempfile.TemporaryDirectory() as directory:
            history = EvaluationHistory(directory)
            history.archive(full)
            current = history.archive(partial)

            self.assertIsNone(current.baseline_run_id)
            self.assertEqual(
                current.regression, {"comparison_status": "no_compatible_baseline"}
            )

    def test_complete_matching_cohorts_remain_compatible_across_adapter_versions(self):
        digest = "a" * 64
        left = derive_cohort_identity(
            _summary(0.8, 24, companies_sha256=digest, total=30),
            metadata={"adapter_version": "old"},
        )
        right = derive_cohort_identity(
            _summary(0.9, 27, companies_sha256=digest, total=30),
            metadata={"adapter_version": "new"},
        )

        self.assertNotIn("adapter_version", left)
        self.assertNotIn("adapter_version", right)
        self.assertTrue(cohort_identities_compatible(left, right))

    def test_invalid_or_missing_total_fails_closed(self):
        full = derive_cohort_identity(
            _summary(0.8, 24, companies_sha256="a" * 64, total=30)
        )
        for invalid_total in (None, -1, 30.0, "30", True):
            with self.subTest(total=invalid_total):
                summary = _summary(0.8, 8, companies_sha256="a" * 64)
                if invalid_total is None:
                    summary.pop("total")
                else:
                    summary["total"] = invalid_total

                identity = derive_cohort_identity(summary)

                self.assertNotIn("observed_result_total", identity)
                self.assertFalse(cohort_identities_compatible(identity, full))

        legacy_identity = {"companies_sha256": "a" * 64}
        self.assertFalse(
            cohort_identities_compatible(legacy_identity, legacy_identity.copy())
        )
        for corrupt_total in ("-1", "30.0", "thirty", "03"):
            with self.subTest(identity_total=corrupt_total):
                corrupt_identity = {
                    "companies_sha256": "a" * 64,
                    "observed_result_total": corrupt_total,
                }
                self.assertFalse(
                    cohort_identities_compatible(corrupt_identity, corrupt_identity.copy())
                )

    def test_total_without_primary_cohort_identity_fails_closed(self):
        identity = derive_cohort_identity(_summary(0.8, 8))

        self.assertIsNone(identity)
        total_only_identity = {"observed_result_total": "10"}
        self.assertFalse(
            cohort_identities_compatible(
                total_only_identity,
                total_only_identity.copy(),
            )
        )

    def test_different_cohort_has_explicit_no_compatible_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            history = EvaluationHistory(directory)
            history.archive(_summary(0.5, 5, companies_sha256="a" * 64))
            current = history.archive(_summary(0.9, 9, companies_sha256="b" * 64))

            self.assertIsNone(current.baseline_run_id)
            self.assertEqual(current.regression, {"comparison_status": "no_compatible_baseline"})
            self.assertNotIn("rates_delta", current.regression)

    def test_legacy_rows_match_only_other_identity_less_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            history = EvaluationHistory(directory)
            legacy = history.archive(_summary(0.5, 5))
            identified = history.archive(_summary(0.8, 8, companies_sha256="a" * 64))
            second_legacy = history.archive(_summary(0.6, 6))

            self.assertIsNone(identified.baseline_run_id)
            self.assertEqual(identified.regression, {"comparison_status": "no_compatible_baseline"})
            self.assertEqual(second_legacy.baseline_run_id, legacy.run_id)
            self.assertEqual(second_legacy.regression["rates_delta"]["opening"], 0.1)

    def test_expectations_identity_is_part_of_cohort_when_available(self):
        with tempfile.TemporaryDirectory() as directory:
            first_summary = _summary(0.5, 5, companies_sha256="a" * 64)
            first_summary["evaluation_manifest"]["expectations_sha256"] = "b" * 64
            second_summary = _summary(0.9, 9, companies_sha256="a" * 64)
            second_summary["evaluation_manifest"]["expectations_sha256"] = "c" * 64
            history = EvaluationHistory(directory)
            history.archive(first_summary)
            current = history.archive(second_summary)

            self.assertIsNone(current.baseline_run_id)
            self.assertEqual(current.regression["comparison_status"], "no_compatible_baseline")

    def test_run_configuration_digest_is_part_of_baseline_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            first_summary = _summary(0.5, 5, companies_sha256="a" * 64)
            first_summary["evaluation_manifest"]["run_configuration_digest"] = "b" * 64
            second_summary = _summary(0.9, 9, companies_sha256="a" * 64)
            second_summary["evaluation_manifest"]["run_configuration_digest"] = "c" * 64
            history = EvaluationHistory(directory)
            history.archive(first_summary)

            current = history.archive(second_summary)

            self.assertEqual(
                current.cohort_identity,
                {
                    "companies_sha256": "a" * 64,
                    "run_configuration_digest": "c" * 64,
                    "observed_result_total": "10",
                },
            )
            self.assertIsNone(current.baseline_run_id)
            self.assertEqual(current.regression, {"comparison_status": "no_compatible_baseline"})

    def test_batch_execution_digest_is_part_of_baseline_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            first_summary = _summary(0.5, 5, companies_sha256="a" * 64)
            first_summary["evaluation_manifest"]["batch_execution_configuration_digest"] = "b" * 64
            second_summary = _summary(0.9, 9, companies_sha256="a" * 64)
            second_summary["evaluation_manifest"]["batch_execution_configuration_digest"] = "c" * 64
            history = EvaluationHistory(directory)
            history.archive(first_summary)

            current = history.archive(second_summary)

            self.assertIsNone(current.baseline_run_id)
            self.assertEqual(current.regression, {"comparison_status": "no_compatible_baseline"})

    def test_manifest_input_identity_precedes_metadata_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = _summary(0.5, 5)
            summary["summary_manifest"] = {"input_identity": "manifest-input"}
            run = EvaluationHistory(directory).archive(
                summary,
                metadata={"cohort_input_sha256": "fallback-input"},
            )

            self.assertEqual(
                run.cohort_identity,
                {"input_identity": "manifest-input", "observed_result_total": "10"},
            )

    def test_equivalent_companies_and_input_identity_sources_match(self):
        with tempfile.TemporaryDirectory() as directory:
            digest = "a" * 64
            history = EvaluationHistory(directory)
            first = history.archive(_summary(0.5, 5, companies_sha256=digest))
            current_summary = _summary(0.7, 7)
            current_summary["summary_manifest"] = {"input_sha256": digest}
            current = history.archive(current_summary)

            self.assertEqual(current.baseline_run_id, first.run_id)
            self.assertEqual(current.regression["rates_delta"]["opening"], 0.2)

    def test_rejects_non_json_and_non_finite_summary_values(self):
        with tempfile.TemporaryDirectory() as directory:
            history = EvaluationHistory(directory)
            with self.assertRaises(ValueError):
                history.archive([])  # type: ignore[arg-type]
            with self.assertRaises(ValueError):
                history.archive({"rate": float("nan")})

    def test_corrupt_object_is_strict_by_default_and_explicitly_skippable(self):
        with tempfile.TemporaryDirectory() as directory:
            history = EvaluationHistory(directory)
            first = history.archive(_summary(0.7, 7))
            second = history.archive(_summary(0.8, 8))
            object_path = Path(directory) / "objects" / first.summary_sha256[:2] / f"{first.summary_sha256}.json"
            object_path.write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(CorruptEvaluationHistoryError, "content verification"):
                history.scan()
            scan = history.scan(on_corrupt="skip")
            self.assertEqual([run.run_id for run in scan.runs], [second.run_id])
            self.assertEqual(scan.skipped[0]["run_id"], first.run_id)
            self.assertIn("content verification", scan.skipped[0]["error"])

    def test_corrupt_manifest_never_silently_resets_history(self):
        with tempfile.TemporaryDirectory() as directory:
            history = EvaluationHistory(directory)
            history.archive(_summary(0.7, 7))
            (Path(directory) / "manifest.json").write_text("{", encoding="utf-8")

            with self.assertRaisesRegex(CorruptEvaluationHistoryError, "manifest"):
                history.latest()
            with self.assertRaises(CorruptEvaluationHistoryError):
                history.archive(_summary(0.8, 8))

    def test_rejects_traversal_run_ids_and_managed_symlinks(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            history = EvaluationHistory(directory)
            with self.assertRaises(ValueError):
                history.load("../../outside")
            os.symlink(outside, Path(directory) / "runs")
            with self.assertRaisesRegex(EvaluationHistoryError, "symlink"):
                history.archive(_summary(0.7, 7))

    def test_atomic_failure_keeps_previous_manifest_and_cleans_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            history = EvaluationHistory(directory)
            first = history.archive(_summary(0.7, 7))
            real_replace = os.replace

            def fail_manifest(source, destination):
                if Path(destination).name == "manifest.json":
                    raise OSError("injected")
                return real_replace(source, destination)

            with mock.patch("job_source_agent.evaluation_history.os.replace", side_effect=fail_manifest):
                with self.assertRaises(OSError):
                    history.archive(_summary(0.8, 8))

            self.assertEqual(history.latest().run_id, first.run_id)
            self.assertEqual(list(Path(directory).rglob("*.tmp")), [])

    def test_cli_archives_existing_summary_without_changing_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary_path = root / "summary.json"
            summary = _summary(0.9, 9)
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "archive_evaluation.py"),
                    "--summary", str(summary_path),
                    "--history-dir", str(root / "history"),
                    "--label", "live-46",
                    "--commit-sha", "deadbeef",
                    "--benchmark-command", "python3 scripts/live_batch_eval.py --input fixed.json",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )

            output = json.loads(completed.stdout)
            self.assertEqual(output["summary"], summary)
            self.assertEqual(output["label"], "live-46")
            self.assertIsNone(output["regression"])
            self.assertEqual(output["metadata"]["commit_sha"], "deadbeef")
            self.assertEqual(output["metadata"]["adapter_version"], __import__("job_source_agent.checkpoint", fromlist=["ADAPTER_VERSION"]).ADAPTER_VERSION)
            self.assertIn("live_batch_eval.py", output["metadata"]["benchmark_command"])
            self.assertIsNone(output["cohort_identity"])

    def test_cli_derives_identity_from_canonical_input_and_expectations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary_path = root / "summary.json"
            input_path = root / "input.json"
            expectations_path = root / "expectations.json"
            summary_path.write_text(json.dumps(_summary(0.9, 9)), encoding="utf-8")
            input_path.write_text('[{"name": "Example"}]', encoding="utf-8")
            expectations_path.write_text('{"Example": {"opening": true}}', encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "archive_evaluation.py"),
                    "--summary", str(summary_path),
                    "--history-dir", str(root / "history"),
                    "--input", str(input_path),
                    "--expectations", str(expectations_path),
                    "--commit-sha", "deadbeef",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )

            output = json.loads(completed.stdout)
            self.assertEqual(
                output["cohort_identity"],
                {
                    "input_identity": output["metadata"]["cohort_input_sha256"],
                    "expectations_identity": output["metadata"]["cohort_expectations_sha256"],
                    "observed_result_total": "10",
                },
            )


if __name__ == "__main__":
    unittest.main()
