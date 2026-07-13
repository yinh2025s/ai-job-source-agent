import io
import unittest
from contextlib import redirect_stdout

from scripts.benchmark_eval import print_summary


class BenchmarkEvalTests(unittest.TestCase):
    def test_print_summary_handles_incompatible_baseline(self):
        summary = {
            "total": 1,
            "success": 1,
            "pipeline_status_counts": {"success": 1},
            "with_job_list": 1,
            "with_opening": 1,
            "expectation_checks": {"passed": 1, "total": 1},
            "rates": {"opening": 1.0},
            "provider_counts": {"ashby": 1},
            "regression": {"comparison_status": "no_compatible_baseline"},
        }

        output = io.StringIO()
        with redirect_stdout(output):
            print_summary(summary)

        self.assertIn("baseline_comparison: no_compatible_baseline", output.getvalue())


if __name__ == "__main__":
    unittest.main()
