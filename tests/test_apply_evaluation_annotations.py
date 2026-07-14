from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.apply_evaluation_annotations import (
    EvaluationAnnotationError,
    apply_annotations,
)


def _result(company: str, opening: str | None) -> dict:
    return {
        "company_name": company,
        "linkedin_job_url": f"https://www.linkedin.com/jobs/view/{company.lower()}",
        "linkedin_job_title": "AI Engineer",
        "open_position_url": opening,
        "identity_assertion": {
            "candidate_opening_url": opening,
        },
        "pipeline_status": "success" if opening else "partial",
        "status": "success" if opening else "partial",
        "stages": [],
    }


class ApplyEvaluationAnnotationsTests(unittest.TestCase):
    def test_binds_complete_review_and_reports_trustworthy_metrics(self):
        results = [
            _result("Exact", "https://jobs.example.com/exact"),
            _result("Gap", None),
        ]
        traces = [dict(record) for record in results]
        manifest = self._manifest(
            results,
            traces,
            [
                self._annotation(
                    results[0],
                    disposition="exact_public",
                    eligibility=True,
                    verdict="verified",
                ),
                self._annotation(
                    results[1],
                    disposition="system_gap",
                    eligibility=True,
                    verdict="not_applicable",
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results_path = self._write(root / "results.json", results)
            trace_path = self._write(root / "trace.json", traces)
            summary_path = self._write(root / "summary.json", {"elapsed_sec": 12.3})
            manifest["source_results_sha256"] = hashlib.sha256(results_path.read_bytes()).hexdigest()
            manifest["source_trace_sha256"] = hashlib.sha256(trace_path.read_bytes()).hexdigest()
            manifest["source_summary_sha256"] = hashlib.sha256(summary_path.read_bytes()).hexdigest()
            annotations_path = self._write(root / "annotations.json", manifest)

            annotated, summary = apply_annotations(
                results_path,
                trace_path,
                summary_path,
                annotations_path,
            )

        self.assertEqual(len(annotated), 2)
        self.assertEqual(summary["evaluation_metrics"]["exact_precision"]["value"], 1.0)
        self.assertEqual(
            summary["evaluation_metrics"]["conditional_exact_recall"]["value"],
            0.5,
        )
        self.assertEqual(summary["evaluation_metrics"]["system_defect_rate"]["value"], 0.5)
        self.assertEqual(summary["review_manifest"]["reviewed_record_count"], 2)
        self.assertEqual(summary["elapsed_sec"], 12.3)

    def test_rejects_opening_identity_drift(self):
        results = [_result("Exact", "https://jobs.example.com/exact")]
        traces = [dict(results[0])]
        annotation = self._annotation(
            results[0],
            disposition="exact_public",
            eligibility=True,
            verdict="verified",
        )
        annotation["expected_open_position_url"] = "https://jobs.example.com/other"
        manifest = self._manifest(results, traces, [annotation])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results_path = self._write(root / "results.json", results)
            trace_path = self._write(root / "trace.json", traces)
            summary_path = self._write(root / "summary.json", {"total": 1})
            manifest["source_results_sha256"] = hashlib.sha256(results_path.read_bytes()).hexdigest()
            manifest["source_trace_sha256"] = hashlib.sha256(trace_path.read_bytes()).hexdigest()
            manifest["source_summary_sha256"] = hashlib.sha256(summary_path.read_bytes()).hexdigest()
            annotations_path = self._write(root / "annotations.json", manifest)

            with self.assertRaisesRegex(EvaluationAnnotationError, "opening URL drift"):
                apply_annotations(
                    results_path,
                    trace_path,
                    summary_path,
                    annotations_path,
                )

    def _manifest(self, results, traces, records):
        return {
            "schema_version": "1.0",
            "cohort_provenance": "frozen_observed",
            "reviewed_at": "2026-07-15",
            "review_method": "independent_manual_official_evidence_review",
            "reviewer": "test-reviewer",
            "source_results_sha256": "0" * 64,
            "source_trace_sha256": "0" * 64,
            "source_summary_sha256": "0" * 64,
            "records": records,
        }

    def _annotation(self, result, *, disposition, eligibility, verdict):
        return {
            "company_name": result["company_name"],
            "linkedin_job_url": result["linkedin_job_url"],
            "linkedin_job_title": result["linkedin_job_title"],
            "expected_open_position_url": result["open_position_url"],
            "expected_candidate_opening_url": result["identity_assertion"]["candidate_opening_url"],
            "record_disposition": disposition,
            "eligible_exact_opening": eligibility,
            "identity_verdict": verdict,
            "evidence": [
                {
                    "kind": "official_public_page",
                    "url": "https://jobs.example.com/evidence",
                    "finding": "reviewed",
                }
            ],
            "review_notes": "Independent test review.",
        }

    def _write(self, path: Path, payload):
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
