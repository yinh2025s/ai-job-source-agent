from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from job_source_agent.candidate_reasoning_contracts import CandidateEvidence
from job_source_agent.candidate_reasoning_coordinator import CandidateReasoningResult
from job_source_agent.candidate_reasoning_experiment import (
    ExperimentIntegrityError,
    RecordingCandidateReasoningService,
    extract_deterministic_candidate_urls,
    load_evaluator_labels,
    load_public_cohort,
    load_ranker_evidence_urls,
    reasoning_input_digest,
    verify_sealed_files,
    write_json_atomic,
)
from job_source_agent.candidate_reasoning_inputs import PublicCompanyReasoningInput
from job_source_agent.candidate_reasoning_policy import CandidateReasoningEligibilityResult
from job_source_agent.candidate_reasoning_service import CandidateReasoningInvocationService


ROOT = Path(__file__).resolve().parents[1]
COHORT = ROOT / "samples/evaluation/llm_candidate_reasoning_g_dev_v1.json"
LABELS = ROOT / "samples/evaluation/llm_candidate_reasoning_g_dev_labels_v1.json"


class _StubService(CandidateReasoningInvocationService):
    def __init__(self, result: CandidateReasoningResult) -> None:
        self.result = result

    @property
    def enabled(self) -> bool:
        return True

    def reason(self, company, outcome, *, baseline_candidates=()):
        return self.result


class CandidateReasoningExperimentTest(unittest.TestCase):
    def test_frozen_public_cohort_has_18_answer_free_records_and_stable_digest(self):
        records = load_public_cohort(COHORT)

        self.assertEqual(len(records), 18)
        self.assertEqual(records[0]["record_id"], "006")
        self.assertNotIn("reference_website_url", records[0])
        self.assertEqual(
            reasoning_input_digest(records[0]),
            reasoning_input_digest(dict(records[0])),
        )

    def test_public_cohort_tampering_and_label_shape_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = json.loads(COHORT.read_text(encoding="utf-8"))
            payload["records"][0]["company_name"] = "Changed"
            path = root / "cohort.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ExperimentIntegrityError, "digest"):
                load_public_cohort(path)

            labels = json.loads(LABELS.read_text(encoding="utf-8"))
            labels["records"][0]["unexpected"] = "answer"
            label_path = root / "labels.json"
            label_path.write_text(json.dumps(labels), encoding="utf-8")
            with self.assertRaisesRegex(ExperimentIntegrityError, "fields"):
                load_evaluator_labels(label_path)

    def test_recording_service_keeps_only_public_candidate_evidence(self):
        candidate = CandidateEvidence(
            "candidate-1",
            "https://example.test/",
            "Example",
            "Public search snippet",
            "resolver-search",
            "query-1",
            1,
        )
        result = CandidateReasoningResult(
            CandidateReasoningEligibilityResult(
                "ELIGIBLE",
                True,
                "TYPED_G_CONDITION",
                ("NO_SOURCE_BACKED_CANDIDATE",),
            ),
            (candidate,),
            used_llm_ranking=True,
        )
        service = RecordingCandidateReasoningService(_StubService(result))
        company = PublicCompanyReasoningInput("Example", "example")

        returned = service.reason(company, object())
        records = service.records()

        self.assertIs(returned, result)
        self.assertEqual(records[0]["candidates"][0]["url"], "https://example.test/")
        serialized = json.dumps(records)
        self.assertNotIn("cookie", serialized.casefold())
        self.assertNotIn("authorization", serialized.casefold())

    def test_ranker_evidence_index_uses_invocation_digest_not_company_answer(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "llm-decisions.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "1",
                        "record": {
                            "key": {"decision_kind": "candidate_rank"},
                            "sanitized_request": {
                                "invocation_input_evidence_digest": "a" * 64,
                                "candidates": [
                                    {"url": "https://candidate.test/"},
                                    {"url": "https://candidate.test/"},
                                ],
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            indexed = load_ranker_evidence_urls(path)

            self.assertEqual(indexed["a" * 64], ("https://candidate.test/",))

    def test_sealed_file_verifier_rejects_changed_or_symlinked_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json_atomic(root / "artifact.json", {"value": 1})
            import hashlib

            digest = hashlib.sha256((root / "artifact.json").read_bytes()).hexdigest()
            manifest = {"files": {"artifact.json": digest}}
            verify_sealed_files(root, manifest)

            (root / "artifact.json").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ExperimentIntegrityError, "mismatch"):
                verify_sealed_files(root, manifest)

    def test_deterministic_candidate_extraction_is_bounded_and_deduplicated(self):
        trace = {
            "trace": {
                "stages": {
                    "website_resolution": {
                        "candidates": [
                            {"url": "https://one.test/"},
                            {"url": "https://one.test/"},
                            {"url": "http://unsafe.test/"},
                            {"url": "https://two.test/"},
                            {"url": "https://three.test/"},
                            {"url": "https://four.test/"},
                        ]
                    }
                }
            }
        }
        self.assertEqual(
            extract_deterministic_candidate_urls(trace),
            ("https://one.test/", "https://two.test/", "https://three.test/"),
        )


if __name__ == "__main__":
    unittest.main()
