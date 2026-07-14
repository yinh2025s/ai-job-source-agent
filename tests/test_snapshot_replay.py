import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from job_source_agent.outcome_tape import (
    FetchFailureOutcomeTapeEntry,
    PageOutcomeTapeEntry,
)
from job_source_agent.snapshot import SnapshotStore
from job_source_agent.snapshot_capture import SnapshotCaptureCoordinator
from job_source_agent.snapshot_replay import (
    ScopedSnapshotRequiresBundleV6Error,
    SnapshotReplayError,
    load_scoped_outcome_tapes,
    replay_snapshots,
)
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

    def _capture_scope(self, store, attempt, stage, outcomes):
        coordinator = SnapshotCaptureCoordinator(store)
        coordinator.begin_stage(attempt, "a" * 64, stage)
        for kind, url, value in outcomes:
            capture = coordinator.begin_request()
            if kind == "page":
                record = store.write_page(
                    Page(url=url, html=value, source="live"),
                    request_url=url,
                    capture=capture,
                )
            else:
                record = store.write_failure(
                    FetchError(
                        value,
                        status=403,
                        reason_code="HTTP_FORBIDDEN",
                        retryable=False,
                    ),
                    url,
                    capture=capture,
                )
            coordinator.accept_terminal_record(record)
        return coordinator.finalize()

    def _rewrite_record(self, path, mutate, index=0):
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        mutate(records[index])
        path.write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
            encoding="utf-8",
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

    def test_root_response_alias_with_slash_replays_request_without_slash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            SnapshotStore(snapshots).write_page(
                Page(
                    url="https://jobs.example.com/?token=secret",
                    html="<html>Root jobs</html>",
                    source="live",
                )
            )

            replay_snapshots(snapshots, output)
            page = Fetcher(fixtures_dir=output / "sites", offline=True).fetch(
                "https://jobs.example.com"
            )

        self.assertEqual(page.html, "<html>Root jobs</html>")

    def test_root_response_alias_without_slash_replays_request_with_slash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            SnapshotStore(snapshots).write_page(
                Page(
                    url="https://jobs.example.com?token=secret",
                    html="<html>Root jobs</html>",
                    source="live",
                )
            )

            replay_snapshots(snapshots, output)
            page = Fetcher(fixtures_dir=output / "sites", offline=True).fetch(
                "https://jobs.example.com/"
            )

        self.assertEqual(page.html, "<html>Root jobs</html>")

    def test_redirect_root_response_with_slash_replays_without_slash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            SnapshotStore(snapshots).write_page(
                Page(
                    url="https://jobs.taxbit.example/",
                    final_url="https://jobs.taxbit.example/",
                    html="<html>TaxBit jobs</html>",
                    source="live",
                ),
                request_url="https://www.taxbit.example/careers",
            )

            replay_snapshots(snapshots, output)
            page = Fetcher(fixtures_dir=output / "sites", offline=True).fetch(
                "https://jobs.taxbit.example"
            )

        self.assertEqual(page.html, "<html>TaxBit jobs</html>")
        self.assertEqual(page.final_url, "https://jobs.taxbit.example/")

    def test_redirect_root_response_without_slash_replays_with_slash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            SnapshotStore(snapshots).write_page(
                Page(
                    url="https://jobs.taxbit.example",
                    final_url="https://jobs.taxbit.example",
                    html="<html>TaxBit jobs</html>",
                    source="live",
                ),
                request_url="https://www.taxbit.example/careers",
            )

            replay_snapshots(snapshots, output)
            page = Fetcher(fixtures_dir=output / "sites", offline=True).fetch(
                "https://jobs.taxbit.example/"
            )

        self.assertEqual(page.html, "<html>TaxBit jobs</html>")
        self.assertEqual(page.final_url, "https://jobs.taxbit.example")

    def test_non_root_response_alias_rejects_trailing_slash_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            SnapshotStore(snapshots).write_page(
                Page(
                    url="https://jobs.example.com/jobs/?token=secret",
                    html="<html>Jobs</html>",
                    source="live",
                )
            )

            replay_snapshots(snapshots, output)
            with self.assertRaisesRegex(FetchError, "Invalid offline replay manifest"):
                Fetcher(fixtures_dir=output / "sites", offline=True).fetch(
                    "https://jobs.example.com/jobs"
                )

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

    def test_missing_offline_fixture_has_explicit_replay_failure_metadata(self):
        url = "https://jobs.example.com/missing?token=secret"

        with self.assertRaises(FetchError) as raised:
            Fetcher(offline=True).fetch(url)

        self.assertEqual(raised.exception.reason_code, "OFFLINE_FIXTURE_MISSING")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(
            raised.exception.request_identity["sanitized_url"],
            "https://jobs.example.com/missing?token=%5BREDACTED%5D",
        )

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

    def test_fixture_fetcher_rejects_manifest_missing_selected_request_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            self._write_snapshot(snapshots)
            replay_snapshots(snapshots, output)
            manifest_path = output / "replay-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["entries"] = []
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaises(FetchError) as raised:
                Fetcher(fixtures_dir=output / "sites", offline=True).fetch(
                    "https://jobs.example.com/search?token=secret"
                )

        self.assertEqual(raised.exception.reason_code, "OFFLINE_FIXTURE_MISSING")

    def test_schema_one_snapshot_manifest_preserves_redirect_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            request_url = "https://legacy.example/careers"
            final_url = "https://careers.legacy.example/"
            SnapshotStore(snapshots).write_page(
                Page(
                    url=request_url,
                    final_url=final_url,
                    html="<html>Legacy careers</html>",
                    source="live",
                ),
                request_url=request_url,
            )
            index_path = snapshots / "snapshots.jsonl"
            record = json.loads(index_path.read_text(encoding="utf-8"))
            record["schema_version"] = 1
            for field in ("kind", "sequence", "request"):
                record.pop(field)
            index_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            result = replay_snapshots(snapshots, output)
            page = Fetcher(fixtures_dir=output / "sites", offline=True).fetch(request_url)

        self.assertEqual(result.manifest["entries"][0]["request"], None)
        self.assertEqual(page.url, request_url)
        self.assertEqual(page.final_url, final_url)

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
            request_url = "https://company.example.com/careers"
            final_url = "https://jobs.example.com/"
            store.write_page(
                Page(
                    url=request_url,
                    final_url=final_url,
                    html="earlier success",
                    source="live",
                ),
                request_url=request_url,
            )
            store.write_failure(
                FetchError(
                    "HTTP Error 403: Forbidden",
                    status=403,
                    reason_code="HTTP_FORBIDDEN",
                    retryable=False,
                ),
                request_url,
            )

            result = replay_snapshots(snapshots, root / "replay")
            fetcher = Fetcher(fixtures_dir=root / "replay" / "sites", offline=True)

            with self.assertRaises(FetchError) as raised:
                fetcher.fetch(request_url)
            with self.assertRaises(FetchError) as final_url_raised:
                fetcher.fetch(final_url)
            materialized_pages = list((root / "replay" / "sites").rglob("*.html"))

        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.reason_code, "HTTP_FORBIDDEN")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(final_url_raised.exception.reason_code, "OFFLINE_FIXTURE_MISSING")
        self.assertEqual(result.manifest["entries"], [])
        self.assertEqual(result.summary["fixture_count"], 0)
        self.assertEqual(result.summary["replayable_failures"], 1)
        self.assertEqual(materialized_pages, [])

    def test_later_page_replays_over_earlier_failure_for_same_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            store = SnapshotStore(snapshots)
            url = "https://jobs.example.com/"
            store.write_failure(
                FetchError(
                    "HTTP Error 403: Forbidden",
                    status=403,
                    reason_code="HTTP_FORBIDDEN",
                    retryable=False,
                ),
                url,
            )
            store.write_page(Page(url=url, html="later success", source="live"))

            result = replay_snapshots(snapshots, root / "replay")
            page = Fetcher(fixtures_dir=root / "replay" / "sites", offline=True).fetch(url)

        self.assertEqual(page.html, "later success")
        self.assertEqual(result.manifest["failure_entries"], [])
        self.assertEqual(result.summary["replayable_failures"], 0)

    def test_terminal_outcomes_are_isolated_by_exact_request_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            store = SnapshotStore(snapshots)
            url = "https://jobs.example.com/api"
            headers = {"Content-Type": "application/json"}
            successful_data = b'{"page": 1}'
            failed_data = b'{"page": 2}'
            store.write_page(
                Page(url=url, html="identity one", source="live"),
                data=successful_data,
                headers=headers,
            )
            store.write_page(
                Page(url=url, html="stale identity two", source="live"),
                data=failed_data,
                headers=headers,
            )
            store.write_failure(
                FetchError(
                    "HTTP Error 403: Forbidden",
                    status=403,
                    reason_code="HTTP_FORBIDDEN",
                    retryable=False,
                ),
                url,
                data=failed_data,
                headers=headers,
            )

            result = replay_snapshots(snapshots, root / "replay")
            fetcher = Fetcher(fixtures_dir=root / "replay" / "sites", offline=True)
            page = fetcher.fetch(url, data=successful_data, headers=headers)
            with self.assertRaises(FetchError) as raised:
                fetcher.fetch(url, data=failed_data, headers=headers)

        self.assertEqual(page.html, "identity one")
        self.assertEqual(raised.exception.reason_code, "HTTP_FORBIDDEN")
        self.assertEqual(result.summary["fixture_count"], 1)
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

    def test_scoped_tapes_isolate_attempts_and_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshots = Path(directory) / "snapshots"
            store = SnapshotStore(snapshots)
            url = "https://jobs.example.com/shared"
            first = self._capture_scope(
                store,
                "attempt-first-0001",
                "career_discovery",
                [("page", url, "first attempt")],
            )
            second = self._capture_scope(
                store,
                "attempt-second-001",
                "career_discovery",
                [("failure", url, "forbidden")],
            )
            other_stage = self._capture_scope(
                store,
                "attempt-first-0001",
                "job_board_discovery",
                [("page", url, "other stage")],
            )

            tapes = load_scoped_outcome_tapes(snapshots, [first, second, other_stage])

        self.assertEqual(tapes[first.scope_id].entries[0].html, "first attempt")
        self.assertIsInstance(tapes[first.scope_id].entries[0], PageOutcomeTapeEntry)
        self.assertIsInstance(tapes[second.scope_id].entries[0], FetchFailureOutcomeTapeEntry)
        self.assertEqual(tapes[other_stage.scope_id].entries[0].html, "other stage")

    def test_scoped_tape_preserves_repeated_identity_in_ordinal_order(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshots = Path(directory) / "snapshots"
            store = SnapshotStore(snapshots)
            url = "https://jobs.example.com/repeated"
            scope = self._capture_scope(
                store,
                "attempt-repeat-0001",
                "opening_match",
                [("page", url, "first"), ("page", url, "second")],
            )

            tape = load_scoped_outcome_tapes(snapshots, [scope])[scope.scope_id]

        self.assertEqual([entry.request_ordinal for entry in tape.entries], [1, 2])
        self.assertEqual([entry.html for entry in tape.entries], ["first", "second"])
        self.assertEqual(tape.entries[0].request, tape.entries[1].request)

    def test_scoped_tape_preserves_crlf_bytes_used_by_scope_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshots = Path(directory) / "snapshots"
            html = "<html>\r\n<body>Jobs</body>\r\n</html>"
            scope = self._capture_scope(
                SnapshotStore(snapshots),
                "attempt-crlf-00001",
                "job_board_discovery",
                [("page", "https://jobs.example.com/careers", html)],
            )

            tape = load_scoped_outcome_tapes(snapshots, [scope])[scope.scope_id]

        self.assertEqual(tape.entries[0].html, html)

    def test_scoped_zero_request_scope_and_orphan_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshots = Path(directory) / "snapshots"
            store = SnapshotStore(snapshots)
            zero = self._capture_scope(
                store,
                "attempt-zero-000001",
                "result_validation",
                [],
            )
            orphan = self._capture_scope(
                store,
                "attempt-orphan-0001",
                "career_discovery",
                [("page", "https://orphan.example/jobs", "orphan")],
            )
            record = json.loads((snapshots / "snapshots.jsonl").read_text(encoding="utf-8"))
            (snapshots / record["blob_path"]).unlink()

            tapes = load_scoped_outcome_tapes(snapshots, [zero])

        self.assertEqual(tapes[zero.scope_id].entries, ())
        self.assertNotIn(orphan.scope_id, tapes)

    def test_scoped_tape_rejects_missing_count_digest_and_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshots = Path(directory) / "snapshots"
            scope = self._capture_scope(
                SnapshotStore(snapshots),
                "attempt-errors-0001",
                "career_discovery",
                [("page", "https://jobs.example.com", "jobs")],
            )
            index = snapshots / "snapshots.jsonl"
            original = index.read_text(encoding="utf-8")

            index.unlink()
            with self.assertRaisesRegex(SnapshotReplayError, "count"):
                load_scoped_outcome_tapes(snapshots, [scope])
            index.write_text(original, encoding="utf-8")

            with self.assertRaisesRegex(SnapshotReplayError, "digest"):
                load_scoped_outcome_tapes(
                    snapshots,
                    [replace(scope, records_sha256="b" * 64)],
                )
            with self.assertRaisesRegex(SnapshotReplayError, "sequence bounds"):
                load_scoped_outcome_tapes(
                    snapshots,
                    [replace(scope, last_sequence=scope.last_sequence + 1)],
                )

    def test_scoped_tape_rejects_duplicate_and_mixed_kind_ordinal(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshots = Path(directory) / "snapshots"
            scope = self._capture_scope(
                SnapshotStore(snapshots),
                "attempt-ordinal-001",
                "career_discovery",
                [
                    ("page", "https://jobs.example.com/one", "one"),
                    ("failure", "https://jobs.example.com/two", "forbidden"),
                ],
            )
            self._rewrite_record(
                snapshots / "fetch-failures.jsonl",
                lambda record: record.__setitem__("request_ordinal", 1),
            )

            with self.assertRaisesRegex(SnapshotReplayError, "duplicate request ordinal"):
                load_scoped_outcome_tapes(snapshots, [scope])

    def test_scoped_tape_rejects_wrong_membership_fields(self):
        mutations = {
            "snapshot_store_id": "different-store-0001",
            "capture_attempt_id": "different-attempt-01",
            "execution_fingerprint": "b" * 64,
            "stage": "opening_match",
        }
        for field, value in mutations.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                snapshots = Path(directory) / "snapshots"
                scope = self._capture_scope(
                    SnapshotStore(snapshots),
                    "attempt-member-0001",
                    "career_discovery",
                    [("page", "https://jobs.example.com", "jobs")],
                )
                self._rewrite_record(
                    snapshots / "snapshots.jsonl",
                    lambda record, field=field, value=value: record.__setitem__(field, value),
                )

                with self.assertRaisesRegex(SnapshotReplayError, "different evidence scope"):
                    load_scoped_outcome_tapes(snapshots, [scope])

    def test_scoped_tape_rejects_unknown_fields_and_schema(self):
        for mutation in (
            lambda record: record.__setitem__("unknown", True),
            lambda record: record.__setitem__("schema_version", 99),
        ):
            with tempfile.TemporaryDirectory() as directory:
                snapshots = Path(directory) / "snapshots"
                scope = self._capture_scope(
                    SnapshotStore(snapshots),
                    "attempt-schema-0001",
                    "career_discovery",
                    [("page", "https://jobs.example.com", "jobs")],
                )
                self._rewrite_record(snapshots / "snapshots.jsonl", mutation)

                with self.assertRaises(SnapshotReplayError):
                    load_scoped_outcome_tapes(snapshots, [scope])

    def test_scoped_tape_rejects_unsafe_path_corrupt_hash_and_private_body(self):
        cases = ("path", "hash", "privacy")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                snapshots = Path(directory) / "snapshots"
                scope = self._capture_scope(
                    SnapshotStore(snapshots),
                    "attempt-safety-0001",
                    "career_discovery",
                    [("page", "https://jobs.example.com", "jobs")],
                )
                record = json.loads((snapshots / "snapshots.jsonl").read_text(encoding="utf-8"))
                if case == "path":
                    self._rewrite_record(
                        snapshots / "snapshots.jsonl",
                        lambda item: item.__setitem__("path", "sites/../../private"),
                    )
                elif case == "hash":
                    self._rewrite_record(
                        snapshots / "snapshots.jsonl",
                        lambda item: item.__setitem__("sha256", "b" * 64),
                    )
                else:
                    (snapshots / record["blob_path"]).write_text(
                        "token=private-secret",
                        encoding="utf-8",
                    )

                with self.assertRaises(SnapshotReplayError):
                    load_scoped_outcome_tapes(snapshots, [scope])

    def test_legacy_materialization_rejects_schema_v3_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            self._capture_scope(
                SnapshotStore(snapshots),
                "attempt-v3-only-001",
                "career_discovery",
                [("page", "https://jobs.example.com", "scoped only")],
            )

            with self.assertRaises(ScopedSnapshotRequiresBundleV6Error) as raised:
                replay_snapshots(snapshots, root / "legacy")

        self.assertEqual(raised.exception.code, "SCOPED_SNAPSHOT_REQUIRES_BUNDLE_V6")
        self.assertEqual(
            raised.exception.records,
            ({"index": "snapshots.jsonl", "line": 1, "schema_version": 3},),
        )
        self.assertIn("bundle v6/scoped replay", str(raised.exception))

    def test_legacy_materialization_rejects_mixed_legacy_and_v3_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            store = SnapshotStore(snapshots)
            store.write_page(
                Page(url="https://legacy.example/jobs", html="legacy", source="live")
            )
            self._capture_scope(
                store,
                "attempt-mixed-0001",
                "career_discovery",
                [("failure", "https://scoped.example/jobs", "forbidden")],
            )

            with self.assertRaises(ScopedSnapshotRequiresBundleV6Error) as raised:
                replay_snapshots(snapshots, root / "legacy")

        self.assertEqual(
            raised.exception.records,
            ({"index": "fetch-failures.jsonl", "line": 1, "schema_version": 3},),
        )

    def test_scoped_rejection_diagnostic_bounds_record_samples(self):
        error = ScopedSnapshotRequiresBundleV6Error(
            ("snapshots.jsonl", line_number) for line_number in range(1, 26)
        )

        payload = error.as_dict()["error"]
        self.assertEqual(payload["record_count"], 25)
        self.assertEqual(len(payload["records"]), error.record_sample_limit)
        self.assertTrue(payload["records_truncated"])
        self.assertIn("5 more", payload["message"])

    def test_legacy_materialization_preserves_existing_output_when_v3_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "legacy"
            self._capture_scope(
                SnapshotStore(snapshots),
                "attempt-preserve-001",
                "career_discovery",
                [("page", "https://scoped.example/jobs", "scoped")],
            )
            fixture = output / "sites" / "existing.html"
            fixture.parent.mkdir(parents=True)
            fixture.write_text("existing fixture", encoding="utf-8")
            manifest = output / "replay-manifest.json"
            manifest.write_text('{"existing": true}\n', encoding="utf-8")

            with self.assertRaises(ScopedSnapshotRequiresBundleV6Error):
                replay_snapshots(snapshots, output)

            self.assertEqual(fixture.read_text(encoding="utf-8"), "existing fixture")
            self.assertEqual(manifest.read_text(encoding="utf-8"), '{"existing": true}\n')

    def test_legacy_only_materialization_remains_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            self._write_snapshot(snapshots)

            result = replay_snapshots(snapshots, root / "legacy")

        self.assertEqual(result.summary["status"], "success")
        self.assertEqual(result.summary["snapshot_records"], 1)
        self.assertGreaterEqual(result.summary["fixture_count"], 1)

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

    def test_cli_rejects_schema_v3_without_overwriting_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            output = root / "replay"
            self._capture_scope(
                SnapshotStore(snapshots),
                "attempt-cli-v3-0001",
                "career_discovery",
                [("page", "https://scoped.example/jobs", "scoped")],
            )
            fixture = output / "sites" / "existing.html"
            fixture.parent.mkdir(parents=True)
            fixture.write_text("existing fixture", encoding="utf-8")
            manifest = output / "replay-manifest.json"
            manifest.write_text('{"existing": true}\n', encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "replay_snapshots.py"),
                    "--snapshot-dir",
                    str(snapshots),
                    "--output-dir",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            error = json.loads(completed.stderr)

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(error["status"], "failed")
            self.assertEqual(
                error["error"]["code"],
                "SCOPED_SNAPSHOT_REQUIRES_BUNDLE_V6",
            )
            self.assertEqual(error["error"]["required_replay"], "bundle_v6_scoped")
            self.assertEqual(fixture.read_text(encoding="utf-8"), "existing fixture")
            self.assertEqual(manifest.read_text(encoding="utf-8"), '{"existing": true}\n')


if __name__ == "__main__":
    unittest.main()
