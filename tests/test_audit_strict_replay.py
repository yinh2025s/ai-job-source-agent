import unittest

from scripts.audit_strict_replay import (
    StrictReplayAuditError,
    audit_strict_replay,
)


def manifest(count: int) -> dict:
    return {
        "status": "success",
        "record_integrity": {
            "status": "passed",
            "counts": {
                "comparison_count": count,
                "export_attempted_count": count,
                "exported_count": count,
                "filter_matched_count": count,
                "limit_omitted_count": 0,
                "replayability_dropped_count": 0,
                "result_count": count,
                "selected_count": count,
                "source_result_count": count,
                "trace_count": count,
            },
        },
        "outcome_gate": {
            "status": "passed",
            "classification_counts": {
                "budget_recovery": 0,
                "expected_transition": 0,
                "fixture_gap": 0,
                "mismatch": 0,
                "reproduced": count,
            },
        },
    }


class AuditStrictReplayTests(unittest.TestCase):
    def test_accepts_only_full_reproduced_replay(self):
        report = audit_strict_replay(manifest(3), expected_records=3)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["issues"], [])

    def test_budget_recovery_is_a_strict_failure(self):
        payload = manifest(3)
        payload["outcome_gate"]["classification_counts"]["reproduced"] = 2
        payload["outcome_gate"]["classification_counts"]["budget_recovery"] = 1
        report = audit_strict_replay(payload, expected_records=3)
        self.assertEqual(report["status"], "failed")
        self.assertIn("budget_recovery_nonzero", report["issues"])
        self.assertIn("reproduced_count_mismatch", report["issues"])

    def test_integrity_count_mismatch_is_rejected(self):
        payload = manifest(3)
        payload["record_integrity"]["counts"]["exported_count"] = 2
        report = audit_strict_replay(payload, expected_records=3)
        self.assertIn("exported_count_mismatch", report["issues"])

    def test_invalid_inputs_fail_closed(self):
        with self.assertRaises(StrictReplayAuditError):
            audit_strict_replay([], expected_records=3)
        with self.assertRaises(StrictReplayAuditError):
            audit_strict_replay({}, expected_records=0)


if __name__ == "__main__":
    unittest.main()
