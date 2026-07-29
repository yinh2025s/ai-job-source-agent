import json
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_diagnostic_cohort import (
    DiagnosticCohortError,
    parse_quotas,
    prepare_diagnostic_cohort,
)


def candidate(index: int, keyword: str, *, company: str | None = None) -> dict:
    return {
        "company_name": company or f"Diagnostic Company {index}",
        "linkedin_job_url": (
            f"https://www.linkedin.com/jobs/view/example-role-{9100000 + index}"
        ),
        "linkedin_company_url": (
            f"https://www.linkedin.com/company/diagnostic-{index}"
        ),
        "job_title": f"{keyword} {index}",
        "job_location": "United States",
        "source": "linkedin_public_jobs",
        "source_trace": {
            "blind_candidate_collection": {
                "first_seen_keyword": keyword,
                "matched_keywords": [keyword],
                "evidence_source": "public_search_card",
            }
        },
    }


class PrepareDiagnosticCohortTests(unittest.TestCase):
    def test_freezes_exact_quotas_with_zero_explicit_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pool = [
                candidate(1, "Software Engineer"),
                candidate(2, "Software Engineer"),
                candidate(3, "Registered Nurse"),
                candidate(4, "Registered Nurse"),
                candidate(5, "Registered Nurse"),
            ]
            (root / "pool.json").write_text(json.dumps(pool), encoding="utf-8")
            (root / "prior.json").write_text(
                json.dumps(
                    [
                        {"company_name": "Diagnostic Company 1"},
                        {
                            "linkedin_job_url": (
                                "https://www.linkedin.com/jobs/view/prior-9100003"
                            )
                        },
                    ]
                ),
                encoding="utf-8",
            )

            cohort, manifest = prepare_diagnostic_cohort(
                candidate_paths=[root / "pool.json"],
                excluded_paths=[root / "prior.json"],
                quotas=[("Software Engineer", 1), ("Registered Nurse", 2)],
                cohort_name="v-next-diagnostic",
            )

        self.assertEqual(
            [record["company_name"] for record in cohort],
            ["Diagnostic Company 2", "Diagnostic Company 4", "Diagnostic Company 5"],
        )
        self.assertEqual(
            manifest["role_counts"],
            {"Software Engineer": 1, "Registered Nurse": 2},
        )
        self.assertEqual(manifest["record_count"], 3)
        self.assertEqual(manifest["independent_company_count"], 3)
        self.assertEqual(manifest["unique_linkedin_job_id_count"], 3)
        self.assertEqual(manifest["unique_linkedin_company_slug_count"], 3)
        self.assertEqual(
            manifest["historical_identity_counts"],
            {
                "company_count": 1,
                "linkedin_company_slug_count": 0,
                "linkedin_job_id_count": 1,
            },
        )
        self.assertEqual(
            [row["ordinal"] for row in manifest["records"]],
            [0, 1, 2],
        )
        self.assertEqual(
            [row["role_family"] for row in manifest["records"]],
            ["Software Engineer", "Registered Nurse", "Registered Nurse"],
        )
        self.assertEqual(
            manifest["cohort_provenance"],
            "development_diagnostic_nonsealed",
        )
        self.assertFalse(manifest["s2_s7_executed_during_selection"])
        self.assertEqual(
            manifest["historical_input_policy"],
            {
                "automatic_history_scan": False,
                "explicit_exclusion_paths_only": True,
                "sealed_holdout_access_claimed": False,
            },
        )
        self.assertRegex(manifest["cohort_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(
            all(
                record["source"] == "linkedin_public_jobs_diagnostic"
                for record in cohort
            )
        )

    def test_rejects_answer_prefills(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = candidate(1, "Software Engineer")
            record["external_apply_url"] = "https://jobs.example.test/openings/1"
            (root / "pool.json").write_text(json.dumps([record]), encoding="utf-8")
            with self.assertRaisesRegex(
                DiagnosticCohortError, "answer prefills"
            ):
                prepare_diagnostic_cohort(
                    candidate_paths=[root / "pool.json"],
                    excluded_paths=[],
                    quotas=[("Software Engineer", 1)],
                    cohort_name="diagnostic",
                )

    def test_fails_closed_on_quota_shortfall(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pool.json").write_text(
                json.dumps([candidate(1, "Software Engineer")]),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(DiagnosticCohortError, "quota shortfall"):
                prepare_diagnostic_cohort(
                    candidate_paths=[root / "pool.json"],
                    excluded_paths=[],
                    quotas=[("Software Engineer", 2)],
                    cohort_name="diagnostic",
                )

    def test_duplicate_company_cannot_fill_two_role_quotas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pool.json").write_text(
                json.dumps(
                    [
                        candidate(1, "Software Engineer", company="Same Company"),
                        candidate(2, "Registered Nurse", company="Same Company"),
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(DiagnosticCohortError, "quota shortfall"):
                prepare_diagnostic_cohort(
                    candidate_paths=[root / "pool.json"],
                    excluded_paths=[],
                    quotas=[("Software Engineer", 1), ("Registered Nurse", 1)],
                    cohort_name="diagnostic",
                )

    def test_duplicate_linkedin_company_slug_cannot_fill_two_quotas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = candidate(1, "Software Engineer", company="First Brand")
            second = candidate(2, "Registered Nurse", company="Second Brand")
            second["linkedin_company_url"] = first["linkedin_company_url"]
            (root / "pool.json").write_text(
                json.dumps([first, second]),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(DiagnosticCohortError, "quota shortfall"):
                prepare_diagnostic_cohort(
                    candidate_paths=[root / "pool.json"],
                    excluded_paths=[],
                    quotas=[("Software Engineer", 1), ("Registered Nurse", 1)],
                    cohort_name="diagnostic",
                )

    def test_parses_unique_positive_quotas(self):
        self.assertEqual(
            parse_quotas(["Software Engineer=3", "Registered Nurse=2"]),
            [("Software Engineer", 3), ("Registered Nurse", 2)],
        )
        with self.assertRaises(DiagnosticCohortError):
            parse_quotas(["Software Engineer=1", "software engineer=2"])
        with self.assertRaises(DiagnosticCohortError):
            parse_quotas(["Software Engineer=0"])


if __name__ == "__main__":
    unittest.main()
