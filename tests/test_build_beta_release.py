import unittest

from scripts.build_beta_release import BetaReleaseError, select_release_files


class BuildBetaReleaseTests(unittest.TestCase):
    def test_selects_source_and_review_material_without_runtime_artifacts(self):
        selected = select_release_files(
            [
                "README.md",
                "SUBMISSION.md",
                "job_source_agent/pipeline.py",
                "scripts/build_beta_release.py",
                "tests/test_pipeline.py",
                "samples/beta_demo_input.json",
                "samples/linkedin_jobs.json",
                "samples/sites/aurora-data.example/index.html",
                "samples/evaluation/blind_v3/sealed-input.bin",
                "samples/evaluation/live100_fresh_cohort_20260718.json",
                "docs/BETA_DEMO_SCRIPT.md",
                "docs/adr/0025-merge-provider-candidate-discovery.md",
                "artifacts/evaluations/run/results.json",
                "capture/snapshots/page.html",
                ".DS_Store",
            ]
        )

        self.assertIn("README.md", selected)
        self.assertIn("job_source_agent/pipeline.py", selected)
        self.assertIn("samples/beta_demo_input.json", selected)
        self.assertIn("samples/sites/aurora-data.example/index.html", selected)
        self.assertNotIn("samples/evaluation/blind_v3/sealed-input.bin", selected)
        self.assertNotIn(
            "samples/evaluation/live100_fresh_cohort_20260718.json", selected
        )
        self.assertNotIn("artifacts/evaluations/run/results.json", selected)
        self.assertNotIn("capture/snapshots/page.html", selected)
        self.assertNotIn(".DS_Store", selected)

    def test_rejects_path_traversal(self):
        with self.assertRaisesRegex(BetaReleaseError, "unsafe tracked path"):
            select_release_files(["../outside.txt"])


if __name__ == "__main__":
    unittest.main()
