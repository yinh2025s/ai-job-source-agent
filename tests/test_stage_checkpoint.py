import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
