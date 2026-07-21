from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from job_source_agent.candidate_reasoning_contracts import (
    LLMDecisionKey,
    LLMDecisionRecord,
    TokenUsage,
    llm_decision_key_digest,
)
from job_source_agent.llm_decision_bundle import (
    LLM_DECISION_BUNDLE_CORRUPT,
    LLM_DECISION_BUNDLE_EXTRA,
    LLM_DECISION_BUNDLE_INCOMPATIBLE,
    LLM_DECISION_BUNDLE_MISSING,
    LLM_DECISION_MANIFEST_FILENAME,
    LLM_DECISIONS_FILENAME,
    AuditedLLMDecisionStore,
    LLMDecisionBundleCorrupt,
    LLMDecisionBundleExtra,
    LLMDecisionBundleIncompatible,
    LLMDecisionBundleMissing,
    freeze_llm_decision_fixture,
    load_llm_decision_fixture,
)
from job_source_agent.llm_decision_store import (
    LLMDecisionFixturesUnconsumed,
    LLMDecisionUnexpectedCall,
    serialize_llm_decision_record,
)


EXECUTION = "a" * 64
RUN_CONFIG = "b" * 64
INVOCATION = "c" * 64
OTHER_INVOCATION = "d" * 64
EVIDENCE = "e" * 64


class LLMDecisionBundleTest(unittest.TestCase):
    def test_live_artifacts_round_trip_into_zero_client_replay_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "live"
            store = self._audited(root)
            planner = self._record("query_plan", INVOCATION)
            ranker = self._record(
                "candidate_rank",
                "f" * 64,
                invocation_digest=INVOCATION,
            )

            store.save(planner)
            store.save(ranker)

            self.assertTrue((root / LLM_DECISIONS_FILENAME).is_file())
            manifest = json.loads(
                (root / LLM_DECISION_MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["record_count"], 2)
            replay, loaded_manifest = load_llm_decision_fixture(root, **self._identity())
            self.assertEqual(replay.load(planner.key), planner)
            self.assertEqual(replay.load(ranker.key), ranker)
            replay.assert_consumed()
            self.assertEqual(loaded_manifest["execution_identity"], EXECUTION)

    def test_freezer_selects_one_invocation_and_binds_both_file_digests(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "live"
            bundle = Path(temporary) / "bundle"
            store = self._audited(source)
            selected_planner = self._record("query_plan", INVOCATION)
            selected_ranker = self._record(
                "candidate_rank", "f" * 64, invocation_digest=INVOCATION
            )
            other = self._record("query_plan", OTHER_INVOCATION)
            for record in (selected_planner, selected_ranker, other):
                store.save(record)

            provenance = freeze_llm_decision_fixture(
                source,
                bundle,
                selected_input_evidence_digests=(INVOCATION,),
                **self._identity(),
            )

            self.assertEqual(provenance["record_count"], 2)
            self.assertEqual(
                provenance["decisions_sha256"],
                hashlib.sha256((bundle / LLM_DECISIONS_FILENAME).read_bytes()).hexdigest(),
            )
            self.assertEqual(
                provenance["manifest_sha256"],
                hashlib.sha256((bundle / LLM_DECISION_MANIFEST_FILENAME).read_bytes()).hexdigest(),
            )
            replay, _ = load_llm_decision_fixture(bundle, **self._identity())
            self.assertEqual(replay.load(selected_planner.key), selected_planner)
            self.assertEqual(replay.load(selected_ranker.key), selected_ranker)
            replay.assert_consumed()

    def test_missing_extra_corrupt_and_incompatible_are_distinct(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixture"
            with self.assertRaises(LLMDecisionBundleMissing) as missing:
                load_llm_decision_fixture(root, **self._identity())
            self.assertIn(LLM_DECISION_BUNDLE_MISSING, str(missing.exception))

            extra_root = Path(temporary) / "extra"
            self._audited(extra_root).save(self._record("query_plan", INVOCATION))
            decisions = extra_root / LLM_DECISIONS_FILENAME
            decisions.write_bytes(decisions.read_bytes() + decisions.read_bytes())
            manifest_path = extra_root / LLM_DECISION_MANIFEST_FILENAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["decisions_sha256"] = hashlib.sha256(decisions.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(LLMDecisionBundleExtra) as extra:
                load_llm_decision_fixture(extra_root, **self._identity())
            self.assertIn(LLM_DECISION_BUNDLE_EXTRA, str(extra.exception))

            corrupt_root = Path(temporary) / "corrupt"
            self._audited(corrupt_root).save(self._record("query_plan", INVOCATION))
            decisions = corrupt_root / LLM_DECISIONS_FILENAME
            manifest_path = corrupt_root / LLM_DECISION_MANIFEST_FILENAME
            decisions.write_text("{not-json}\n", encoding="utf-8")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["decisions_sha256"] = hashlib.sha256(decisions.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(LLMDecisionBundleCorrupt) as corrupt:
                load_llm_decision_fixture(corrupt_root, **self._identity())
            self.assertIn(LLM_DECISION_BUNDLE_CORRUPT, str(corrupt.exception))

            other_root = Path(temporary) / "incompatible"
            self._audited(other_root).save(self._record("query_plan", INVOCATION))
            incompatible_identity = {**self._identity(), "model_id": "other-model"}
            with self.assertRaises(LLMDecisionBundleIncompatible) as incompatible:
                load_llm_decision_fixture(other_root, **incompatible_identity)
            self.assertIn(LLM_DECISION_BUNDLE_INCOMPATIBLE, str(incompatible.exception))

    def test_unexpected_and_unconsumed_are_distinct_replay_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixture"
            record = self._record("query_plan", INVOCATION)
            self._audited(root).save(record)
            replay, _ = load_llm_decision_fixture(root, **self._identity())

            with self.assertRaises(LLMDecisionUnexpectedCall):
                replay.load(self._record("query_plan", OTHER_INVOCATION).key)
            with self.assertRaises(LLMDecisionFixturesUnconsumed):
                replay.assert_consumed()

    @staticmethod
    def _identity() -> dict[str, str]:
        return {
            "execution_identity": EXECUTION,
            "run_configuration_digest": RUN_CONFIG,
            "llm_provider": "fake-provider",
            "model_id": "fake-model",
            "prompt_version": "prompt-v1",
            "adapter_version": "adapter-v1",
        }

    @classmethod
    def _audited(cls, root: Path) -> AuditedLLMDecisionStore:
        return AuditedLLMDecisionStore(root, **cls._identity())

    @staticmethod
    def _record(
        decision_kind: str,
        input_digest: str,
        *,
        invocation_digest: str | None = None,
    ) -> LLMDecisionRecord:
        key = LLMDecisionKey(
            decision_kind,
            "1" * 64,
            input_digest,
            "fake-provider",
            "fake-model",
            "prompt-v1",
            "1",
            "adapter-v1",
        )
        request = {"normalized_company_name": "Example Labs"}
        if invocation_digest is not None:
            request["invocation_input_evidence_digest"] = invocation_digest
        response = (
            {
                "schema_version": "1",
                "normalized_company_name": "Example Labs",
                "core_brand_tokens": ["Example"],
                "legal_or_descriptive_suffixes": ["Labs"],
                "possible_aliases": [],
                "queries": [],
                "ambiguous": False,
                "reason_codes": [],
            }
            if decision_kind == "query_plan"
            else {"schema_version": "1", "ranked_candidates": [], "ambiguous": False}
        )
        return LLMDecisionRecord(
            record_key=llm_decision_key_digest(key),
            execution_fingerprint=EXECUTION,
            key=key,
            sanitized_request=request,
            sanitized_response=response,
            candidate_ids=(),
            query_ids=(),
            candidate_evidence_digest=EVIDENCE,
            duration_ms=1.0,
            token_usage=TokenUsage(2, 1, 3),
            created_at_epoch=1.0,
            status="success",
            failure_code=None,
        )


if __name__ == "__main__":
    unittest.main()
