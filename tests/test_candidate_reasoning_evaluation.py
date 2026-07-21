import unittest

from job_source_agent.candidate_reasoning_evaluation import (
    CandidateReasoningABObservation,
    evaluate_candidate_reasoning_ab,
    evaluate_candidate_reasoning_gate,
)


def observation(record_id: str, **overrides):
    values = {
        "eligible_g": True,
        "reference_candidate_url": f"https://jobs.example.test/{record_id}/role",
        "reference_website_url": f"https://{record_id}.example.test/",
        "frozen_search_evidence_urls": (
            f"https://jobs.example.test/{record_id}/role",
            f"https://{record_id}.example.test/",
        ),
        "baseline_top_candidate_urls": (),
        "treatment_top_candidate_urls": (f"https://jobs.example.test/{record_id}/role",),
        "baseline_verified_website_url": None,
        "treatment_verified_website_url": f"https://{record_id}.example.test/",
        "llm_calls": 2,
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "estimated_cost_usd": 0.01,
        "llm_latency_ms": 10.0,
    }
    values.update(overrides)
    return CandidateReasoningABObservation(record_id=record_id, **values)


class CandidateReasoningEvaluationTests(unittest.TestCase):
    def test_aggregate_reports_explicit_denominators_and_promotion_metrics(self):
        records = (
            observation("a", llm_latency_ms=10),
            observation("b", llm_latency_ms=20),
            observation(
                "c",
                baseline_top_candidate_urls=("https://jobs.example.test/c/role",),
                treatment_top_candidate_urls=("https://jobs.example.test/c/role",),
                baseline_verified_website_url="https://c.example.test/",
                treatment_verified_website_url="https://c.example.test/",
                llm_calls=1,
                llm_latency_ms=100,
            ),
            observation("d", llm_latency_ms=40),
        )

        report = evaluate_candidate_reasoning_ab(records)

        self.assertEqual((report.baseline_candidate_recall_at_3.count, report.baseline_candidate_recall_at_3.denominator), (1, 4))
        self.assertEqual((report.treatment_candidate_recall_at_3.count, report.treatment_candidate_recall_at_3.denominator), (4, 4))
        self.assertEqual(report.candidate_recall_delta_percentage_points, 75.0)
        self.assertEqual((report.eligible_g_recovery_fraction.count, report.eligible_g_recovery_fraction.denominator), (3, 4))
        self.assertEqual((report.baseline_verified_website_recall.count, report.treatment_verified_website_recall.count), (1, 4))
        self.assertEqual(report.calls_per_company_mean, 1.75)
        self.assertEqual(report.calls_per_company_max, 2)
        self.assertEqual(report.total_prompt_tokens, 400)
        self.assertEqual(report.total_completion_tokens, 80)
        self.assertEqual(report.total_tokens, 480)
        self.assertAlmostEqual(report.total_estimated_cost_usd, 0.04)
        self.assertAlmostEqual(report.estimated_cost_per_company_mean_usd, 0.01)
        self.assertEqual(report.latency_p50_ms, 20.0)
        self.assertEqual(report.latency_p95_ms, 100.0)
        self.assertTrue(evaluate_candidate_reasoning_gate(report).passed)

    def test_safety_counts_and_gate_failures_are_fail_closed(self):
        record = observation(
            "unsafe",
            treatment_top_candidate_urls=("https://invented.example.test/opening",),
            treatment_verified_website_url="https://wrong.example.test/",
            treatment_cross_company=True,
            treatment_cross_tenant=True,
            replay_mismatch=True,
            llm_calls=3,
            advisory_failure=True,
        )

        report = evaluate_candidate_reasoning_ab((record,))
        gate = evaluate_candidate_reasoning_gate(report)

        self.assertEqual(report.treatment_wrong_verified_url_count, 1)
        self.assertEqual(report.invented_or_modified_treatment_url_count, 1)
        self.assertEqual(report.cross_company_count, 1)
        self.assertEqual(report.cross_tenant_count, 1)
        self.assertEqual(report.replay_mismatch_count, 1)
        self.assertEqual((report.advisory_failure_rate.count, report.advisory_failure_rate.denominator), (1, 1))
        self.assertFalse(gate.passed)
        self.assertEqual(
            set(gate.failures),
            {
                "wrong_verified_urls_nonzero",
                "model_invented_or_modified_urls_nonzero",
                "cross_company_nonzero",
                "cross_tenant_nonzero",
                "replay_mismatch_nonzero",
                "calls_per_company_mean_above_2",
                "calls_per_company_max_above_2",
                "candidate_recall_delta_below_25_percentage_points",
                "eligible_g_recovery_below_40_percent",
            },
        )

    def test_empty_input_fails_closed(self):
        report = evaluate_candidate_reasoning_ab(())
        gate = evaluate_candidate_reasoning_gate(report)

        self.assertEqual(report.record_count, 0)
        self.assertEqual(report.baseline_candidate_recall_at_3.denominator, 0)
        self.assertEqual(report.latency_p50_ms, 0.0)
        self.assertFalse(gate.passed)
        self.assertIn("empty_eligible_g_subset", gate.failures)

    def test_rejects_noneligible_duplicate_and_over_top_three_observations(self):
        with self.assertRaisesRegex(ValueError, "eligible G"):
            observation("not-g", eligible_g=False)
        with self.assertRaisesRegex(ValueError, "exceeds limit 3"):
            observation(
                "too-many",
                treatment_top_candidate_urls=(
                    "https://a.example.test/",
                    "https://b.example.test/",
                    "https://c.example.test/",
                    "https://d.example.test/",
                ),
            )
        record = observation("duplicate")
        with self.assertRaisesRegex(ValueError, "duplicate record_id"):
            evaluate_candidate_reasoning_ab((record, record))

    def test_reference_answers_are_evaluator_only_fields(self):
        fields = CandidateReasoningABObservation.__dataclass_fields__
        self.assertIn("reference_candidate_url", fields)
        self.assertIn("reference_website_url", fields)
        self.assertNotIn("planner_request", fields)
        self.assertNotIn("ranker_request", fields)


if __name__ == "__main__":
    unittest.main()
