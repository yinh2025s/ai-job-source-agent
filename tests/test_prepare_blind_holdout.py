import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.prepare_blind_holdout import BlindHoldoutError, prepare_holdout


def candidate(index):
    return {
        "company_name": f"Unseen Company {index}",
        "linkedin_job_url": f"https://www.linkedin.com/jobs/view/role-{9000000 + index}",
        "linkedin_company_url": f"https://www.linkedin.com/company/unseen-{index}",
        "job_title": "AI Engineer",
        "job_location": "United States",
    }


class PrepareBlindHoldoutTests(unittest.TestCase):
    def test_freezes_only_unseen_unique_companies_and_records_digests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history"
            history.mkdir()
            (history / "old.json").write_text(
                json.dumps(
                    [{
                        "company_name": "Unseen Company 1",
                        "linkedin_job_url": "https://www.linkedin.com/jobs/view/old-9000002",
                    }]
                ),
                encoding="utf-8",
            )
            candidates_path = root / "pool.json"
            candidates_path.write_text(
                json.dumps([candidate(index) for index in range(40)]),
                encoding="utf-8",
            )
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"workers": 1}), encoding="utf-8")

            with patch(
                "scripts.prepare_blind_holdout._git_evidence",
                return_value={"head": "abc123", "tree": "tree123", "history_bytes": b""},
            ):
                cohort, manifest = prepare_holdout(
                    candidates_path=candidates_path,
                    repo_root=history,
                    history_roots=[],
                    history_cutoff="2999-01-01T00:00:00+00:00",
                    run_config_path=config_path,
                    limit=30,
                    excluded_paths={candidates_path},
                )

        self.assertEqual(len(cohort), 30)
        self.assertNotIn("Unseen Company 1", {item["company_name"] for item in cohort})
        self.assertNotIn("Unseen Company 2", {item["company_name"] for item in cohort})
        self.assertEqual(manifest["cohort_provenance"], "blind_unseen")
        self.assertEqual(manifest["selection"]["post_selection_overlap_count"], 0)
        self.assertRegex(manifest["cohort_sha256"], r"^[0-9a-f]{64}$")

    def test_requires_thirty_to_fifty_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pool.json").write_text("[]", encoding="utf-8")
            (root / "config.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(BlindHoldoutError, "between 30 and 50"):
                prepare_holdout(
                    candidates_path=root / "pool.json",
                    repo_root=root,
                    history_roots=[],
                    history_cutoff="2999-01-01T00:00:00+00:00",
                    run_config_path=root / "config.json",
                    limit=29,
                    excluded_paths={root / "pool.json"},
                )

    def test_rejects_direct_discovery_answer_prefills(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = [candidate(index) for index in range(30)]
            records[0]["external_apply_url"] = "https://jobs.example.com/jobs/1"
            (root / "pool.json").write_text(json.dumps(records), encoding="utf-8")
            (root / "config.json").write_text("{}", encoding="utf-8")
            with patch(
                "scripts.prepare_blind_holdout._git_evidence",
                return_value={"head": "abc123", "tree": "tree123", "history_bytes": b""},
            ):
                cohort, _manifest = prepare_holdout(
                    candidates_path=root / "pool.json", repo_root=root,
                    history_roots=[], history_cutoff="2999-01-01T00:00:00+00:00",
                    run_config_path=root / "config.json", limit=30,
                    excluded_paths={root / "pool.json"},
                )
        self.assertNotIn("external_apply_url", cohort[0])


if __name__ == "__main__":
    unittest.main()
