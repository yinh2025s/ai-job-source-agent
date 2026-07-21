from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from job_source_agent.candidate_reasoning_contracts import (
    LLMDecisionKey,
    LLMDecisionRecord,
    TokenUsage,
)
from job_source_agent.llm_decision_store import (
    FilesystemLLMDecisionStore,
    LLMDecisionFixtureCorrupt,
    LLMDecisionFixtureIncompatible,
    LLMDecisionFixtureMissing,
    LLMDecisionFixturesUnconsumed,
    LLMDecisionReplayDivergence,
    LLMDecisionUnexpectedCall,
    StrictReplayLLMDecisionStore,
    llm_decision_key_digest,
)


def digest(character: str) -> str:
    return character * 64


def make_key(**changes) -> LLMDecisionKey:
    values = {
        "decision_kind": "candidate_rank",
        "normalized_company_identity_digest": digest("a"),
        "input_evidence_digest": digest("b"),
        "llm_provider": "fake-provider",
        "model_id": "fake-model",
        "prompt_version": "prompt-1",
        "decision_schema_version": "schema-1",
        "adapter_version": "adapter-1",
    }
    values.update(changes)
    return LLMDecisionKey(**values)


def make_record(key: LLMDecisionKey | None = None, **changes) -> LLMDecisionRecord:
    key = key or make_key()
    values = {
        "record_key": llm_decision_key_digest(key),
        "execution_fingerprint": digest("c"),
        "key": key,
        "sanitized_request": {"candidate_ids": ["candidate-1"], "context": {"public": True}},
        "sanitized_response": {"ranked_candidate_ids": ["candidate-1"]},
        "candidate_ids": ("candidate-1",),
        "query_ids": ("query-1",),
        "candidate_evidence_digest": digest("d"),
        "duration_ms": 12.5,
        "token_usage": TokenUsage(10, 5, 15),
        "created_at_epoch": 1_700_000_000.0,
        "status": "success",
        "failure_code": None,
    }
    values.update(changes)
    return LLMDecisionRecord(**values)


class FilesystemLLMDecisionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "decisions"
        self.store = FilesystemLLMDecisionStore(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def record_path(self, key: LLMDecisionKey) -> Path:
        value = llm_decision_key_digest(key)
        return self.root / value[:2] / f"{value}.json"

    def test_successful_candidate_rank_round_trip_is_deeply_immutable(self):
        record = make_record()
        self.store.save(record)

        loaded = self.store.load(record.key)

        self.assertEqual(loaded, record)
        self.assertIsNot(loaded, record)
        with self.assertRaises(TypeError):
            loaded.sanitized_request["new"] = "value"
        with self.assertRaises(TypeError):
            loaded.sanitized_request["context"]["public"] = False
        self.assertIsInstance(loaded.sanitized_request["candidate_ids"], tuple)

    def test_key_digest_is_sensitive_to_every_field(self):
        original = make_key()
        mutations = {
            "decision_kind": "query_plan",
            "normalized_company_identity_digest": digest("e"),
            "input_evidence_digest": digest("f"),
            "llm_provider": "other-provider",
            "model_id": "other-model",
            "prompt_version": "prompt-2",
            "decision_schema_version": "schema-2",
            "adapter_version": "adapter-2",
        }
        original_digest = llm_decision_key_digest(original)
        for field, value in mutations.items():
            with self.subTest(field=field):
                self.assertNotEqual(
                    llm_decision_key_digest(replace(original, **{field: value})),
                    original_digest,
                )

    def test_corrupt_truncated_unknown_schema_field_and_nonfinite_are_safe_misses(self):
        record = make_record()
        self.store.save(record)
        path = self.record_path(record.key)
        original = json.loads(path.read_text(encoding="utf-8"))
        variants = []
        variants.append("{")
        unknown_schema = copy.deepcopy(original)
        unknown_schema["schema_version"] = "999"
        variants.append(json.dumps(unknown_schema))
        unknown_field = copy.deepcopy(original)
        unknown_field["record"]["unknown"] = True
        variants.append(json.dumps(unknown_field))
        nonfinite = copy.deepcopy(original)
        nonfinite["record"]["duration_ms"] = math.nan
        variants.append(json.dumps(nonfinite, allow_nan=True))

        for contents in variants:
            with self.subTest(contents=contents[:30]):
                path.write_text(contents, encoding="utf-8")
                self.assertIsNone(self.store.load(record.key))

    def test_symlinked_record_and_root_are_rejected_as_safe_miss_or_save_error(self):
        record = make_record()
        path = self.record_path(record.key)
        path.parent.mkdir(parents=True)
        target = path.parent / "target.json"
        target.write_text("{}", encoding="utf-8")
        path.symlink_to(target)
        self.assertIsNone(self.store.load(record.key))
        with self.assertRaises(OSError):
            self.store.save(record)

        other_root = Path(self.temporary.name) / "other"
        other_root.mkdir()
        linked_root = Path(self.temporary.name) / "linked"
        linked_root.symlink_to(other_root, target_is_directory=True)
        with self.assertRaises(OSError):
            FilesystemLLMDecisionStore(linked_root).save(record)

    def test_failed_atomic_replace_preserves_previous_value_and_cleans_temp(self):
        original = make_record()
        updated = make_record(duration_ms=25.0)
        self.store.save(original)

        with patch(
            "job_source_agent.llm_decision_store.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with self.assertRaisesRegex(OSError, "replace failed"):
                self.store.save(updated)

        self.assertEqual(self.store.load(original.key), original)
        self.assertEqual(list(self.root.rglob("*.tmp")), [])

    def test_failure_and_query_plan_records_are_audit_written_but_not_cache_hits(self):
        failed = make_record(status="failure", failure_code="TIMEOUT")
        self.store.save(failed)
        self.assertTrue(self.record_path(failed.key).exists())
        self.assertIsNone(self.store.load(failed.key))

        query_key = make_key(decision_kind="query_plan")
        query_record = make_record(query_key)
        self.store.save(query_record)
        self.assertTrue(self.record_path(query_key).exists())
        self.assertIsNone(self.store.load(query_key))

    def test_record_key_must_match_all_key_fields(self):
        record = make_record(record_key=digest("e"))
        with self.assertRaisesRegex(ValueError, "record_key"):
            self.store.save(record)

    def test_forbidden_business_evidence_and_secret_fields_are_not_persisted(self):
        for field in ("website", "job_board", "opening"):
            with self.subTest(field=field):
                record = make_record(sanitized_response={field: "value"})
                with self.assertRaisesRegex(ValueError, "forbidden"):
                    self.store.save(record)
        with self.assertRaisesRegex(ValueError, "forbidden"):
            make_record(sanitized_response={"api_key": "secret"})


class StrictReplayLLMDecisionStoreTests(unittest.TestCase):
    def test_replay_load_is_single_pass_and_consumption_is_enforced(self):
        record = make_record()
        store = StrictReplayLLMDecisionStore((record,))

        self.assertEqual(store.load(record.key), record)
        store.assert_consumed()
        with self.assertRaises(LLMDecisionReplayDivergence):
            store.load(record.key)
        with self.assertRaises(LLMDecisionReplayDivergence):
            store.save(record)

    def test_missing_fixture_is_distinct(self):
        key = make_key()
        store = StrictReplayLLMDecisionStore((), expected_keys=(key,))
        with self.assertRaises(LLMDecisionFixtureMissing):
            store.load(key)
        with self.assertRaises(LLMDecisionFixtureMissing):
            store.assert_consumed()

    def test_incompatible_fixture_is_distinct(self):
        expected = make_key(model_id="expected-model")
        actual_key = make_key(model_id="recorded-model")
        store = StrictReplayLLMDecisionStore(
            (make_record(actual_key),),
            expected_keys=(expected,),
        )
        with self.assertRaises(LLMDecisionFixtureIncompatible):
            store.load(expected)

    def test_corrupt_payload_and_record_key_are_distinct(self):
        record = make_record()
        payload = {
            "schema_version": "1",
            "record": {
                "schema_version": record.schema_version,
                "record_key": record.record_key,
                "execution_fingerprint": record.execution_fingerprint,
                "key": {
                    field: getattr(record.key, field)
                    for field in (
                        "decision_kind",
                        "normalized_company_identity_digest",
                        "input_evidence_digest",
                        "llm_provider",
                        "model_id",
                        "prompt_version",
                        "decision_schema_version",
                        "adapter_version",
                    )
                },
                "sanitized_request": {"candidate_ids": ["candidate-1"]},
                "sanitized_response": {"ranked_candidate_ids": ["candidate-1"]},
                "candidate_ids": ["candidate-1"],
                "query_ids": ["query-1"],
                "candidate_evidence_digest": record.candidate_evidence_digest,
                "duration_ms": record.duration_ms,
                "token_usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
                "created_at_epoch": record.created_at_epoch,
                "status": "success",
                "failure_code": None,
            },
        }
        payload["record"]["unknown"] = True
        with self.assertRaises(LLMDecisionFixtureCorrupt):
            StrictReplayLLMDecisionStore.from_payloads((payload,))
        with self.assertRaises(LLMDecisionFixtureCorrupt):
            StrictReplayLLMDecisionStore((replace(record, record_key=digest("e")),))

    def test_unexpected_request_and_unconsumed_fixture_are_divergence(self):
        record = make_record()
        store = StrictReplayLLMDecisionStore((record,))
        with self.assertRaises(LLMDecisionUnexpectedCall):
            store.load(make_key(model_id="unexpected"))
        with self.assertRaises(LLMDecisionFixturesUnconsumed):
            store.assert_consumed()


if __name__ == "__main__":
    unittest.main()
