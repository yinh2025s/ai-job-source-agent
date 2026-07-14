import unittest

from scripts.render_summary_report import render_markdown_report


class RenderEvaluationMetricTests(unittest.TestCase):
    def test_absent_metrics_render_unavailable_not_zero(self):
        report = render_markdown_report({})

        self.assertIn(
            "| exact_precision | unavailable | - | - | - | unavailable |",
            report,
        )
        self.assertNotIn("| exact_precision | 0.0%", report)

    def test_not_reportable_metric_does_not_render_a_percentage(self):
        report = render_markdown_report(
            {
                "evaluation_metrics": {
                    "exact_precision": {
                        "value": None,
                        "numerator": 1,
                        "denominator": 1,
                        "unknown_count": 1,
                        "status": "not_reportable",
                    }
                }
            }
        )

        self.assertIn(
            "| exact_precision | not_reportable | 1 | 1 | 1 | not_reportable |",
            report,
        )

    def test_renders_independent_review_provenance_and_dispositions(self):
        report = render_markdown_report(
            {
                "review_manifest": {
                    "cohort_provenance": "frozen_observed",
                    "review_method": "independent_manual_official_evidence_review",
                    "reviewer": "reviewer-1",
                    "reviewed_at": "2026-07-15",
                    "reviewed_record_count": 30,
                },
                "record_disposition_counts": {
                    "exact_public": 19,
                    "system_gap": 7,
                },
            }
        )

        self.assertIn("## Independent Review", report)
        self.assertIn("| Cohort provenance | frozen_observed |", report)
        self.assertIn("## Record Dispositions", report)
        self.assertIn("| exact_public | 19 |", report)


if __name__ == "__main__":
    unittest.main()
