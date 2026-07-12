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
        self.assertEqual(manifest["summary"]["checkpoint_action_counts"]["save"], 7)
        self.assertEqual(replay_results[0]["open_position_url"], "https://jobs.example.test/jobs/123-data-analyst")
        self.assertNotIn(str(root), json.dumps(manifest))
        self.assertEqual(manifest["paths"]["fixtures"], "offline/sites")

    def test_rejects_empty_filter_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)

            with self.assertRaisesRegex(FailureReplayError, "No replayable records"):
                replay_failure_bundle(
                    self._args(root, reason_code=["NETWORK_TIMEOUT"])
                )


if __name__ == "__main__":
    unittest.main()
