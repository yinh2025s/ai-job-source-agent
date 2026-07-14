import unittest

from scripts.replay_failure_bundle import _build_outcome_gate


def _identity(*, verdict="system_gap", codes=None, conflicts=None):
    return {
        "identity_verdict": verdict,
        "identity_failure_codes": codes or ["RESULT_IDENTITY_MISMATCH"],
        "conflicting_fields": conflicts or ["provider.tenant"],
        "normalized_identity_chain": {
            "provider": {
                "tenant": "acme/jobs",
                "canonical_board_url": "https://jobs.example.test/acme/",
            },
            "opening": {
                "canonical_opening_url": "https://jobs.example.test/acme/123/",
            },
        },
    }


def _identity_failure(*, reason_code="RESULT_IDENTITY_MISMATCH", identity=None):
    return {
        "company_name": "Acme",
        "pipeline_status": "failed",
        "terminal_disposition": "system_gap",
        "identity": identity or _identity(),
        "stages": [
            {
                "stage": "result_validation",
                "status": "failed",
                "reason_code": reason_code,
            }
        ],
    }


class ReplayIdentityContractTests(unittest.TestCase):
    def test_legacy_records_use_the_legacy_gate_when_identity_is_absent(self):
        source = {
            "company_name": "Acme",
            "pipeline_status": "partial",
            "stages": [{
                "stage": "opening_match",
                "status": "partial",
                "reason_code": "OPENING_NOT_FOUND",
            }],
        }

        gate = _build_outcome_gate([{"company_name": "Acme"}], [source], source_records=[source])

        comparison = gate["records"][0]
        self.assertEqual(gate["status"], "passed")
        self.assertEqual(comparison["classification"], "reproduced")
        self.assertEqual(comparison["identity_comparison"], "unavailable")

    def test_identity_system_gap_reproduces_normalized_chain_and_failure_contract(self):
        source = _identity_failure()
        replay = _identity_failure(identity=_identity(conflicts=[" PROVIDER.TENANT "]))

        gate = _build_outcome_gate([{"company_name": "Acme"}], [replay], source_records=[source])

        comparison = gate["records"][0]
        self.assertEqual(gate["status"], "passed")
        self.assertEqual(comparison["classification"], "reproduced")
        self.assertEqual(comparison["identity_comparison"], "available")
        self.assertEqual(comparison["original_identity"], comparison["replay_identity"])

    def test_identity_system_gap_cannot_degrade_to_opening_not_found(self):
        source = _identity_failure()
        replay = _identity_failure(reason_code="OPENING_NOT_FOUND")

        gate = _build_outcome_gate([{"company_name": "Acme"}], [replay], source_records=[source])

        comparison = gate["records"][0]
        self.assertEqual(gate["status"], "failed")
        self.assertEqual(comparison["classification"], "mismatch")
        self.assertEqual(comparison["reason"], "identity_system_gap_degraded")

    def test_identity_transition_requires_dispositions_and_identity_expectation(self):
        source = _identity_failure()
        replay = {**_identity_failure(), "pipeline_status": "partial"}
        replay_input = {
            "company_name": "Acme",
            "source_trace": {"replay": {
                "pipeline_status": "failed",
                "first_non_success_stage": {
                    "stage": "result_validation",
                    "status": "failed",
                    "reason_code": "RESULT_IDENTITY_MISMATCH",
                },
                "expected_transition": {
                    "pipeline_status": "partial",
                    "failure_stage": {
                        "stage": "result_validation",
                        "status": "failed",
                        "reason_code": "RESULT_IDENTITY_MISMATCH",
                    },
                },
            }},
        }

        gate = _build_outcome_gate([replay_input], [replay], source_records=[source])

        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["records"][0]["reason"], "declared_transition_not_met")

        replay_input["source_trace"]["replay"]["expected_transition"].update(
            {
                "old_disposition": "system_gap",
                "new_disposition": "system_gap",
                "identity_expectation": "same",
            }
        )
        gate = _build_outcome_gate([replay_input], [replay], source_records=[source])

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["records"][0]["classification"], "expected_transition")

        changed = {
            **_identity_failure(identity=_identity(codes=["OPENING_TENANT_MISMATCH"])),
            "pipeline_status": "partial",
        }
        gate = _build_outcome_gate([replay_input], [changed], source_records=[source])

        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["records"][0]["reason"], "declared_transition_not_met")


if __name__ == "__main__":
    unittest.main()
