from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_observed_annotations import ObservedEvaluationError, evaluate


def _result(company: str, linkedin: str, opening: str | None, *, verified=True):
    return {
        "company_name": company,
        "linkedin_job_url": linkedin,
        "open_position_url": opening,
        "identity_assertion": {"verdict": "verified" if verified else "rejected"},
        "stages": [{"stage": "result_validation", "status": "success" if verified else "failed"}],
    }


class EvaluateObservedAnnotationsTests(unittest.TestCase):
    def test_reports_recovery_wrong_url_and_unreviewed_precision(self):
        linkedin_a = "https://www.linkedin.com/jobs/view/a"
        linkedin_b = "https://www.linkedin.com/jobs/view/b"
        annotations = {
            "manifest": {"annotation_record_count": 2, "cohort_record_count": 3},
            "records": [
                {
                    "company_name": "A",
                    "linkedin_job_url": linkedin_a,
                    "eligible_exact_opening": True,
                    "expected_disposition": "system_gap",
                    "expected_opening_url": "https://jobs.example/a",
                },
                {
                    "company_name": "B",
                    "linkedin_job_url": linkedin_b,
                    "eligible_exact_opening": False,
                    "expected_disposition": "verified_closed",
                    "expected_opening_url": None,
                },
            ],
        }
        results = [
            _result("A", linkedin_a, "https://jobs.example/wrong"),
            _result("B", linkedin_b, "https://jobs.example/b"),
            _result("C", "https://www.linkedin.com/jobs/view/c", None),
        ]

        report = self._evaluate(annotations, results)

        self.assertEqual(report["metrics"]["conditional_exact_recall"]["value"], 1.0)
        self.assertEqual(report["metrics"]["wrong_expected_url_count"], 1)
        self.assertEqual(report["metrics"]["negative_control_exact_requires_review"], 1)
        self.assertEqual(report["metrics"]["exact_precision"]["status"], "not_reportable")
        self.assertEqual(report["metrics"]["unannotated_record_count"], 1)

    def test_rejected_s7_output_is_unsafe_and_not_recovered(self):
        linkedin = "https://www.linkedin.com/jobs/view/a"
        annotations = {
            "manifest": {"annotation_record_count": 1, "cohort_record_count": 1},
            "records": [{
                "company_name": "A",
                "linkedin_job_url": linkedin,
                "eligible_exact_opening": True,
                "expected_disposition": "system_gap",
                "expected_opening_url": None,
            }],
        }

        report = self._evaluate(
            annotations,
            [_result("A", linkedin, "https://jobs.example/a", verified=False)],
        )

        self.assertEqual(report["metrics"]["unsafe_exact_count"], 1)
        self.assertEqual(report["metrics"]["conditional_exact_recall"]["value"], 0.0)

    def test_rejects_missing_annotated_record(self):
        annotations = {
            "manifest": {"annotation_record_count": 1, "cohort_record_count": 1},
            "records": [{
                "company_name": "A",
                "linkedin_job_url": "https://www.linkedin.com/jobs/view/a",
                "eligible_exact_opening": True,
                "expected_disposition": "system_gap",
                "expected_opening_url": None,
            }],
        }
        with self.assertRaisesRegex(ObservedEvaluationError, "missing from results"):
            self._evaluate(
                annotations,
                [_result("B", "https://www.linkedin.com/jobs/view/b", None)],
            )

    def _evaluate(self, annotations, results):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            annotation_path = root / "annotations.json"
            results_path = root / "results.json"
            annotation_path.write_text(json.dumps(annotations), encoding="utf-8")
            results_path.write_text(json.dumps(results), encoding="utf-8")
            return evaluate(annotation_path, results_path)


if __name__ == "__main__":
    unittest.main()
