import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from job_source_agent.snapshot import SnapshotStore
from job_source_agent.web import Page
from scripts.replay_failure_bundle import FailureReplayError, replay_failure_bundle


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

    def test_builds_bundle_and_executes_selected_failure_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)

            manifest = replay_failure_bundle(self._args(root))
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest["summary"]["total"], 1)
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["summary"]["checkpoint_action_counts"]["save"], 7)
        self.assertEqual(replay_results[0]["open_position_url"], "https://jobs.example.test/jobs/123-data-analyst")
        self.assertNotIn(str(root), json.dumps(manifest))
        self.assertEqual(manifest["paths"]["fixtures"], "offline/sites")

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


if __name__ == "__main__":
    unittest.main()
