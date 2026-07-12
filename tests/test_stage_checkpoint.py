import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from job_source_agent.checkpoint import ADAPTER_VERSION, CHECKPOINT_SCHEMA_VERSION
from job_source_agent.contracts import CheckpointStore, StageExecution
from job_source_agent.models import PIPELINE_STAGES, StageResult
from job_source_agent.stage_checkpoint import FilesystemCheckpointStore


class FilesystemCheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = FilesystemCheckpointStore(self.root)
        self.fingerprint = "a" * 64

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_satisfies_checkpoint_contract_and_round_trips_dataclasses(self):
        execution = StageExecution(
            result=StageResult(
                stage="career_discovery",
                status="success",
                provider="greenhouse",
                output_count=1,
                evidence=[{"url": "https://example.test/careers"}],
            ),
            updates={"career_page_url": "https://example.test/careers"},
            trace={"candidates": [{"score": 10}]},
        )

        self.assertIsInstance(self.store, CheckpointStore)
        self.store.save(self.fingerprint, execution)

        self.assertEqual(self.store.load(self.fingerprint, "career_discovery"), execution)
        payload = json.loads(next(self.root.rglob("career_discovery.json")).read_text())
        self.assertEqual(payload["checkpoint_schema_version"], CHECKPOINT_SCHEMA_VERSION)
        self.assertEqual(payload["adapter_version"], ADAPTER_VERSION)
        self.assertEqual(payload["input_fingerprint"], self.fingerprint)

    def test_missing_and_corrupt_checkpoints_are_safe_cache_misses(self):
        self.assertIsNone(self.store.load(self.fingerprint, "career_discovery"))

        execution = StageExecution(StageResult(stage="career_discovery", status="success"))
        self.store.save(self.fingerprint, execution)
        path = next(self.root.rglob("career_discovery.json"))
        path.write_text("{truncated", encoding="utf-8")

        self.assertIsNone(self.store.load(self.fingerprint, "career_discovery"))

    def test_incompatible_or_mismatched_metadata_is_not_loaded(self):
        execution = StageExecution(StageResult(stage="career_discovery", status="success"))
        incompatible_fields = {
            "checkpoint_schema_version": "old",
            "adapter_version": "old",
            "input_fingerprint": "wrong",
            "stage": "job_board_discovery",
        }

        for field, value in incompatible_fields.items():
            with self.subTest(field=field):
                self.store.save(self.fingerprint, execution)
                path = next(self.root.rglob("career_discovery.json"))
                payload = json.loads(path.read_text())
                payload[field] = value
                path.write_text(json.dumps(payload), encoding="utf-8")
                self.assertIsNone(self.store.load(self.fingerprint, "career_discovery"))

    def test_invalidate_from_removes_selected_and_downstream_only(self):
        for stage in PIPELINE_STAGES:
            self.store.save(self.fingerprint, StageExecution(StageResult(stage=stage, status="success")))

        self.store.invalidate_from(self.fingerprint, "job_board_discovery")

        for stage in PIPELINE_STAGES[:4]:
            self.assertIsNotNone(self.store.load(self.fingerprint, stage))
        for stage in PIPELINE_STAGES[4:]:
            self.assertIsNone(self.store.load(self.fingerprint, stage))

    def test_invalid_stage_and_empty_fingerprint_are_rejected(self):
        execution = StageExecution(StageResult(stage="unknown", status="success"))
        with self.assertRaisesRegex(ValueError, "Unknown pipeline stage"):
            self.store.save(self.fingerprint, execution)
        with self.assertRaisesRegex(ValueError, "Unknown pipeline stage"):
            self.store.invalidate_from(self.fingerprint, "unknown")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.store.load("", "career_discovery")

    def test_fingerprint_cannot_escape_store_root(self):
        execution = StageExecution(StageResult(stage="career_discovery", status="success"))
        self.store.save("../../outside", execution)

        paths = list(self.root.rglob("career_discovery.json"))
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].is_relative_to(self.root))
        self.assertEqual(self.store.load("../../outside", "career_discovery"), execution)

    def test_concurrent_saves_publish_one_complete_execution(self):
        executions = [
            StageExecution(
                StageResult(stage="career_discovery", status="success", output_count=index),
                updates={"writer": index, "payload": "x" * 4096},
            )
            for index in range(12)
        ]
        barrier = Barrier(len(executions))

        def save(execution):
            barrier.wait()
            self.store.save(self.fingerprint, execution)

        with ThreadPoolExecutor(max_workers=len(executions)) as executor:
            list(executor.map(save, executions))

        self.assertIn(self.store.load(self.fingerprint, "career_discovery"), executions)
        self.assertEqual(list(self.root.rglob(".career_discovery.*.tmp")), [])

    def test_failed_atomic_replace_and_next_save_clean_temporary_files(self):
        execution = StageExecution(StageResult(stage="career_discovery", status="success"))
        with patch("job_source_agent.stage_checkpoint.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                self.store.save(self.fingerprint, execution)
        self.assertEqual(list(self.root.rglob(".career_discovery.*.tmp")), [])

        directory = self.store._fingerprint_directory(self.fingerprint)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ".career_discovery.crashed.tmp").write_text("partial", encoding="utf-8")
        self.store.save(self.fingerprint, execution)
        self.assertEqual(list(self.root.rglob(".career_discovery.*.tmp")), [])

    def test_parallel_first_saves_tolerate_shared_parent_directory_race(self):
        first = "parent-race-0"
        prefix = hashlib.sha256(first.encode()).hexdigest()[:2]
        second = next(
            f"parent-race-{index}"
            for index in range(1, 10_000)
            if hashlib.sha256(f"parent-race-{index}".encode()).hexdigest()[:2] == prefix
        )
        barrier = Barrier(2)

        def save(fingerprint):
            barrier.wait()
            execution = StageExecution(
                StageResult(stage="career_discovery", status="success"),
                updates={"fingerprint": fingerprint},
            )
            self.store.save(fingerprint, execution)
            return execution

        with ThreadPoolExecutor(max_workers=2) as executor:
            expected = list(executor.map(save, (first, second)))

        self.assertEqual(self.store.load(first, "career_discovery"), expected[0])
        self.assertEqual(self.store.load(second, "career_discovery"), expected[1])

    def test_load_save_and_invalidate_race_returns_only_complete_value_or_miss(self):
        old = StageExecution(
            StageResult(stage="career_discovery", status="success", output_count=1),
            updates={"version": "old"},
        )
        new = StageExecution(
            StageResult(stage="career_discovery", status="success", output_count=2),
            updates={"version": "new"},
        )
        self.store.save(self.fingerprint, old)
        barrier = Barrier(3)

        def load():
            barrier.wait()
            return self.store.load(self.fingerprint, "career_discovery")

        def save():
            barrier.wait()
            self.store.save(self.fingerprint, new)

        def invalidate():
            barrier.wait()
            self.store.invalidate_from(self.fingerprint, "career_discovery")

        with ThreadPoolExecutor(max_workers=3) as executor:
            load_future = executor.submit(load)
            save_future = executor.submit(save)
            invalidate_future = executor.submit(invalidate)
            observed = load_future.result()
            save_future.result()
            invalidate_future.result()

        self.assertIn(observed, (None, old, new))
        self.assertIn(self.store.load(self.fingerprint, "career_discovery"), (None, new))
        self.assertEqual(list(self.root.rglob(".career_discovery.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
