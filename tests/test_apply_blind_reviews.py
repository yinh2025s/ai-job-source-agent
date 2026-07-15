import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.apply_blind_reviews import BlindReviewError, apply_reviews
from scripts.build_blind_review_templates import _codex_record, _human_record
from scripts.blind_review_contract import BlindChainError, _canonical_json_bytes, verify_execution_chain


class ApplyBlindReviewsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.result = {
            "company_name": "Example",
            "linkedin_job_url": "https://www.linkedin.com/jobs/view/example-9000001",
            "linkedin_job_title": "AI Engineer",
            "linkedin_job_location": "Remote",
            "open_position_url": "https://jobs.example.com/example/jobs/1",
            "candidate_open_position_url": "https://jobs.example.com/example/jobs/1",
            "job_list_page_url": "https://jobs.example.com/example",
            "provider": "example",
            "pipeline_status": "success",
            "status": "success",
            "stages": [],
        }
        self._write("results.json", [self.result])
        self._write("trace.json", [self.result])
        self._write("summary.json", {"total": 1})
        cohort = [{
            "company_name": "Example",
            "linkedin_job_url": self.result["linkedin_job_url"],
            "job_title": "AI Engineer",
            "job_location": "Remote",
        }]
        self._write("cohort.json", cohort)
        cohort_sha = hashlib.sha256(_canonical_json_bytes(cohort)).hexdigest()
        holdout = {
            "cohort_sha256": cohort_sha,
            "records": [{
                "company_name": "Example", "linkedin_job_url": self.result["linkedin_job_url"],
                "job_title": "AI Engineer", "job_location": "Remote",
            }],
        }
        self._write("holdout.json", holdout)
        execution = {
            "status": "complete", "live_execution_count": 1,
            "cohort_provenance_before_execution": "blind_unseen",
            "cohort_provenance_after_execution": "blind_observed",
            "run_id": "run-1", "cohort_sha256": cohort_sha,
            "holdout_manifest_sha256": hashlib.sha256((self.root / "holdout.json").read_bytes()).hexdigest(),
            "artifact_sha256": {
                name: hashlib.sha256((self.root / f"{name}.json").read_bytes()).hexdigest()
                for name in ("results", "trace", "summary")
            },
        }
        self._write("execution.json", execution)
        self.provenance = verify_execution_chain(
            results_path=self.root / "results.json", trace_path=self.root / "trace.json",
            summary_path=self.root / "summary.json", cohort_path=self.root / "cohort.json",
            holdout_manifest_path=self.root / "holdout.json",
            execution_manifest_path=self.root / "execution.json",
        )[3]

    def tearDown(self):
        self.directory.cleanup()

    def test_human_manifest_alone_controls_reportable_metrics(self):
        codex_record = _codex_record(self.result)
        codex_record.update({
            "suggested_record_disposition": "system_gap",
            "suggested_eligible_exact_opening": True,
            "suggested_identity_verdict": "rejected",
            "evidence": [self._evidence()],
            "review_notes": "Codex suggestion differs.",
        })
        codex = self._manifest("codex_artifact", [codex_record])
        self._write("codex.json", codex)
        human_record = _human_record(self.result)
        human_record.update({
            "hiring_entity_name": "Example",
            "hiring_relationship": "same_entity",
            "hiring_relationship_verdict": "verified",
            "provider": "example",
            "provider_tenant": "example",
            "canonical_board_url": "https://jobs.example.com/example",
            "provider_tenant_verdict": "verified",
            "observed_opening_title": "AI Engineer",
            "title_verdict": "exact",
            "observed_opening_location": "Remote",
            "location_verdict": "compatible_remote",
            "accessibility_verdict": "publicly_accessible",
            "accessibility_checked_at": "2026-07-15T02:00:00+00:00",
            "record_disposition": "exact_public",
            "eligible_exact_opening": True,
            "identity_verdict": "verified",
            "evidence": self._exact_evidence(),
            "review_notes": "Human verified all dimensions.",
        })
        human = self._manifest("user_human", [human_record])
        self._write("human.json", human)

        with patch("scripts.apply_blind_reviews.verify_ssh_signature"):
            _traces, summary = apply_reviews(
                self.root / "results.json", self.root / "trace.json", self.root / "summary.json",
                self.root / "cohort.json", self.root / "holdout.json", self.root / "execution.json",
                self.root / "codex.json", self.root / "human.json", self.root / "human.sig",
                self.root / "allowed_signers", "Human Reviewer",
            )

        self.assertEqual(summary["evaluation_metrics"]["exact_precision"]["value"], 1.0)
        self.assertEqual(summary["review_manifests"]["metrics_authority"], "user_human")

    def test_exact_public_rejects_missing_manual_location_verification(self):
        codex_record = _codex_record(self.result)
        codex_record.update({
            "suggested_record_disposition": "exact_public",
            "suggested_eligible_exact_opening": True,
            "suggested_identity_verdict": "verified",
            "evidence": [self._evidence()], "review_notes": "suggestion",
        })
        codex = self._manifest("codex_artifact", [codex_record])
        self._write("codex.json", codex)
        human_record = _human_record(self.result)
        human_record.update({
            "hiring_entity_name": "Example", "hiring_relationship": "same_entity",
            "hiring_relationship_verdict": "verified", "provider": "example",
            "provider_tenant": "example", "canonical_board_url": "https://jobs.example.com/example",
            "provider_tenant_verdict": "verified", "observed_opening_title": "AI Engineer",
            "title_verdict": "exact", "observed_opening_location": None,
            "location_verdict": "unknown", "accessibility_verdict": "publicly_accessible",
            "accessibility_checked_at": "2026-07-15T02:00:00+00:00",
            "record_disposition": "exact_public", "eligible_exact_opening": True,
            "identity_verdict": "verified", "evidence": self._exact_evidence(), "review_notes": "human",
        })
        human = self._manifest("user_human", [human_record])
        self._write("human.json", human)

        with patch("scripts.apply_blind_reviews.verify_ssh_signature"):
            with self.assertRaisesRegex(BlindReviewError, "location was not manually verified"):
                apply_reviews(
                    self.root / "results.json", self.root / "trace.json", self.root / "summary.json",
                    self.root / "cohort.json", self.root / "holdout.json", self.root / "execution.json",
                    self.root / "codex.json", self.root / "human.json", self.root / "human.sig",
                    self.root / "allowed_signers", "Human Reviewer",
                )

    def test_execution_chain_rejects_result_trace_url_drift(self):
        drifted = dict(self.result)
        drifted["open_position_url"] = "https://jobs.example.com/example/jobs/other"
        self._write("trace.json", [drifted])
        execution = json.loads((self.root / "execution.json").read_text(encoding="utf-8"))
        execution["artifact_sha256"]["trace"] = hashlib.sha256(
            (self.root / "trace.json").read_bytes()
        ).hexdigest()
        self._write("execution.json", execution)
        with self.assertRaisesRegex(BlindChainError, "semantic drift"):
            verify_execution_chain(
                results_path=self.root / "results.json", trace_path=self.root / "trace.json",
                summary_path=self.root / "summary.json", cohort_path=self.root / "cohort.json",
                holdout_manifest_path=self.root / "holdout.json",
                execution_manifest_path=self.root / "execution.json",
            )

    def _manifest(self, role, records):
        manifest = {
            "schema_version": "2.0",
            "review_type": role, "reviewer_id": "Human Reviewer" if role == "user_human" else "Codex artifact review",
            "reviewed_at": "2026-07-15T02:00:00+00:00", "records": records,
        }
        manifest.update(self.provenance)
        return manifest

    def _write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding="utf-8")

    @staticmethod
    def _evidence():
        return {"kind": "official_opening", "url": "https://jobs.example.com/example/jobs/1", "finding": "Verified."}

    @staticmethod
    def _exact_evidence():
        return [
            {"kind": "official_public_opening", "url": "https://jobs.example.com/example/jobs/1", "finding": "Opening verified."},
            {"kind": "official_job_board", "url": "https://jobs.example.com/example", "finding": "Board verified."},
            {"kind": "hiring_entity_identity", "url": "https://jobs.example.com/example/jobs/1", "finding": "Hiring entity verified."},
        ]


if __name__ == "__main__":
    unittest.main()
