import unittest

from job_source_agent.web import Page
from scripts.js_heavy_cohort_eval import (
    DEFAULT_FIXTURE_ROOT,
    _case_pass,
    _classify_error,
    cohort_diversity,
    evaluate_page_evidence,
    evaluate_saved_cohort,
    load_cases,
    summary_exit_code,
)


class JSHeavyRenderedCohortTests(unittest.TestCase):
    def test_fixed_cohort_has_five_distinct_real_company_boards_and_honest_provenance(self):
        cases = load_cases()

        self.assertEqual(len(cases), 5)
        self.assertEqual(len({case["company"] for case in cases}), 5)
        self.assertEqual(len({case["url"] for case in cases}), 5)
        self.assertGreaterEqual(len({case["provider"] for case in cases}), 3)
        self.assertGreaterEqual(len({case["technology"] for case in cases}), 3)
        for case in cases:
            provenance = case["fixture_provenance"]
            self.assertEqual(provenance["capture_kind"], "sanitized_minimal_live_capture")
            self.assertFalse(provenance["complete"])
            self.assertIn("2026-07-12", provenance["captured_at"])
            self.assertEqual(provenance["rendered_source"], "playwright_chrome_12s")
            self.assertIn(case["evidence_selector"], {"h1", "h2", "h3", "nav"})
        self.assertTrue(all((DEFAULT_FIXTURE_ROOT / case["static_fixture"]).is_file() for case in cases))
        self.assertTrue(all((DEFAULT_FIXTURE_ROOT / case["rendered_fixture"]).is_file() for case in cases))

    def test_saved_and_live_gate_exposes_honest_per_case_evidence_diagnostics(self):
        summary = evaluate_saved_cohort()

        self.assertFalse(summary["passed"])
        self.assertEqual(summary["case_count"], 5)
        self.assertGreaterEqual(summary["provider_count"], 3)
        self.assertGreaterEqual(summary["technology_count"], 3)
        self.assertTrue(summary["diversity_passed"])
        expected = {
            "Plum": True,
            "Workable": True,
            "Apple": True,
            "Intuitive Apps": False,
            "BlueFit": False,
        }
        for row in summary["cases"]:
            with self.subTest(company=row["company"]):
                self.assertIn(
                    row["trigger_reason"],
                    {"static_shell", "static_no_usable_job_links", "javascript_required"},
                )
                self.assertTrue(row["render_triggered"])
                self.assertEqual(row["render_source"], "browser_after_static_shell")
                self.assertEqual(row["render_outcome"], "success")
                self.assertEqual(row["passed"], expected[row["company"]])
                self.assertEqual(row["career_job_evidence_found"], expected[row["company"]])
                self.assertGreater(row["visible_text_length"], 0)
                self.assertFalse(row["post_fetch_wait_supported"])
                self.assertEqual(row["evidence_timing"], "fetcher_return_snapshot")

        intuitive = next(row for row in summary["cases"] if row["company"] == "Intuitive Apps")
        self.assertEqual(intuitive["forbidden_evidence_matches"], ["Loading jobs..."])
        bluefit = next(row for row in summary["cases"] if row["company"] == "BlueFit")
        self.assertEqual(bluefit["evidence_text_matches"], [])

    def test_evidence_gate_requires_configured_url_and_structured_text_without_forbidden_state(self):
        case = {
            "evidence_text": "Open Roles",
            "evidence_selector": "h2",
            "evidence_url": "https://jobs.example.test/jobs/123",
            "forbidden_text": ["Loading jobs..."],
            "minimum_visible_text_length": 10,
        }
        text_only = evaluate_page_evidence(
            Page(
                url="https://jobs.example.test",
                html="<h2>Open Roles</h2><p>Ready now</p>",
            ),
            case,
        )
        loading = evaluate_page_evidence(
            Page(
                url="https://jobs.example.test",
                html=(
                    '<h2>Open Roles</h2><p>Loading jobs...</p>'
                    '<a href="/jobs/123">Role</a>'
                ),
            ),
            case,
        )
        complete = evaluate_page_evidence(
            Page(
                url="https://jobs.example.test",
                html=(
                    '<h2>Open Roles</h2><p>Current openings</p>'
                    '<a href="/jobs/123">Role</a>'
                ),
            ),
            case,
        )

        self.assertTrue(text_only["text_evidence_found"])
        self.assertFalse(text_only["url_evidence_found"])
        self.assertFalse(text_only["career_job_evidence_found"])
        self.assertEqual(loading["forbidden_evidence_matches"], ["Loading jobs..."])
        self.assertFalse(loading["career_job_evidence_found"])
        self.assertTrue(complete["career_job_evidence_found"])

    def test_case_pass_requires_successful_render_and_no_error_class(self):
        base = {
            "trigger_reason": "static_shell",
            "render_triggered": True,
            "render_outcome": "success",
            "career_job_evidence_found": True,
            "error_class": None,
        }

        self.assertTrue(_case_pass(base))
        self.assertFalse(_case_pass({**base, "render_outcome": "failed"}))
        self.assertFalse(_case_pass({**base, "error_class": "timeout"}))
        self.assertEqual(_classify_error(TimeoutError("timed out")), "timeout")
        self.assertEqual(
            _classify_error(RuntimeError("Playwright is not installed")),
            "browser_unavailable",
        )

    def test_shared_browser_budget_is_never_exceeded(self):
        summary = evaluate_saved_cohort()

        self.assertEqual(summary["render_budget"], 5)
        self.assertEqual(summary["render_attempts"], 5)
        self.assertTrue(summary["budget_not_exceeded"])
        self.assertEqual(summary["exhausted_request_outcome"], "skipped_budget")

    def test_diversity_gate_rejects_fewer_than_three_provider_or_technology_types(self):
        cases = [
            {"provider": "one", "technology": "same"},
            {"provider": "two", "technology": "same"},
            {"provider": "two", "technology": "same"},
        ]

        diversity = cohort_diversity(cases)

        self.assertEqual(diversity["provider_count"], 2)
        self.assertEqual(diversity["technology_count"], 1)
        self.assertFalse(diversity["diversity_passed"])

    def test_live_failure_is_nonzero_even_when_budget_is_respected(self):
        failed_live_summary = {
            "mode": "live_browser_smoke",
            "budget_not_exceeded": True,
            "passed": False,
            "cases": [
                {
                    "company": "Example",
                    "render_triggered": True,
                    "career_job_evidence_found": False,
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
