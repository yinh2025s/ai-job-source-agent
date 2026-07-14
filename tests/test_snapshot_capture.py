import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from job_source_agent.evidence_scope import EMPTY_RECORDS_SHA256, new_capture_attempt_id
from job_source_agent.snapshot import SnapshotStore, SnapshottingFetcher
from job_source_agent.snapshot_capture import SnapshotCaptureCoordinator
from job_source_agent.web import FetchError, Page


class _Fetcher:
    def fetch(self, url, data=None, headers=None):
        if "/failure" in url:
            raise FetchError("private token", reason_code="NETWORK_TIMEOUT", retryable=True)
        return Page(url=url, html=f"<html>{url.rsplit('/', 1)[-1]}</html>", source="live")


class SnapshotCaptureTests(unittest.TestCase):
    fingerprint = "a" * 64
    stage = "career_discovery"

    def build(self, directory):
        coordinator = SnapshotCaptureCoordinator()
        fetcher = SnapshottingFetcher(_Fetcher(), directory, coordinator=coordinator)
        return coordinator, fetcher

    def test_zero_request_scope_and_atomic_opaque_store_id(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, _ = self.build(directory)
            coordinator.begin_stage(new_capture_attempt_id(), self.fingerprint, self.stage)
            scope = coordinator.finalize()
            store_id_text = (Path(directory) / ".snapshot-store-id").read_text(encoding="ascii")

        self.assertEqual(scope.request_count, 0)
        self.assertEqual(scope.records_sha256, EMPTY_RECORDS_SHA256)
        self.assertEqual(store_id_text.strip(), scope.snapshot_store_id)
        self.assertNotIn(directory, store_id_text)

    def test_store_id_is_shared_by_concurrent_store_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            with ThreadPoolExecutor(max_workers=12) as executor:
                store_ids = list(
                    executor.map(
                        lambda _: SnapshotStore(directory).snapshot_store_id,
                        range(24),
                    )
                )

        self.assertEqual(len(set(store_ids)), 1)
        self.assertRegex(store_ids[0], r"^[a-f0-9]{32}$")

    def test_v3_page_and_failure_preserve_v2_fields_and_add_scope_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, fetcher = self.build(directory)
            attempt_id = new_capture_attempt_id()
            coordinator.begin_stage(attempt_id, self.fingerprint, self.stage)
            fetcher.fetch("https://example.com/page?token=credential")
            with self.assertRaises(FetchError):
                fetcher.fetch("https://example.com/failure?token=credential")
            scope = coordinator.finalize()
            page = json.loads((Path(directory) / "snapshots.jsonl").read_text(encoding="utf-8"))
            failure = json.loads((Path(directory) / "fetch-failures.jsonl").read_text(encoding="utf-8"))

        scope_fields = {
            "snapshot_store_id",
            "scope_id",
            "capture_attempt_id",
            "execution_fingerprint",
            "stage",
            "request_ordinal",
        }
        page_v2_fields = {
            "schema_version",
            "kind",
            "sequence",
            "request",
            "request_url",
            "page_url",
            "final_url",
            "sanitized_url",
            "source",
            "path",
            "blob_path",
            "artifact_paths",
            "artifact_blob_paths",
            "sha256",
            "byte_count",
            "captured_at_epoch",
        }
        failure_v2_fields = {
            "schema_version",
            "kind",
            "sequence",
            "request",
            "failure",
            "captured_at_epoch",
            "terminal",
        }
        self.assertEqual(page["schema_version"], 3)
        self.assertEqual(failure["schema_version"], 3)
        self.assertEqual(set(page), page_v2_fields | scope_fields)
        self.assertEqual(set(failure), failure_v2_fields | scope_fields)
        self.assertEqual((page["request_ordinal"], failure["request_ordinal"]), (1, 2))
        self.assertEqual(scope.request_count, 2)
        self.assertEqual((scope.first_sequence, scope.last_sequence), (1, 2))
        persisted = json.dumps([page, failure])
        self.assertNotIn("credential", persisted)
        self.assertNotIn(directory, persisted)

    def test_concurrent_fetches_receive_unique_stage_local_ordinals(self):
        count = 32
        barrier = threading.Barrier(count)

        class ConcurrentFetcher:
            def fetch(self, url, data=None, headers=None):
                barrier.wait()
                return Page(url=url, html="ok", source="live")

        with tempfile.TemporaryDirectory() as directory:
            coordinator = SnapshotCaptureCoordinator()
            fetcher = SnapshottingFetcher(ConcurrentFetcher(), directory, coordinator=coordinator)
            coordinator.begin_stage(new_capture_attempt_id(), self.fingerprint, self.stage)
            with ThreadPoolExecutor(max_workers=count) as executor:
                list(executor.map(fetcher.fetch, [f"https://example.com/{i}" for i in range(count)]))
            scope = coordinator.finalize()
            records = [
                json.loads(line)
                for line in (Path(directory) / "snapshots.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(scope.request_count, count)
        self.assertEqual({record["request_ordinal"] for record in records}, set(range(1, count + 1)))

    def test_digest_is_stable_across_terminal_completion_order_and_global_sequences(self):
        def capture(directory, urls):
            coordinator, fetcher = self.build(directory)
            coordinator.begin_stage(new_capture_attempt_id(), self.fingerprint, self.stage)
            captures = [coordinator.begin_request() for _ in urls]
            records = [
                fetcher.snapshot_store.write_page(
                    Page(url=url, html="same", source="live"),
                    capture=capture,
                )
                for url, capture in reversed(list(zip(urls, captures)))
            ]
            for record in records:
                coordinator.accept_terminal_record(record)
            return coordinator.finalize().records_sha256

        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_digest = capture(first, ["https://example.com/a", "https://example.com/b"])
            second_store = SnapshotStore(second)
            second_store.write_page(Page(url="https://example.com/prefix", html="old", source="live"))
            second_digest = capture(second, ["https://example.com/a", "https://example.com/b"])

        self.assertEqual(first_digest, second_digest)
        self.assertRegex(first_digest, r"^[0-9a-f]{64}$")

    def test_scoped_fetcher_requires_active_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            _, fetcher = self.build(directory)
            with self.assertRaisesRegex(RuntimeError, "No snapshot stage scope"):
                fetcher.fetch("https://example.com/jobs")
            self.assertFalse((Path(directory) / "snapshots.jsonl").exists())

    def test_aborted_stage_drops_orphan_scope_and_allows_next_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, _ = self.build(directory)
            attempt_id = new_capture_attempt_id()
            coordinator.begin_stage(attempt_id, self.fingerprint, self.stage)
            coordinator.begin_request()
            coordinator.abort_stage()

            coordinator.begin_stage(
                attempt_id,
                self.fingerprint,
                "job_board_discovery",
            )
            scope = coordinator.finalize()

        self.assertEqual(scope.stage, "job_board_discovery")
        self.assertEqual(scope.request_count, 0)


if __name__ == "__main__":
    unittest.main()
