import dataclasses
import unittest

from job_source_agent.evidence_scope import (
    EMPTY_RECORDS_SHA256,
    EvidenceScopeRef,
    StageEvidenceLineage,
    evidence_scope_id,
    new_capture_attempt_id,
)


class EvidenceScopeContractTests(unittest.TestCase):
    def setUp(self):
        self.store_id = "snapshot-store-0001"
        self.attempt_id = new_capture_attempt_id()
        self.execution_fingerprint = "a" * 64

    def scope(self, **overrides):
        values = {
            "snapshot_store_id": self.store_id,
            "scope_id": evidence_scope_id(
                self.store_id,
                self.attempt_id,
                self.execution_fingerprint,
                "career_discovery",
            ),
            "capture_attempt_id": self.attempt_id,
            "execution_fingerprint": self.execution_fingerprint,
            "stage": "career_discovery",
            "request_count": 0,
            "records_sha256": EMPTY_RECORDS_SHA256,
        }
        values.update(overrides)
        return EvidenceScopeRef(**values)

    def test_empty_scope_is_typed_and_round_trips_strictly(self):
        scope = self.scope()
        lineage = StageEvidenceLineage(
            stage=scope.stage,
            execution_fingerprint=scope.execution_fingerprint,
            producer_attempt_id=scope.capture_attempt_id,
            snapshot_scope=scope,
        )

        restored = StageEvidenceLineage.from_payload(dataclasses.asdict(lineage))

        self.assertEqual(restored, lineage)
        self.assertIsInstance(restored.snapshot_scope, EvidenceScopeRef)

    def test_scope_id_binds_store_attempt_execution_and_stage(self):
        baseline = self.scope().scope_id

        self.assertNotEqual(
            baseline,
            evidence_scope_id(
                self.store_id,
                new_capture_attempt_id(),
                self.execution_fingerprint,
                "career_discovery",
            ),
        )
        self.assertNotEqual(
            baseline,
            evidence_scope_id(
                self.store_id,
                self.attempt_id,
                self.execution_fingerprint,
                "job_board_discovery",
            ),
        )

    def test_nonempty_scope_requires_consistent_sequence_bounds(self):
        with self.assertRaisesRegex(ValueError, "sequence bounds"):
            self.scope(request_count=1, records_sha256="b" * 64)
        with self.assertRaisesRegex(ValueError, "reversed"):
            self.scope(
                request_count=2,
                records_sha256="b" * 64,
                first_sequence=9,
                last_sequence=3,
            )

    def test_lineage_rejects_scope_from_another_stage_or_attempt(self):
        scope = self.scope()
        with self.assertRaisesRegex(ValueError, "stage does not match"):
            StageEvidenceLineage(
                stage="job_board_discovery",
                execution_fingerprint=self.execution_fingerprint,
                producer_attempt_id=self.attempt_id,
                snapshot_scope=scope,
            )
        with self.assertRaisesRegex(ValueError, "attempt does not match"):
            StageEvidenceLineage(
                stage=scope.stage,
                execution_fingerprint=self.execution_fingerprint,
                producer_attempt_id=new_capture_attempt_id(),
                snapshot_scope=scope,
            )

    def test_payload_rejects_unknown_fields_and_unsafe_identifiers(self):
        payload = dataclasses.asdict(self.scope())
        payload["raw_html"] = "secret"
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            EvidenceScopeRef.from_payload(payload)
        with self.assertRaisesRegex(ValueError, "privacy-safe"):
            self.scope(capture_attempt_id="../../cookies")


if __name__ == "__main__":
    unittest.main()
