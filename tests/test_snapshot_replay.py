import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from job_source_agent.snapshot import SnapshotStore
from job_source_agent.snapshot_replay import SnapshotReplayError, replay_snapshots
from job_source_agent.web import FetchError, Fetcher, Page, fixture_path_candidates


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
        self.assertEqual(request_page.url, "https://jobs.example.com/careers")
        self.assertEqual(
            request_page.final_url,
            "https://jobs.example.com/candidate/?token=%5BREDACTED%5D",
        )
        self.assertEqual(final_page.html, request_page.html)
        self.assertEqual(result.summary["fixture_count"], 2)

    def test_cross_host_redirect_preserves_verified_response_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            request_url = "https://www.airbnb.example/careers"
            final_url = "https://careers.airbnb.example/"
            SnapshotStore(snapshots).write_page(
                Page(
                    url=request_url,
                    final_url=final_url,
                    html="<html>Airbnb careers</html>",
                    source="live",
                ),
                request_url=request_url,
            )

            replay_snapshots(snapshots, output)
            page = Fetcher(fixtures_dir=output / "sites", offline=True).fetch(request_url)

        self.assertEqual(page.url, request_url)
        self.assertEqual(page.final_url, final_url)
        self.assertEqual(page.html, "<html>Airbnb careers</html>")

    def test_legacy_get_and_post_fixtures_remain_compatible_without_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            fixtures = Path(directory) / "sites"
            url = "https://jobs.example.com/api"
            legacy_path = fixture_path_candidates(fixtures, url)[-1]
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text("legacy response", encoding="utf-8")
            fetcher = Fetcher(fixtures_dir=fixtures, offline=True)

            get_page = fetcher.fetch(url)
            post_page = fetcher.fetch(
                url,
                data=b'{"page": 2}',
                headers={"Content-Type": "application/json"},
            )

        self.assertEqual(get_page.html, "legacy response")
        self.assertEqual(post_page.html, "legacy response")
        self.assertEqual(post_page.final_url, url)

    def test_fixture_fetcher_rejects_unsafe_replay_response_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            self._write_snapshot(snapshots)
            replay_snapshots(snapshots, output)
            manifest_path = output / "replay-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["entries"][0]["final_url"] = "file:///private/secret"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaises(FetchError) as raised:
                Fetcher(fixtures_dir=output / "sites", offline=True).fetch(
                    "https://jobs.example.com/search"
                )

        self.assertEqual(raised.exception.reason_code, "OFFLINE_FIXTURE_MISSING")
        self.assertIn("Invalid offline replay manifest", str(raised.exception))

    def test_fixture_fetcher_rejects_manifest_body_metadata_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            self._write_snapshot(snapshots)
            replay_snapshots(snapshots, output)
            manifest_path = output / "replay-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest["entries"]:
                entry["byte_count"] += 1
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaises(FetchError):
                Fetcher(fixtures_dir=output / "sites", offline=True).fetch(
                    "https://jobs.example.com/search?token=secret"
                )

    def test_redirect_alias_keeps_its_blob_when_final_url_is_recaptured(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            store = SnapshotStore(snapshots)
            store.write_page(
                Page(
                    url="https://example.com",
                    final_url="https://www.example.com/",
                    html="<html>redirect response</html>",
                    source="live",
                ),
                request_url="https://example.com",
            )
            store.write_page(
                Page(
                    url="https://www.example.com/",
                    final_url="https://www.example.com/",
                    html="<html>later canonical response</html>",
                    source="live",
                )
            )

            replay_snapshots(snapshots, root / "replay")
            fetcher = Fetcher(fixtures_dir=root / "replay" / "sites", offline=True)
            alias_page = fetcher.fetch("https://example.com")
            canonical_page = fetcher.fetch("https://www.example.com/")

        self.assertEqual(alias_page.html, "<html>redirect response</html>")
        self.assertEqual(canonical_page.html, "<html>later canonical response</html>")

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

    def test_reusing_output_removes_fixtures_absent_from_new_snapshot_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_snapshots = root / "first-snapshots"
            second_snapshots = root / "second-snapshots"
            output = root / "replay"
            self._write_snapshot(first_snapshots)
            replay_snapshots(first_snapshots, output)
            SnapshotStore(second_snapshots).write_page(
                Page(
                    url="https://other.example.com/jobs",
                    final_url="https://other.example.com/jobs",
                    html="<html>other</html>",
                    source="live",
                )
            )

            replay_snapshots(second_snapshots, output)
            fetcher = Fetcher(fixtures_dir=output / "sites", offline=True)

            with self.assertRaises(FetchError):
                fetcher.fetch("https://jobs.example.com/search")
            self.assertEqual(
                fetcher.fetch("https://other.example.com/jobs").html,
                "<html>other</html>",
            )

    def test_query_variants_replay_as_distinct_pages_and_artifacts(self):
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
            first_page = Fetcher(fixtures_dir=root / "replay" / "sites", offline=True).fetch(
                "https://jobs.example.com/search"
            )
            second_page = Fetcher(fixtures_dir=root / "replay" / "sites", offline=True).fetch(
                "https://jobs.example.com/search?q=data"
            )
            replay_artifacts = {
                (root / "replay" / artifact["replay_path"]).read_bytes()
                for artifact in result.manifest["artifacts"]
            }

        self.assertNotEqual(first.blob_path, second.blob_path)
        self.assertEqual(first_page.html, "<html>first</html>")
        self.assertEqual(second_page.html, "<html>second</html>")
        self.assertEqual(replay_artifacts, {b"first-image", b"second-image"})
        self.assertEqual(result.summary["duplicate_records"], 0)
        self.assertEqual(result.summary["superseded_records"], 0)

    def test_post_pagination_replays_each_request_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            store = SnapshotStore(snapshots)
            headers = {"Content-Type": "application/json"}
            for page_range, body in ((0, "first"), (10, "second"), (20, "third")):
                data = json.dumps({"range": page_range}).encode("utf-8")
                store.write_page(
                    Page(url="https://jobs.example.com/api", html=body, source="live"),
                    data=data,
                    headers=headers,
                )

            result = replay_snapshots(snapshots, root / "replay")
            fetcher = Fetcher(fixtures_dir=root / "replay" / "sites", offline=True)
            pages = [
                fetcher.fetch(
                    "https://jobs.example.com/api",
                    data=json.dumps({"range": page_range}).encode("utf-8"),
                    headers=headers,
                ).html
                for page_range in (0, 10, 20)
            ]

        self.assertEqual(pages, ["first", "second", "third"])
        self.assertEqual(result.summary["fixture_count"], 3)

    def test_terminal_failure_replays_over_earlier_success_for_same_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            store = SnapshotStore(snapshots)
            url = "https://jobs.example.com/"
            store.write_page(Page(url=url, html="earlier success", source="live"))
            store.write_failure(
                FetchError(
                    "HTTP Error 403: Forbidden",
                    status=403,
                    reason_code="HTTP_FORBIDDEN",
                    retryable=False,
                ),
                url,
            )

            result = replay_snapshots(snapshots, root / "replay")
            fetcher = Fetcher(fixtures_dir=root / "replay" / "sites", offline=True)

            with self.assertRaises(FetchError) as raised:
                fetcher.fetch(url)

        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.reason_code, "HTTP_FORBIDDEN")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(result.summary["replayable_failures"], 1)

    def test_failure_only_snapshot_root_replays_without_page_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            url = "https://jobs.example.com/api"
            SnapshotStore(snapshots).write_failure(
                FetchError(
                    "HTTP Error 403: Forbidden",
                    status=403,
                    reason_code="HTTP_FORBIDDEN",
                    retryable=False,
                ),
                url,
            )
            self.assertFalse((snapshots / "snapshots.jsonl").exists())

            result = replay_snapshots(snapshots, output)
            fetcher = Fetcher(fixtures_dir=output / "sites", offline=True)

            with self.assertRaises(FetchError) as raised:
                fetcher.fetch(url)
            sites_materialized = (output / "sites").is_dir()

        self.assertEqual(result.summary["snapshot_records"], 0)
        self.assertEqual(result.summary["fixture_count"], 0)
        self.assertEqual(result.summary["replayable_failures"], 1)
        self.assertEqual(result.manifest["entries"], [])
        self.assertTrue(sites_materialized)
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.reason_code, "HTTP_FORBIDDEN")

    def test_replay_rejects_root_without_page_or_failure_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            snapshots.mkdir()

            with self.assertRaisesRegex(SnapshotReplayError, "both missing"):
                replay_snapshots(snapshots, root / "replay")

    def test_rejects_unknown_snapshot_schema_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            self._write_snapshot(snapshots)
            index = snapshots / "snapshots.jsonl"
            record = json.loads(index.read_text(encoding="utf-8"))
            record["schema_version"] = 99
            index.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(SnapshotReplayError, "unsupported snapshot schema"):
                replay_snapshots(snapshots, root / "replay")

    def test_rejects_request_identity_that_does_not_match_request_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            self._write_snapshot(snapshots)
            index = snapshots / "snapshots.jsonl"
            record = json.loads(index.read_text(encoding="utf-8"))
            record["request_url"] = "https://other.example.test/jobs"
            index.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(SnapshotReplayError, "request identity does not match"):
                replay_snapshots(snapshots, root / "replay")

    def test_fixture_fetcher_fails_closed_on_unknown_failure_manifest_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            self._write_snapshot(snapshots)
            replay_snapshots(snapshots, root / "replay")
            failure_manifest = root / "replay" / "fetch-failures.json"
            failure_manifest.write_text(
                json.dumps({"schema_version": 99, "entries": []}),
                encoding="utf-8",
            )
            fetcher = Fetcher(fixtures_dir=root / "replay" / "sites", offline=True)

            with self.assertRaises(FetchError) as raised:
                fetcher.fetch("https://jobs.example.com/search")

        self.assertEqual(raised.exception.reason_code, "OFFLINE_FIXTURE_MISSING")

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
