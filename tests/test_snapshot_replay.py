import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from job_source_agent.snapshot import SnapshotStore
from job_source_agent.snapshot_replay import SnapshotReplayError, replay_snapshots
from job_source_agent.web import Fetcher, Page


ROOT = Path(__file__).resolve().parents[1]


class SnapshotReplayTests(unittest.TestCase):
    def _write_snapshot(self, root: Path, *, artifacts=None):
        return SnapshotStore(root).write_page(
            Page(
                url="https://jobs.example.com/search?token=secret",
                final_url="https://jobs.example.com/search?token=secret",
                html="<html><body>Jobs access_token=secret</body></html>",
                source="live",
                artifacts=artifacts or {},
            ),
            request_url="https://jobs.example.com/search?token=secret",
        )

    def test_replay_creates_fixture_fetcher_can_consume_and_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            self._write_snapshot(snapshots, artifacts={"screenshot_png": b"fake-png"})

            result = replay_snapshots(snapshots, output)
            page = Fetcher(fixtures_dir=output / "sites", offline=True).fetch(
                "https://jobs.example.com/search"
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            summary = json.loads(result.summary_path.read_text(encoding="utf-8"))

        self.assertEqual(page.html, "<html><body>Jobs access_token=[REDACTED]</body></html>")
        self.assertEqual(manifest, result.manifest)
        self.assertEqual(summary["fixture_count"], 1)
        self.assertEqual(summary["artifact_count"], 1)
        self.assertNotIn("source_path", manifest["artifacts"][0])

    def test_duplicate_identical_records_are_deduplicated_deterministically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            self._write_snapshot(snapshots)
            self._write_snapshot(snapshots)

            first = replay_snapshots(snapshots, root / "first")
            second = replay_snapshots(snapshots, root / "second")

        self.assertEqual(first.manifest, second.manifest)
        self.assertEqual(first.summary, second.summary)
        self.assertEqual(first.summary["duplicate_records"], 1)
        self.assertEqual(first.summary["fixture_count"], 1)

    def test_rejects_directory_traversal_in_snapshot_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            self._write_snapshot(snapshots)
            index = snapshots / "snapshots.jsonl"
            record = json.loads(index.read_text(encoding="utf-8"))
            record["path"] = "sites/../../outside.html"
            index.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(SnapshotReplayError, "unsafe path"):
                replay_snapshots(snapshots, root / "replay")

    def test_rejects_missing_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            record = self._write_snapshot(snapshots, artifacts={"screenshot_png": b"fake-png"})
            (snapshots / record.artifact_paths["screenshot_png"]).unlink()

            with self.assertRaisesRegex(SnapshotReplayError, "missing or unsafe artifact"):
                replay_snapshots(snapshots, root / "replay")

    def test_rejects_hash_mismatch_and_unsanitized_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            record = self._write_snapshot(snapshots)
            body_path = snapshots / record.path
            body_path.write_text("<html>token=plain-secret</html>", encoding="utf-8")

            with self.assertRaisesRegex(SnapshotReplayError, "not fully sanitized"):
                replay_snapshots(snapshots, root / "replay")

    def test_rejects_metadata_path_that_does_not_match_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            self._write_snapshot(snapshots)
            index = snapshots / "snapshots.jsonl"
            record = json.loads(index.read_text(encoding="utf-8"))
            record["sanitized_url"] = "https://other.example.com/jobs"
            record["final_url"] = record["sanitized_url"]
            index.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(SnapshotReplayError, "path does not match sanitized_url"):
                replay_snapshots(snapshots, root / "replay")

    def test_cli_writes_summary_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            self._write_snapshot(snapshots)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "replay_snapshots.py"),
                    "--snapshot-dir",
                    str(snapshots),
                    "--output-dir",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            manifest_exists = (output / "replay-manifest.json").is_file()
            summary_exists = (output / "replay-summary.json").is_file()

        self.assertIn('"status": "success"', completed.stdout)
        self.assertTrue(manifest_exists)
        self.assertTrue(summary_exists)


if __name__ == "__main__":
    unittest.main()
