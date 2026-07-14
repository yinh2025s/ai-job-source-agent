import unittest

from job_source_agent.evaluation import (
    EVALUATION_SCHEMA_VERSION,
    summarize_evaluation_metrics,
    validate_evaluation_record,
)


def record(
    disposition,
    eligibility,
    *,
    opening=False,
    identity_verdict="not_applicable",
    s7_status="success",
):
    value = {
        "evaluation": {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "record_disposition": disposition,
            "eligible_exact_opening": eligibility,
            "identity_verdict": identity_verdict,
        },
        "stages": [{"stage": "result_validation", "status": s7_status}],
    }
    if opening:
        value["open_position_url"] = "https://jobs.example.test/role"
    return value


class EvaluationMetricTests(unittest.TestCase):
    def test_metrics_follow_frozen_denominators(self):
        records = [
            record("exact_public", True, opening=True, identity_verdict="verified"),
            record("system_gap", True, opening=True, identity_verdict="rejected"),
            record("verified_closed", False),
            record("external_blocked", "unknown"),
        ]

        metrics = summarize_evaluation_metrics(records)

        self.assertEqual(metrics["raw_exact_rate"]["value"], 0.5)
        self.assertEqual(metrics["exact_precision"]["value"], 0.5)
        self.assertEqual(metrics["conditional_exact_recall"]["value"], 0.5)
        self.assertEqual(metrics["system_defect_rate"]["value"], 0.25)
        self.assertEqual(metrics["system_defect_rate"]["denominator"], 4)

    def test_precision_is_not_reportable_when_any_exact_output_is_unreviewed(self):
        metrics = summarize_evaluation_metrics(
            [
                record("exact_public", True, opening=True, identity_verdict="verified"),
                record("system_gap", True, opening=True, identity_verdict="unreviewed"),
            ]
        )

        self.assertEqual(metrics["exact_precision"]["status"], "not_reportable")
        self.assertIsNone(metrics["exact_precision"]["value"])
        self.assertEqual(metrics["exact_precision"]["unknown_count"], 1)

    def test_eligibility_is_never_inferred(self):
        with self.assertRaisesRegex(ValueError, "eligible_exact_opening"):
            validate_evaluation_record(
                {
                    "evaluation": {
                        "schema_version": EVALUATION_SCHEMA_VERSION,
                        "record_disposition": "system_gap",
                        "identity_verdict": "rejected",
                    }
                }
            )

    def test_s7_failure_with_url_cannot_be_exact_public(self):
        with self.assertRaisesRegex(ValueError, "S7 failure"):
            validate_evaluation_record(
                record(
                    "exact_public",
                    True,
                    opening=True,
                    identity_verdict="verified",
                    s7_status="failed",
                )
            )

    def test_missing_annotations_make_review_metrics_not_reportable(self):
        metrics = summarize_evaluation_metrics(
            [record("no_public_opening", False), {"open_position_url": None}]
        )

        self.assertEqual(metrics["raw_exact_rate"]["status"], "available")
        self.assertEqual(metrics["system_defect_rate"]["status"], "not_reportable")
        self.assertEqual(metrics["annotation_coverage"]["value"], 0.5)


if __name__ == "__main__":
    unittest.main()
