from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.normalize_observed_annotations import (
    AnnotationNormalizationError,
    normalize_annotations,
)


class NormalizeObservedAnnotationsTests(unittest.TestCase):
    def test_normalizes_reviewed_and_unknown_records(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            payload = normalize_annotations(**fixture)

        self.assertEqual(payload["manifest"]["annotation_record_count"], 2)
        reviewed, pending = payload["records"]
        self.assertEqual(reviewed["expected_disposition"], "system_gap")
        self.assertIs(reviewed["eligible_exact_opening"], True)
        self.assertEqual(
            reviewed["expected_opening_url"], "https://jobs.example.com/a/123"
        )
        self.assertEqual(reviewed["failure_stage"], "opening_match")
        self.assertEqual(reviewed["root_cause"], "OPENING_DISCOVERY_INCOMPLETE")
        self.assertEqual(pending["expected_disposition"], "unknown")
        self.assertEqual(pending["eligible_exact_opening"], "unknown")
        self.assertIsNone(pending["expected_opening_url"])

    def test_rejects_digest_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            fixture["expected_bindings"]["source_results_sha256"] = "0" * 64
            with self.assertRaisesRegex(
                AnnotationNormalizationError, "source_results_sha256 digest"
            ):
                normalize_annotations(**fixture)

    def test_rejects_duplicate_annotation_record(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            raw_path = fixture["raw_annotations_path"]
            content = raw_path.read_text(encoding="utf-8")
            raw_path.write_text(content + "\n" + content.split("## Pending")[0], encoding="utf-8")
            fixture["expected_bindings"]["source_annotation_sha256"] = self._digest(raw_path)
            with self.assertRaisesRegex(AnnotationNormalizationError, "duplicate record"):
                normalize_annotations(**fixture)

    def test_rejects_missing_annotation_record(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            checklist = fixture["checklist_path"]
            content = checklist.read_text(encoding="utf-8")
            checklist.write_text(content.split("## Pending")[0], encoding="utf-8")
            with self.assertRaisesRegex(AnnotationNormalizationError, "cohort mismatch"):
                normalize_annotations(**fixture)

    def test_unreviewed_record_cannot_claim_known_eligibility(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            checklist = fixture["checklist_path"]
            checklist.write_text(
                checklist.read_text(encoding="utf-8")
                + "  - Manual disposition: `system_gap`\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                AnnotationNormalizationError, "unreviewed record.*has a disposition"
            ):
                normalize_annotations(**fixture)

    def _fixture(self, root: Path) -> dict:
        linkedin_a = "https://www.linkedin.com/jobs/view/a-1"
        linkedin_b = "https://www.linkedin.com/jobs/view/b-2"
        checklist = self._write_text(
            root / "checklist.md",
            f"""## Opening Discovery Incomplete
- [x] **A Co** - Engineer; Austin, TX. [LinkedIn]({linkedin_a})
  - Automated evidence: Inventory traversal stopped.
  - Later targeted recovery: verified [exact opening](https://jobs.example.com/a/123).
  - Manual finding: The target was visible.
  - Manual disposition: `system_gap`

## Pending
- [ ] **B Co** - Analyst; Boston, MA. [LinkedIn]({linkedin_b})
  - Automated evidence: Website was not resolved.
  - Manual website / finding:
""",
        )
        raw = self._write_text(
            root / "raw.md",
            f"""## Opening Discovery Incomplete
- [ ] **A Co** - Engineer; Austin, TX. [LinkedIn]({linkedin_a})
  - Automated evidence: Inventory traversal stopped.
  - Manual finding:The target was visible.

## Pending
- [ ] **B Co** - Analyst; Boston, MA. [LinkedIn]({linkedin_b})
  - Automated evidence: Website was not resolved.
  - Manual website / finding:
""",
        )
        results = [
            self._result(
                "A Co", linkedin_a, "Engineer", "Austin, TX",
                "opening_match", "OPENING_DISCOVERY_INCOMPLETE",
            ),
            self._result(
                "B Co", linkedin_b, "Analyst", "Boston, MA",
                "website_resolution", "WEBSITE_NOT_RESOLVED",
            ),
        ]
        results_path = self._write_json(root / "results.json", results)
        trace_path = self._write_json(root / "trace.json", [dict(item) for item in results])
        companies_digest = "a" * 64
        run_digest = "b" * 64
        summary_path = self._write_json(
            root / "summary.json",
            {
                "run_configuration_digest": run_digest,
                "evaluation_manifest": {
                    "companies_sha256": companies_digest,
                    "run_configuration_digest": run_digest,
                },
            },
        )
        return {
            "checklist_path": checklist,
            "raw_annotations_path": raw,
            "results_path": results_path,
            "trace_path": trace_path,
            "summary_path": summary_path,
            "expected_bindings": {
                "source_annotation_sha256": self._digest(raw),
                "source_results_sha256": self._digest(results_path),
                "source_trace_sha256": self._digest(trace_path),
                "source_summary_sha256": self._digest(summary_path),
                "companies_sha256": companies_digest,
                "run_configuration_digest": run_digest,
            },
            "reviewed_at": "2026-07-17T09:00:00+08:00",
        }

    def _result(self, company, linkedin, title, location, stage, reason):
        return {
            "company_name": company,
            "linkedin_job_url": linkedin,
            "linkedin_job_title": title,
            "linkedin_job_location": location,
            "company_website_url": None,
            "career_page_url": None,
            "job_list_page_url": None,
            "open_position_url": None,
            "pipeline_status": "partial",
            "error_code": reason,
            "stages": [
                {"stage": stage, "status": "partial", "reason_code": reason},
            ],
        }

    def _write_json(self, path: Path, payload) -> Path:
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _write_text(self, path: Path, content: str) -> Path:
        path.write_text(content, encoding="utf-8")
        return path

    def _digest(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
