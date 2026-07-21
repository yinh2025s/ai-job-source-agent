from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.evaluate_candidate_reasoning_experiment import (
    _normalize_candidate_for_reference,
    _normalize_candidates_for_reference,
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
    def test_baseline_and_treatment_share_every_non_llm_setting(self):
        baseline = _agent_config(llm=False)
        treatment = _agent_config(llm=True, model="deepseek-v4-flash")

        self.assertEqual(
            _common_config_digest(baseline),
            _common_config_digest(treatment),
        )
        self.assertFalse(baseline.enable_llm_candidate_reasoning)
        self.assertTrue(treatment.enable_llm_candidate_reasoning)

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
                "cohort.json",
                "baseline/results.json",
                "baseline/trace.json",
                "treatment/results.json",
                "treatment/trace.json",
                "treatment/candidate-records.json",
                "treatment/decisions/llm-decisions.jsonl",
                "treatment/decisions/llm-decision-manifest.json",
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
