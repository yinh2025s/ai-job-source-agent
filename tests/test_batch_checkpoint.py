import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_source_agent.batch_checkpoint import (
    BATCH_COMPLETION_SCHEMA_VERSION,
    BatchCompletion,
    FilesystemBatchCompletionStore,
)
from job_source_agent.checkpoint import ADAPTER_VERSION, input_fingerprint
from job_source_agent.evidence_scope import StageEvidenceLineage
from job_source_agent.run_configuration import AgentConfig, DeterministicRunConfig


class FilesystemBatchCompletionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.store = FilesystemBatchCompletionStore(self.temporary_directory.name)
        self.input_record = {
            "company_name": "Example Corp",
            "company_website_url": "https://example.com",
            "job_title": "AI Engineer",
        }
        self.result = {"company_name": "Example Corp", "opening_url": "https://example.com/jobs/1"}
        self.trace = {"stages": [{"stage": "opening_match", "status": "success"}]}

    def test_save_and_load_versioned_completion(self):
        saved = self.store.save(self.input_record, self.result, self.trace, 1.25)

        self.assertEqual(
            saved,
            BatchCompletion(
                input_fingerprint(self.input_record),
                self.store.fingerprint(self.input_record),
                self.result,
                self.trace,
                1.25,
            ),
        )
        self.assertEqual(self.store.load(self.input_record), saved)

        payload = json.loads(self.store._completion_path(saved.execution_fingerprint).read_text())
        self.assertEqual(payload["batch_completion_schema_version"], BATCH_COMPLETION_SCHEMA_VERSION)
        self.assertEqual(payload["adapter_version"], ADAPTER_VERSION)
        self.assertEqual(payload["input_fingerprint"], input_fingerprint(self.input_record))
        self.assertEqual(payload["execution_fingerprint"], saved.execution_fingerprint)

    def test_equivalent_input_uses_stable_fingerprint(self):
        self.store.save(self.input_record, self.result, self.trace, 1)
        equivalent = {**self.input_record, "company_name": "  Example   Corp "}

        self.assertIsNotNone(self.store.load(equivalent))

    def test_completion_freezes_canonical_mixed_attempt_stage_lineage(self):
        pipeline_fingerprint = "a" * 64
        lineages = [
            StageEvidenceLineage(
                stage="linkedin_discovery",
                execution_fingerprint=pipeline_fingerprint,
                producer_attempt_id="capture-attempt-first",
            ),
            StageEvidenceLineage(
                stage="website_resolution",
                execution_fingerprint=pipeline_fingerprint,
                producer_attempt_id="capture-attempt-second",
            ),
        ]
        trace = {
            "trace": {
                "stage_evidence_lineage": [
                    {
                        "stage": item.stage,
                        "execution_fingerprint": item.execution_fingerprint,
                        "producer_attempt_id": item.producer_attempt_id,
                        "snapshot_scope": None,
                        "schema_version": item.schema_version,
                    }
                    for item in lineages
                ]
            }
        }

        saved = self.store.save(self.input_record, self.result, trace, 1)
        payload = json.loads(
            self.store._completion_path(saved.execution_fingerprint).read_text()
        )

        self.assertEqual(saved.stage_evidence_lineage, tuple(lineages))
        self.assertEqual(
            [item["producer_attempt_id"] for item in payload["stage_evidence_lineage"]],
            ["capture-attempt-first", "capture-attempt-second"],
        )

    def test_completion_rejects_noncanonical_or_unknown_lineage(self):
        lineage = {
            "stage": "website_resolution",
            "execution_fingerprint": "a" * 64,
            "producer_attempt_id": "capture-attempt-0001",
            "snapshot_scope": None,
            "schema_version": "1.0",
        }
        invalid = [
            [lineage, lineage],
            [lineage, {**lineage, "stage": "linkedin_discovery"}],
            [{**lineage, "raw_html": "secret"}],
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.save(
                    self.input_record,
                    self.result,
                    {"trace": {"stage_evidence_lineage": value}},
                    1,
                )

    def test_scan_only_returns_expected_compatible_inputs(self):
        second = {**self.input_record, "company_name": "Second Corp"}
        absent = {**self.input_record, "company_name": "Absent Corp"}
        first_completion = self.store.save(self.input_record, self.result, self.trace, 1)
        second_completion = self.store.save(second, {"company_name": "Second Corp"}, {}, 2)

        scanned = self.store.scan([absent, second, self.input_record, second])

        self.assertEqual(
            scanned,
            {
                first_completion.execution_fingerprint: first_completion,
                second_completion.execution_fingerprint: second_completion,
            },
        )

    def test_configuration_mismatch_is_a_cache_miss(self):
        first_config = DeterministicRunConfig.from_agent_config(AgentConfig(max_job_pages=3))
        second_config = DeterministicRunConfig.from_agent_config(AgentConfig(max_job_pages=4))
        first_store = FilesystemBatchCompletionStore(
            self.temporary_directory.name,
            first_config,
        )
        second_store = FilesystemBatchCompletionStore(
            self.temporary_directory.name,
            second_config,
        )

        saved = first_store.save(self.input_record, self.result, self.trace, 1)

        self.assertNotEqual(
            first_store.fingerprint(self.input_record),
            second_store.fingerprint(self.input_record),
        )
        self.assertEqual(first_store.scan([self.input_record]), {saved.execution_fingerprint: saved})
        self.assertIsNone(second_store.load(self.input_record))
        self.assertEqual(second_store.scan([self.input_record]), {})

    def test_batch_execution_scope_mismatch_is_a_cache_miss(self):
        first = FilesystemBatchCompletionStore(
            self.temporary_directory.name,
            completion_scope_digest="a" * 64,
        )
        second = FilesystemBatchCompletionStore(
            self.temporary_directory.name,
            completion_scope_digest="b" * 64,
        )

        first.save(self.input_record, self.result, self.trace, 1)

        self.assertIsNone(second.load(self.input_record))

    def test_corrupt_incomplete_and_incompatible_records_are_ignored(self):
        completion = self.store.save(self.input_record, self.result, self.trace, 1)
        path = self.store._completion_path(completion.execution_fingerprint)
        valid = json.loads(path.read_text())

        invalid_payloads = [
            "{broken",
            {**valid, "batch_completion_schema_version": "old"},
            {**valid, "adapter_version": "old"},
            {**valid, "input_fingerprint": "0" * 64},
            {**valid, "execution_fingerprint": "0" * 64},
            {key: value for key, value in valid.items() if key != "trace"},
            {**valid, "unexpected": True},
            {**valid, "result": []},
            {**valid, "elapsed": -1},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
                self.assertIsNone(self.store.load(self.input_record))

    def test_failed_replace_preserves_previous_completion_and_removes_temp_file(self):
        old = self.store.save(self.input_record, self.result, self.trace, 1)
        path = self.store._completion_path(old.execution_fingerprint)

        with patch("job_source_agent.batch_checkpoint.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                self.store.save(self.input_record, {"new": True}, {}, 2)

        self.assertEqual(self.store.load(self.input_record), old)
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_load_removes_only_stale_temporary_files_for_its_fingerprint(self):
        completion = self.store.save(self.input_record, self.result, self.trace, 1)
        path = self.store._completion_path(completion.execution_fingerprint)
        stale = path.parent / f".{completion.execution_fingerprint}.crashed.tmp"
        unrelated = path.parent / ".another-fingerprint.active.tmp"
        stale.write_text("partial", encoding="utf-8")
        unrelated.write_text("partial", encoding="utf-8")

        self.assertEqual(self.store.load(self.input_record), completion)

        self.assertFalse(stale.exists())
        self.assertTrue(unrelated.exists())

    def test_save_rejects_invalid_payloads(self):
        invalid_cases = [
            ([], {}, 1),
            ({}, [], 1),
            ({"bad": {1}}, {}, 1),
            ({}, {}, True),
            ({}, {}, float("inf")),
            ({}, {}, -0.1),
        ]
        for result, trace, elapsed in invalid_cases:
            with self.subTest(result=result, trace=trace, elapsed=elapsed):
                with self.assertRaises(ValueError):
                    self.store.save(self.input_record, result, trace, elapsed)

    def test_missing_record_and_unreadable_directory_are_safe_misses(self):
        self.assertIsNone(self.store.load(self.input_record))
        fingerprint = self.store.fingerprint(self.input_record)
        path = self.store._completion_path(fingerprint)
        path.mkdir(parents=True)

        self.assertIsNone(self.store.load(self.input_record))


if __name__ == "__main__":
    unittest.main()
