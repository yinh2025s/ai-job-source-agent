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

    def test_replay_materializes_redirect_request_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            SnapshotStore(snapshots).write_page(
                Page(
                    url="https://jobs.example.com/careers",
                    final_url="https://jobs.example.com/candidate/?token=secret",
                    html='<input id="token" value="secret">',
                    source="live",
                ),
                request_url="https://jobs.example.com/careers",
            )

            result = replay_snapshots(snapshots, output)
            request_page = Fetcher(fixtures_dir=output / "sites", offline=True).fetch(
                "https://jobs.example.com/careers"
            )
            final_page = Fetcher(fixtures_dir=output / "sites", offline=True).fetch(
                "https://jobs.example.com/candidate/"
            )

        self.assertEqual(request_page.html, '<input id="token" value="[REDACTED]">')
        self.assertEqual(final_page.html, request_page.html)
        self.assertEqual(result.summary["fixture_count"], 2)

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
        self.assertEqual(first.summary["superseded_records"], 0)
        self.assertEqual(first.summary["fixture_count"], 1)

    def test_changed_snapshot_supersedes_prior_body_and_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            store = SnapshotStore(snapshots)
            first = store.write_page(
                Page(
                    url="https://jobs.example.com/search",
                    final_url="https://jobs.example.com/search",
                    html="<html>first</html>",
                    source="live",
                    artifacts={"screenshot_png": b"first-image"},
                )
            )
            second = store.write_page(
                Page(
                    url="https://jobs.example.com/search?q=data",
                    final_url="https://jobs.example.com/search?q=data",
                    html="<html>second</html>",
                    source="live",
                    artifacts={"screenshot_png": b"second-image"},
                )
            )

            result = replay_snapshots(snapshots, root / "replay")
            page = Fetcher(fixtures_dir=root / "replay" / "sites", offline=True).fetch(
                "https://jobs.example.com/search"
            )
            replay_artifact = (
                root / "replay" / result.manifest["artifacts"][0]["replay_path"]
            ).read_bytes()

        self.assertNotEqual(first.blob_path, second.blob_path)
        self.assertEqual(page.html, "<html>second</html>")
        self.assertEqual(replay_artifact, b"second-image")
        self.assertEqual(result.summary["duplicate_records"], 0)
        self.assertEqual(result.summary["superseded_records"], 1)

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

    def test_skips_single_incomplete_final_physical_line(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            self._write_snapshot(snapshots)
            index = snapshots / "snapshots.jsonl"
            with index.open("a", encoding="utf-8") as handle:
                handle.write('{"request_url": "https://interrupted.example.com')

            result = replay_snapshots(snapshots, root / "replay")

        self.assertEqual(result.summary["snapshot_records"], 1)
        self.assertEqual(result.summary["skipped_records"], 1)
        self.assertEqual(result.summary["corrupt_tail_records"], 1)

    def test_rejects_incomplete_json_before_final_physical_line(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            self._write_snapshot(snapshots)
            index = snapshots / "snapshots.jsonl"
            valid_record = index.read_text(encoding="utf-8")
            index.write_text('{"request_url":\n' + valid_record, encoding="utf-8")

            with self.assertRaisesRegex(SnapshotReplayError, "Line 1: invalid JSON"):
                replay_snapshots(snapshots, root / "replay")

    def test_rejects_complete_but_invalid_final_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            self._write_snapshot(snapshots)
            index = snapshots / "snapshots.jsonl"
            with index.open("a", encoding="utf-8") as handle:
                handle.write('{"request_url": invalid}')

            with self.assertRaisesRegex(SnapshotReplayError, "Line 2: invalid JSON"):
                replay_snapshots(snapshots, root / "replay")

    def test_rejects_invalid_final_record_terminated_by_newline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            self._write_snapshot(snapshots)
            index = snapshots / "snapshots.jsonl"
            with index.open("a", encoding="utf-8") as handle:
                handle.write('{"request_url":\n')

            with self.assertRaisesRegex(SnapshotReplayError, "Line 2: invalid JSON"):
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
