from __future__ import annotations

import unittest

from job_source_agent.candidate_reasoning_policy import (
    CandidateReasoningEligibilityContext,
    evaluate_candidate_reasoning_eligibility,
)


class CandidateReasoningPolicyTest(unittest.TestCase):
    def test_priority_order_is_deterministic(self):
        context = CandidateReasoningEligibilityContext(
            feature_enabled=False,
            has_verified_website=True,
            has_verified_provider_relationship=True,
            identity_state="ambiguous",
            transport_cause="DNS_FAILED",
            has_sufficient_budget=False,
            g_conditions=("NO_SOURCE_BACKED_CANDIDATE",),
        )
        self.assertEqual(evaluate_candidate_reasoning_eligibility(context).state, "DISABLED")
        self.assertEqual(
            evaluate_candidate_reasoning_eligibility(
                CandidateReasoningEligibilityContext(True, has_verified_website=True, transport_cause="DNS_FAILED")
            ).state,
            "VERIFIED_WEBSITE",
        )

    def test_direct_routes_bypass_reasoning(self):
        for changes in (
            {"has_verified_provider_relationship": True},
            {"has_official_external_apply": True},
        ):
            result = evaluate_candidate_reasoning_eligibility(
                CandidateReasoningEligibilityContext(feature_enabled=True, **changes)
            )
            self.assertEqual(result.state, "PROVIDER_BYPASS")
            self.assertFalse(result.eligible)

    def test_forbidden_identity_transport_budget_and_replay(self):
        cases = (
            ({"identity_state": "undisclosed"}, "IDENTITY_FORBIDDEN"),
            ({"transport_cause": "NETWORK_TIMEOUT"}, "TRANSPORT_FORBIDDEN"),
            ({"has_sufficient_budget": False}, "BUDGET_FORBIDDEN"),
            ({"replay_mode": True}, "REPLAY_REQUIRED"),
            ({"later_stage_started": True}, "IDENTITY_FORBIDDEN"),
            ({"has_verified_terminal_decision": True}, "IDENTITY_FORBIDDEN"),
        )
        for changes, expected in cases:
            with self.subTest(changes=changes):
                result = evaluate_candidate_reasoning_eligibility(
                    CandidateReasoningEligibilityContext(feature_enabled=True, **changes)
                )
                self.assertEqual(result.state, expected)
                self.assertFalse(result.eligible)

    def test_only_typed_g_conditions_are_eligible(self):
        result = evaluate_candidate_reasoning_eligibility(
            CandidateReasoningEligibilityContext(
                feature_enabled=True,
                g_conditions=("SAME_NAME_AMBIGUITY", "NAME_VARIANT_UNVERIFIED"),
            )
        )
        self.assertTrue(result.eligible)
        self.assertEqual(result.state, "ELIGIBLE")
        self.assertEqual(result.g_conditions, ("SAME_NAME_AMBIGUITY", "NAME_VARIANT_UNVERIFIED"))
        self.assertEqual(
            evaluate_candidate_reasoning_eligibility(
                CandidateReasoningEligibilityContext(feature_enabled=True)
            ).state,
            "INELIGIBLE",
        )
        with self.assertRaises(ValueError):
            CandidateReasoningEligibilityContext(feature_enabled=True, g_conditions=("trace said maybe",))

    def test_compatible_replay_fixture_can_reach_typed_eligibility(self):
        result = evaluate_candidate_reasoning_eligibility(
            CandidateReasoningEligibilityContext(
                feature_enabled=True,
                replay_mode=True,
                has_compatible_replay_fixture=True,
                g_conditions=("NO_SOURCE_BACKED_CANDIDATE",),
            )
        )
        self.assertTrue(result.eligible)

    def test_replay_requirement_precedes_post_s2_forbidden_state(self):
        result = evaluate_candidate_reasoning_eligibility(
            CandidateReasoningEligibilityContext(
                feature_enabled=True,
                replay_mode=True,
                later_stage_started=True,
                g_conditions=("NO_SOURCE_BACKED_CANDIDATE",),
            )
        )
        self.assertEqual(result.state, "REPLAY_REQUIRED")


if __name__ == "__main__":
    unittest.main()
