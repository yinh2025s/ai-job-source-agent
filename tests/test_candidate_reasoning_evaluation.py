import unittest

from job_source_agent.candidate_reasoning_evaluation import (
    CandidateReasoningABObservation,
    FrozenCandidate,
    FrozenPlannerCausalABObservation,
    FrozenRankerCausalABObservation,
    evaluate_candidate_reasoning_ab,
    evaluate_candidate_reasoning_gate,
    evaluate_frozen_planner_causal_ab,
    evaluate_frozen_ranker_causal_ab,
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
        "llm_plan_used": True,
        "llm_causal_contribution": "planner_source_recovery",
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
                llm_plan_used=False,
                llm_causal_contribution="none",
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
            llm_plan_used=False,
            llm_causal_contribution="none",
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
        self.assertEqual(fields["frozen_llm_hypothesis_urls"].default, ())
        self.assertNotIn("planner_request", fields)
        self.assertNotIn("ranker_request", fields)

    def test_url_hypothesis_recovery_is_audited_without_counting_as_invented(self):
        hypothesis_url = "https://jobs.example.test/hypothesis/role"
        record = observation(
            "hypothesis",
            reference_candidate_url=hypothesis_url,
            frozen_search_evidence_urls=("https://search.example.test/result",),
            frozen_llm_hypothesis_urls=(hypothesis_url,),
            treatment_top_candidate_urls=(hypothesis_url,),
            llm_plan_used=True,
            llm_causal_contribution="url_hypothesis_recovery",
        )

        report = evaluate_candidate_reasoning_ab((record,))

        self.assertTrue(record.has_valid_causal_recovery)
        self.assertEqual(report.url_hypothesis_recoveries.count, 1)
        self.assertEqual(report.url_hypothesis_recoveries.denominator, 1)
        self.assertEqual(report.invented_or_modified_treatment_url_count, 0)

    def test_url_hypothesis_recovery_requires_plan_and_frozen_hypothesis_pool(self):
        hypothesis_url = "https://jobs.example.test/hypothesis/role"
        with self.assertRaisesRegex(ValueError, "requires llm_plan_used"):
            observation(
                "hypothesis-no-plan",
                reference_candidate_url=hypothesis_url,
                frozen_search_evidence_urls=(),
                frozen_llm_hypothesis_urls=(hypothesis_url,),
                treatment_top_candidate_urls=(hypothesis_url,),
                llm_plan_used=False,
                llm_causal_contribution="url_hypothesis_recovery",
            )
        with self.assertRaisesRegex(ValueError, "frozen hypothesis URL pool"):
            observation(
                "hypothesis-not-frozen",
                reference_candidate_url=hypothesis_url,
                frozen_search_evidence_urls=(),
                frozen_llm_hypothesis_urls=(),
                treatment_top_candidate_urls=(hypothesis_url,),
                llm_plan_used=True,
                llm_causal_contribution="url_hypothesis_recovery",
            )

    def test_unfrozen_treatment_url_remains_invented_or_modified(self):
        record = observation(
            "unfrozen",
            frozen_search_evidence_urls=(),
            frozen_llm_hypothesis_urls=("https://allowed.example.test/",),
            treatment_top_candidate_urls=("https://unfrozen.example.test/",),
            llm_plan_used=False,
            llm_causal_contribution="none",
        )

        report = evaluate_candidate_reasoning_ab((record,))

        self.assertEqual(report.url_hypothesis_recoveries.count, 0)
        self.assertEqual(report.invented_or_modified_treatment_url_count, 1)

    def test_zero_llm_calls_and_network_variance_do_not_count_as_causal_recovery(self):
        network_variance = observation(
            "network-only",
            llm_calls=0,
            prompt_tokens=0,
            completion_tokens=0,
            estimated_cost_usd=0.0,
            llm_latency_ms=0.0,
            llm_plan_used=False,
            llm_causal_contribution="none",
        )

        report = evaluate_candidate_reasoning_ab((network_variance,))

        self.assertTrue(network_variance.treatment_recovers_g)
        self.assertFalse(network_variance.has_valid_causal_recovery)
        self.assertEqual(report.eligible_g_recovery_fraction.count, 0)
        with self.assertRaisesRegex(ValueError, "llm_calls=0"):
            observation(
                "zero-call-uplift",
                llm_calls=0,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost_usd=0.0,
                llm_latency_ms=0.0,
            )

    def test_frozen_planner_metrics_measure_source_and_causal_recovery(self):
        pool = frozen_pool("planner")
        record = FrozenPlannerCausalABObservation(
            record_id="planner",
            candidate_pool=pool,
            reference_candidate_id="target",
            deterministic_source_candidate_ids=("other",),
            llm_source_candidate_ids=("target", "other"),
            deterministic_top_candidate_ids=("other",),
            llm_top_candidate_ids=("target",),
            llm_structured_output_success=True,
            llm_calls=1,
            prompt_tokens=10,
            completion_tokens=2,
            estimated_cost_usd=0.01,
            llm_latency_ms=5.0,
            verified_website_hit=True,
        )

        report = evaluate_frozen_planner_causal_ab((record,))

        self.assertEqual(report.structured_output_success.count, 1)
        self.assertEqual(report.deterministic_source_candidate_recall_at_10.count, 0)
        self.assertEqual(report.llm_source_candidate_recall_at_10.count, 1)
        self.assertEqual(report.llm_end_to_end_candidate_recall_at_3.count, 1)
        self.assertEqual(report.true_causal_recoveries.count, 1)

    def test_frozen_ranker_uses_only_reference_in_pool_for_conditional_recall(self):
        in_pool = FrozenRankerCausalABObservation(
            record_id="in-pool",
            candidate_pool=frozen_pool("in-pool"),
            reference_candidate_id="target",
            deterministic_top_candidate_ids=("other",),
            llm_top_candidate_ids=("target",),
            llm_rank_invocation_success=True,
            llm_fallback_used=False,
            verified_website_hit=True,
            true_causal_recovery=True,
            llm_calls=1,
        )
        outside_pool = FrozenRankerCausalABObservation(
            record_id="outside-pool",
            candidate_pool=frozen_pool("outside-pool"),
            reference_candidate_id="missing-reference",
            deterministic_top_candidate_ids=("other",),
            llm_top_candidate_ids=("target",),
            llm_rank_invocation_success=False,
            llm_fallback_used=True,
            llm_calls=1,
        )

        report = evaluate_frozen_ranker_causal_ab((in_pool, outside_pool))

        self.assertEqual(report.conditional_record_count, 1)
        self.assertEqual(
            (report.deterministic_conditional_recall_at_3.count,
             report.deterministic_conditional_recall_at_3.denominator),
            (0, 1),
        )
        self.assertEqual(report.llm_conditional_recall_at_3.count, 1)
        self.assertEqual(report.rank_invocation_success.count, 1)
        self.assertEqual(report.fallback_count, 1)
        self.assertEqual(report.true_causal_recoveries.count, 1)

    def test_frozen_pool_enforces_pool_ids_and_top_k_limits(self):
        with self.assertRaisesRegex(ValueError, "cannot also use fallback"):
            FrozenRankerCausalABObservation(
                record_id="success-and-fallback",
                candidate_pool=frozen_pool("success-and-fallback"),
                reference_candidate_id="target",
                deterministic_top_candidate_ids=("other",),
                llm_top_candidate_ids=("target",),
                llm_rank_invocation_success=True,
                llm_fallback_used=True,
                llm_calls=1,
            )
        with self.assertRaisesRegex(ValueError, "outside the frozen pool"):
            FrozenRankerCausalABObservation(
                record_id="wrong-id",
                candidate_pool=frozen_pool("wrong-id"),
                reference_candidate_id="target",
                deterministic_top_candidate_ids=("other",),
                llm_top_candidate_ids=("not-in-pool",),
                llm_rank_invocation_success=True,
                llm_fallback_used=False,
                llm_calls=1,
            )
        with self.assertRaisesRegex(ValueError, "exceeds limit 3"):
            FrozenPlannerCausalABObservation(
                record_id="too-many-top",
                candidate_pool=frozen_pool("too-many-top", count=4),
                reference_candidate_id="target",
                deterministic_source_candidate_ids=("target",),
                llm_source_candidate_ids=("target",),
                deterministic_top_candidate_ids=("target", "other", "third", "fourth"),
                llm_top_candidate_ids=("target",),
                llm_structured_output_success=True,
                llm_calls=1,
            )


def frozen_pool(record_id: str, count: int = 2):
    identifiers = ("target", "other", "third", "fourth")[:count]
    return tuple(
        FrozenCandidate(candidate_id=identifier, url=f"https://{record_id}.example.test/{identifier}")
        for identifier in identifiers
    )


if __name__ == "__main__":
    unittest.main()
