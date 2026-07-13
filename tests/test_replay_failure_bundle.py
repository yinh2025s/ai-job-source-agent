import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from job_source_agent.snapshot import SnapshotStore
from job_source_agent.web import Page
from scripts.replay_failure_bundle import (
    FailureReplayError,
    _build_outcome_gate,
    main,
    replay_failure_bundle,
)


class FailureReplayBundleTests(unittest.TestCase):
    def _args(self, root: Path, **overrides):
        values = {
            "results": str(root / "results.json"),
            "snapshot_dir": str(root / "snapshots"),
            "output_dir": str(root / "bundle"),
            "pipeline_status": ["partial"],
            "stage": "opening_match",
            "stage_status": ["partial"],
            "reason_code": ["OPENING_NOT_FOUND"],
            "provider": None,
            "limit": None,
            "include_missing_website": False,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _write_inputs(self, root: Path):
        board_url = "https://jobs.example.test/jobs"
        results = [
            {
                "company_name": "Example Data",
                "company_website_url": "https://example.test",
                "career_root_url": board_url,
                "linkedin_job_title": "Data Analyst",
                "pipeline_status": "partial",
                "stages": [
                    {
                        "stage": "opening_match",
                        "status": "partial",
                        "reason_code": "OPENING_NOT_FOUND",
                    }
                ],
            }
        ]
        (root / "results.json").write_text(json.dumps(results), encoding="utf-8")
        SnapshotStore(root / "snapshots").write_page(
            Page(
                url=board_url,
                final_url=board_url,
                html=(
                    '<html><body><a href="/jobs/123-data-analyst">'
                    "Data Analyst</a></body></html>"
                ),
                source="live",
            ),
            request_url=board_url,
        )

    def test_reproduced_failure_passes_outcome_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            results_path = root / "results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results[0]["linkedin_job_title"] = "Missing Role"
            results_path.write_text(json.dumps(results), encoding="utf-8")

            manifest = replay_failure_bundle(self._args(root))
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest["summary"]["total"], 1)
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["summary"]["checkpoint_action_counts"]["save"], 7)
        self.assertIsNone(replay_results[0]["open_position_url"])
        self.assertNotIn(str(root), json.dumps(manifest))
        self.assertEqual(manifest["paths"]["fixtures"], "offline/sites")
        self.assertEqual(manifest["outcome_gate"]["status"], "passed")
        self.assertEqual(
            manifest["outcome_gate"]["classification_counts"],
            {
                "reproduced": 1,
                "expected_transition": 0,
                "fixture_gap": 0,
                "mismatch": 0,
            },
        )
        comparison = manifest["outcome_gate"]["records"][0]
        self.assertEqual(comparison["classification"], "reproduced")
        self.assertEqual(comparison["original_outcome"], comparison["replay_outcome"])

    def test_reusing_bundle_output_removes_stale_checkpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            results_path = root / "results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results[0]["linkedin_job_title"] = "Missing Role"
            results_path.write_text(json.dumps(results), encoding="utf-8")
            args = self._args(root)
            replay_failure_bundle(args)
            stale = root / "bundle" / "checkpoints" / "stale.txt"
            stale.write_text("stale", encoding="utf-8")

            replay_failure_bundle(args)

            self.assertFalse(stale.exists())

    def test_improved_replay_is_mismatch_and_cli_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            args = self._args(root)

            manifest = replay_failure_bundle(args)
            written = json.loads(
                (root / "bundle" / "bundle-manifest.json").read_text(encoding="utf-8")
            )
            cli_args = [
                "--results", args.results,
                "--snapshot-dir", args.snapshot_dir,
                "--output-dir", str(root / "cli-bundle"),
                "--pipeline-status", "partial",
                "--stage", "opening_match",
                "--stage-status", "partial",
                "--reason-code", "OPENING_NOT_FOUND",
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "1 outcome mismatch"):
                    main(cli_args)

        self.assertEqual(manifest, written)
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["outcome_gate"]["status"], "failed")
        comparison = manifest["outcome_gate"]["records"][0]
        self.assertEqual(comparison["classification"], "mismatch")
        self.assertEqual(comparison["reason"], "outcome_changed")
        self.assertEqual(comparison["replay_outcome"]["pipeline_status"], "success")

    def test_offline_fixture_failure_is_classified_as_fixture_gap(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "job_title": "Data Analyst",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "linkedin_job_title": "Data Analyst",
            "pipeline_status": "failed",
            "stages": [{
                "stage": "opening_match",
                "status": "failed",
                "reason_code": "OFFLINE_FIXTURE_MISSING",
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "incomplete")
        self.assertEqual(
            gate["classification_counts"],
            {
                "reproduced": 0,
                "expected_transition": 0,
                "fixture_gap": 1,
                "mismatch": 0,
            },
        )
        self.assertEqual(gate["records"][0]["classification"], "fixture_gap")
        self.assertEqual(gate["records"][0]["reason"], "offline_fixture_missing")

    def test_cli_exits_nonzero_for_fixture_gap(self):
        manifest = {
            "summary": {"total": 1},
            "outcome_gate": {
                "status": "incomplete",
                "classification_counts": {"mismatch": 0, "fixture_gap": 1},
            },
        }
        cli_args = [
            "--results", "results.json",
            "--snapshot-dir", "snapshots",
            "--output-dir", "bundle",
        ]
        with patch(
            "scripts.replay_failure_bundle.replay_failure_bundle",
            return_value=manifest,
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "1 fixture gap"):
                    main(cli_args)

    def test_explicit_expected_transition_is_the_only_allowed_outcome_change(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                "expected_transition": {
                    "pipeline_status": "success",
                    "failure_stage": {
                        "stage": "opening_match",
                        "status": "success",
                        "reason_code": None,
                    },
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "pipeline_status": "success",
            "stages": [{
                "stage": "opening_match",
                "status": "success",
                "reason_code": None,
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["expected_transition"], 1)
        self.assertEqual(gate["records"][0]["classification"], "expected_transition")

    def test_expected_transition_can_move_to_a_different_failure_stage(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                "expected_transition": {
                    "pipeline_status": "failed",
                    "failure_stage": {
                        "stage": "career_discovery",
                        "status": "failed",
                        "reason_code": "CAREER_PAGE_NOT_FOUND",
                    },
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "pipeline_status": "failed",
            "stages": [{
                "stage": "career_discovery",
                "status": "failed",
                "reason_code": "CAREER_PAGE_NOT_FOUND",
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["expected_transition"], 1)

    def test_expected_transition_can_remove_the_failure_stage(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                "expected_transition": {
                    "pipeline_status": "success",
                    "failure_stage": None,
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "pipeline_status": "success",
            "stages": [{
                "stage": "opening_match",
                "status": "success",
                "reason_code": None,
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["records"][0]["replay_outcome"]["failure_stage"], None)

    def test_fixture_gap_cannot_be_declared_as_an_expected_transition(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                "expected_transition": {
                    "pipeline_status": "partial",
                    "failure_stage": {
                        "stage": "opening_match",
                        "status": "partial",
                        "reason_code": "OFFLINE_FIXTURE_MISSING",
                    },
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "pipeline_status": "partial",
            "stages": [{
                "stage": "opening_match",
                "status": "partial",
                "reason_code": "OFFLINE_FIXTURE_MISSING",
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "incomplete")
        self.assertEqual(gate["records"][0]["classification"], "fixture_gap")

    def test_replay_preserves_linkedin_native_only_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            career_url = "https://native.example/careers"
            job_url = "https://www.linkedin.com/jobs/view/808"
            results = [{
                "company_name": "Native Apply",
                "company_website_url": "https://native.example",
                "career_root_url": career_url,
                "linkedin_job_url": job_url,
                "linkedin_job_title": "AI Engineer",
                "pipeline_status": "partial",
                "stages": [{
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                }],
                "trace": {"source_trace": {"linkedin_posting": {
                    "availability": "active",
                    "apply_mode": "linkedin_native",
                    "evidence_source": "authenticated_detail_dom",
                    "job_url": job_url,
                    "observed_at": "2026-07-14T00:00:00Z",
                }}},
            }]
            (root / "results.json").write_text(json.dumps(results), encoding="utf-8")
            SnapshotStore(root / "snapshots").write_page(
                Page(
                    url=career_url,
                    final_url=career_url,
                    html="<html><body><h1>Careers</h1><p>No public board.</p></body></html>",
                    source="live",
                ),
                request_url=career_url,
            )

            replay_failure_bundle(self._args(root))
            replay_input = json.loads(
                (root / "bundle" / "replay-input.json").read_text(encoding="utf-8")
            )
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        job_board_stage = next(
            stage for stage in replay_results[0]["stages"]
            if stage["stage"] == "job_board_discovery"
        )
        self.assertEqual(job_board_stage["reason_code"], "LINKEDIN_NATIVE_ONLY")
        self.assertEqual(
            replay_input[0]["source_trace"]["linkedin_posting"]["apply_mode"],
            "linkedin_native",
        )
        self.assertNotIn("observed_at", replay_input[0]["source_trace"]["linkedin_posting"])

    def test_replay_preserves_explicitly_closed_posting_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            results_path = root / "results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results[0]["linkedin_job_title"] = "Missing Role"
            results[0]["trace"] = {
                "source_trace": {
                    "linkedin_posting": {
                        "availability": "closed",
                        "apply_mode": "unknown",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": "https://www.linkedin.com/jobs/view/909",
                    }
                }
            }
            results_path.write_text(json.dumps(results), encoding="utf-8")

            replay_failure_bundle(self._args(root))
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        opening_stage = next(
            stage for stage in replay_results[0]["stages"]
            if stage["stage"] == "opening_match"
        )
        self.assertEqual(opening_stage["reason_code"], "OPENING_CLOSED")
        self.assertEqual(
            opening_stage["evidence"][0]["source_posting_status"],
            "closed",
        )

    def test_rejects_empty_filter_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)

            with self.assertRaisesRegex(FailureReplayError, "No replayable records"):
                replay_failure_bundle(
                    self._args(root, reason_code=["NETWORK_TIMEOUT"])
                )

    def test_allow_empty_writes_skipped_manifest_without_requiring_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            args = self._args(root, reason_code=["NETWORK_TIMEOUT"])

            manifest = replay_failure_bundle(args, allow_empty=True)
            written = json.loads(
                (root / "bundle" / "bundle-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest, written)
        self.assertEqual(manifest["status"], "skipped")
        self.assertEqual(manifest["reason"], "no_replayable_failure_records")
        self.assertEqual(manifest["summary"], {"total": 0})
        self.assertEqual(manifest["outcome_gate"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
