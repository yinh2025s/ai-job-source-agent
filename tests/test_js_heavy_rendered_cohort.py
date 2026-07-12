import unittest

from scripts.js_heavy_cohort_eval import (
    DEFAULT_FIXTURE_ROOT,
    evaluate_saved_cohort,
    load_cases,
    summary_exit_code,
)


class JSHeavyRenderedCohortTests(unittest.TestCase):
    def test_fixed_cohort_has_five_distinct_real_company_boards(self):
        cases = load_cases()

        self.assertEqual(len(cases), 5)
        self.assertEqual(len({case["company"] for case in cases}), 5)
        self.assertEqual(len({case["url"] for case in cases}), 5)
        self.assertTrue(all(case["url"].startswith("https://apply.workable.com/") for case in cases))
        self.assertTrue(
            all(
                case["fixture_provenance"]
                == "minimal_contract_from_live_static_shell_and_public_api"
                for case in cases
            )
        )
        self.assertTrue(all((DEFAULT_FIXTURE_ROOT / case["static_fixture"]).is_file() for case in cases))
        self.assertTrue(all((DEFAULT_FIXTURE_ROOT / case["rendered_fixture"]).is_file() for case in cases))

    def test_all_static_shells_trigger_render_and_rendered_dom_has_exact_job_evidence(self):
        summary = evaluate_saved_cohort()

        self.assertTrue(summary["passed"])
        self.assertEqual(summary["case_count"], 5)
        for row in summary["cases"]:
            with self.subTest(company=row["company"]):
                self.assertIn(
                    row["trigger_reason"],
                    {"static_shell", "static_no_usable_job_links", "javascript_required"},
                )
                self.assertTrue(row["render_triggered"])
                self.assertEqual(row["render_source"], "browser_after_static_shell")
                self.assertTrue(row["job_evidence_found"])

    def test_shared_browser_budget_is_never_exceeded(self):
        summary = evaluate_saved_cohort()

        self.assertEqual(summary["render_budget"], 5)
        self.assertEqual(summary["render_attempts"], 5)
        self.assertTrue(summary["budget_not_exceeded"])
        self.assertEqual(summary["exhausted_request_outcome"], "skipped_budget")

    def test_live_failure_is_nonzero_even_when_budget_is_respected(self):
        failed_live_summary = {
            "mode": "live_browser_smoke",
            "budget_not_exceeded": True,
            "passed": False,
            "cases": [
                {
                    "company": "Example",
                    "render_triggered": True,
                    "career_evidence_found": False,
                }
            ],
        }

        self.assertEqual(summary_exit_code(failed_live_summary), 1)
        self.assertEqual(
            summary_exit_code({"budget_not_exceeded": True, "passed": True}),
            0,
        )
        self.assertEqual(
            summary_exit_code({"budget_not_exceeded": False, "passed": True}),
            1,
        )


if __name__ == "__main__":
    unittest.main()
