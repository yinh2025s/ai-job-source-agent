from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.evaluate_candidate_reasoning_experiment import (
    _decision_artifacts_by_digest,
    _normalize_candidate_for_reference,
    _normalize_candidates_for_reference,
    _strict_causal_recovery,
    _url_hypothesis_promotion_gate,
    _verified_website_conflicts,
)
from scripts.run_candidate_reasoning_experiment import (
    _agent_config,
    _common_config_digest,
    _require_new_root,
    _run_arm,
    _sealed_files,
)


class CandidateReasoningExperimentScriptsTest(unittest.TestCase):
    _DIGEST = "a" * 64

    def _decision_artifact_path(self, directory: Path) -> Path:
        path = directory / "llm-decisions.jsonl"
        planner = {
            "record": {
                "key": {
                    "decision_kind": "query_plan",
                    "input_evidence_digest": self._DIGEST,
                },
                "sanitized_request": {},
                "sanitized_response": {
                    "queries": [
                        {
                            "query": '"Example" careers',
                            "purpose": "official_website",
                        }
                    ],
                    "url_hypotheses": [
                        {
                            "url": "https://example.test/careers",
                            "purpose": "career_site",
                            "confidence": "high",
                        }
                    ],
                },
                "status": "success",
            }
        }
        ranker = {
            "record": {
                "key": {"decision_kind": "candidate_rank"},
                "sanitized_request": {
                    "invocation_input_evidence_digest": self._DIGEST,
                    "candidates": [
                        {
                            "candidate_id": "search-1",
                            "url": "https://search.example.test/result",
                            "source": "resolver-search",
                        },
                        {
                            "candidate_id": "hypothesis-1",
                            "url": "https://example.test/careers",
                            "source": "llm-url-hypothesis",
                        },
                    ],
                },
                "sanitized_response": {
                    "ranked_candidates": [{"candidate_id": "hypothesis-1"}]
                },
                "status": "success",
            }
        }
        path.write_text(
            "\n".join(json.dumps(item) for item in (planner, ranker)) + "\n",
            encoding="utf-8",
        )
        return path

    def _strict_causal(
        self,
        *,
        reference: str,
        hypothesis: dict[str, str],
        treatment_result: dict[str, object],
        selected: dict[str, str],
        stage: str = "website_resolution",
    ) -> dict[str, object]:
        return _strict_causal_recovery(
            reference=reference,
            baseline_result={},
            treatment_result=treatment_result,
            treatment_trace={"trace": {"stages": {stage: {"selected": selected}}}},
            reasoning_record={
                "llm_plan_used": True,
                "llm_hypothesis_used": True,
            },
            frozen_search_urls=("https://search.example.test/independent",),
            url_hypotheses=(hypothesis,),
        )

    def test_decision_artifacts_preserve_planner_hypotheses_and_ranker_source_pool(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = _decision_artifacts_by_digest(
                self._decision_artifact_path(Path(temporary))
            )

        record = artifacts[self._DIGEST]
        self.assertEqual(
            record["url_hypotheses"],
            (
                {
                    "url": "https://example.test/careers",
                    "purpose": "career_site",
                    "confidence": "high",
                },
            ),
        )
        self.assertEqual(record["queries"][0]["purpose"], "official_website")
        # The evaluator needs the source tags to distinguish a frozen search
        # candidate from a model-proposed URL when proving causal recovery.
        self.assertEqual(
            record["ranker_candidates"],
            (
                {
                    "candidate_id": "search-1",
                    "url": "https://search.example.test/result",
                    "source": "resolver-search",
                },
                {
                    "candidate_id": "hypothesis-1",
                    "url": "https://example.test/careers",
                    "source": "llm-url-hypothesis",
                },
            ),
        )

    def test_strict_causal_recovery_requires_real_planner_hypothesis_flags(self):
        result = _strict_causal_recovery(
            reference="https://example.test/",
            baseline_result={},
            treatment_result={"company_website_url": "https://example.test/"},
            treatment_trace={
                "trace": {
                    "stages": {
                        "website_resolution": {
                            "selected": {
                                "url": "https://example.test/",
                                "reason": "candidate reasoning verified candidate",
                            }
                        }
                    }
                }
            },
            reasoning_record={
                "llm_plan_used": False,
                "llm_hypothesis_used": True,
            },
            frozen_search_urls=(),
            url_hypotheses=(
                {
                    "url": "https://example.test/",
                    "purpose": "official_website",
                    "confidence": "high",
                },
            ),
        )

        self.assertFalse(result["strict"])
        self.assertEqual(result["surface"], "none")

    def test_strict_causal_recovery_classifies_website_and_career_hypotheses(self):
        website = self._strict_causal(
            reference="https://example.test/",
            hypothesis={
                "url": "https://example.test/",
                "purpose": "official_website",
                "confidence": "high",
            },
            treatment_result={"company_website_url": "https://example.test/"},
            selected={
                "url": "https://example.test/",
                "reason": "candidate reasoning deterministic verification",
            },
        )
        career = self._strict_causal(
            reference="https://example.test/careers",
            hypothesis={
                "url": "https://example.test/careers",
                "purpose": "career_site",
                "confidence": "high",
            },
            treatment_result={
                "company_website_url": "https://example.test/careers"
            },
            selected={
                "url": "https://example.test/careers",
                "reason": "candidate reasoning deterministic verification",
            },
        )

        self.assertEqual((website["strict"], website["surface"]), (True, "website"))
        self.assertEqual((career["strict"], career["surface"]), (True, "career"))

    def test_strict_causal_recovery_classifies_verified_ats_hypothesis(self):
        result = self._strict_causal(
            reference="https://example.test/",
            hypothesis={
                "url": "https://jobs.ats.test/example",
                "purpose": "provider_site",
                "confidence": "medium",
            },
            treatment_result={
                "open_position_url": "https://jobs.ats.test/example/jobs/123",
                "identity_assertion": {"verdict": "verified"},
            },
            selected={
                "url": "https://jobs.ats.test/example",
                "source_kind": "llm_url_hypothesis",
            },
            stage="job_board_discovery",
        )

        self.assertEqual((result["strict"], result["surface"]), (True, "ats"))
        self.assertEqual(result["evidence"]["identity_verdict"], "verified")

    def test_url_hypothesis_promotion_gate_fails_closed_on_safety_and_budget(self):
        report = SimpleNamespace(record_count=18, calls_per_company_max=2)
        metrics = {
            "candidate_recall_delta_percentage_points": 30.0,
            "strict_causal_recoveries": {"fraction": 0.5},
            "wrong_verified_url_count": 0,
            "invented_or_modified_candidate_url_count": 0,
            "cross_company_count": 0,
            "cross_tenant_count": 0,
            "replay_mismatch_count": 0,
            "replay_fixture_gap_count": 0,
            "calls_per_company_max": 2,
        }
        safe_manifest = {
            "actual_call_count": 30,
            "call_limit": 30,
            "actual_cost_usd": 0.05,
            "hard_cost_cap_usd": 0.05,
        }

        self.assertTrue(
            _url_hypothesis_promotion_gate(
                report=report, metrics=metrics, manifest=safe_manifest
            )["passed"]
        )
        unsafe_metrics = dict(metrics, cross_tenant_count=1, replay_fixture_gap_count=1)
        over_budget = dict(safe_manifest, actual_call_count=31, actual_cost_usd=0.051)
        gate = _url_hypothesis_promotion_gate(
            report=report, metrics=unsafe_metrics, manifest=over_budget
        )

        self.assertFalse(gate["passed"])
        self.assertIn("cross-tenant adoption must remain zero", gate["failures"])
        self.assertIn("replay fixture gap must remain zero", gate["failures"])
        self.assertIn("capture exceeded the call budget", gate["failures"])
        self.assertIn("capture exceeded the cost budget", gate["failures"])

    def test_versioned_phase_budgets_cover_observed_ranker_latency(self):
        treatment = _agent_config(llm=True, model="deepseek-v4-flash")
        self.assertEqual(treatment.llm_planner_timeout, 7.0)
        self.assertEqual(treatment.llm_search_timeout, 4.0)
        self.assertEqual(treatment.llm_ranker_timeout, 7.0)
        self.assertLessEqual(
            treatment.llm_planner_timeout
            + treatment.llm_search_timeout
            + treatment.llm_ranker_timeout,
            treatment.llm_timeout,
        )

    def test_baseline_and_treatment_share_every_non_llm_setting(self):
        baseline = _agent_config(llm=False)
        treatment = _agent_config(llm=True, model="deepseek-v4-flash")

        self.assertEqual(
            _common_config_digest(baseline),
            _common_config_digest(treatment),
        )
        self.assertFalse(baseline.enable_llm_candidate_reasoning)
        self.assertTrue(treatment.enable_llm_candidate_reasoning)
        self.assertEqual(baseline.llm_timeout, 8.0)
        self.assertEqual(treatment.llm_timeout, 18.0)

    def test_experiment_root_must_be_fresh(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fresh"
            _require_new_root(root)
            root.mkdir()
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                _require_new_root(root)

    def test_sealed_file_set_requires_capture_bundle_and_decisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            required = (
                "capture-start.json",
                "cohort.json",
                "baseline/results.json",
                "baseline/trace.json",
                "baseline/company-evidence.json",
                "treatment/results.json",
                "treatment/trace.json",
                "treatment/budget.json",
                "treatment/candidate-records.json",
                "treatment/company-evidence.json",
                "treatment/decisions/llm-decisions.jsonl",
                "treatment/decisions/llm-decision-manifest.json",
                "treatment/query-responses/query.json",
                "treatment/snapshots/page.json",
                "replay/bundle-manifest.json",
            )
            for relative in required:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative, encoding="utf-8")

            files = _sealed_files(root)

            self.assertTrue(set(required).issubset(files))

    def test_evaluator_normalizes_www_and_reference_subtree_after_capture(self):
        self.assertEqual(
            _normalize_candidate_for_reference(
                "https://www.example.com/careers/jobs/123",
                "https://example.com/",
            ),
            "https://example.com/",
        )
        self.assertEqual(
            _normalize_candidate_for_reference(
                "https://www.nyc.gov/site/dss/about/index.page",
                "https://www.nyc.gov/site/dss/",
            ),
            "https://nyc.gov/site/dss",
        )
        self.assertNotEqual(
            _normalize_candidate_for_reference(
                "https://www.nyc.gov/site/doh/index.page",
                "https://www.nyc.gov/site/dss/",
            ),
            "https://nyc.gov/site/dss",
        )

    def test_evaluator_deduplicates_candidates_after_reference_normalization(self):
        self.assertEqual(
            _normalize_candidates_for_reference(
                (
                    "https://www.example.com/careers/jobs/123",
                    "https://example.com/careers/jobs/456",
                    "https://jobs.example.net/opening/1",
                ),
                "https://example.com/",
            ),
            ("https://example.com/", "https://jobs.example.net/opening/1"),
        )

    def test_wrong_company_check_compares_host_not_path(self):
        self.assertFalse(
            _verified_website_conflicts(
                "https://www.example.com/careers", "https://example.com/"
            )
        )
        self.assertTrue(
            _verified_website_conflicts(
                "https://example.net/careers", "https://example.com/"
            )
        )

    def test_run_arm_does_not_use_duration_as_absolute_retry_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "scripts.run_candidate_reasoning_experiment.build_application",
                return_value=SimpleNamespace(pipeline=object()),
            ) as build:
                _run_arm(
                    root=Path(temporary) / "arm",
                    cohort=(),
                    agent_config=_agent_config(llm=False),
                    service_factory=None,
                )

        fetcher_config = build.call_args.args[0]
        self.assertIsNone(fetcher_config.retry_deadline)
        self.assertEqual(fetcher_config.timeout, 5.0)
        self.assertEqual(fetcher_config.retries, 1)


if __name__ == "__main__":
    unittest.main()
