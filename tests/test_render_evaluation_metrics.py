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


if __name__ == "__main__":
    unittest.main()
