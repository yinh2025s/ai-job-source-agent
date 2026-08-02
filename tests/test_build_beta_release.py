import unittest

from scripts.build_beta_release import (
    BetaReleaseError,
    REQUIRED_PRODUCT_FILES,
    select_release_files,
)


class BuildBetaReleaseTests(unittest.TestCase):
    def test_selects_source_and_review_material_without_runtime_artifacts(self):
        selected = select_release_files(
            [
                "README.md",
                "REVIEWER_START_HERE.md",
                "SUBMISSION.md",
                "job_source_agent/pipeline.py",
                "extension/background.js",
                "extension/popup.js",
                "scripts/build_beta_release.py",
                "scripts/reviewer_start.py",
                "tests/test_pipeline.py",
                "tests/test_extension_popup.py",
                "tests/test_reviewer_start.py",
                "samples/beta_demo_input.json",
                "samples/linkedin_jobs.json",
                "samples/sites/aurora-data.example/index.html",
                "samples/evaluation/blind_v3/sealed-input.bin",
                "samples/evaluation/live100_fresh_cohort_20260718.json",
                "docs/BETA_DEMO_SCRIPT.md",
                "docs/BETA4_RELEASE_REPORT.md",
                "docs/FINAL_DEMO_SCRIPT.md",
                "docs/adr/0037-auto-pair-local-extension.md",
                "docs/adr/0038-bound-extension-run-concurrency.md",
                "docs/adr/0025-merge-provider-candidate-discovery.md",
                "artifacts/evaluations/run/results.json",
                "capture/snapshots/page.html",
                "tests/captures/authenticated-page.html",
                "tests/capture/authenticated-page.html",
                "tests/live/results.json",
                "tests/tokens/bridge.txt",
                "tests/token/bridge.txt",
                "tests/raw/linkedin.html",
                "tests/secrets/local.txt",
                "tests/secret/local.txt",
                ".DS_Store",
            ]
        )

        self.assertIn("README.md", selected)
        self.assertIn("REVIEWER_START_HERE.md", selected)
        self.assertIn("job_source_agent/pipeline.py", selected)
        self.assertIn("extension/background.js", selected)
        self.assertIn("extension/popup.js", selected)
        self.assertIn("scripts/reviewer_start.py", selected)
        self.assertIn("tests/test_extension_popup.py", selected)
        self.assertIn("tests/test_reviewer_start.py", selected)
        self.assertIn("samples/beta_demo_input.json", selected)
        self.assertIn("samples/sites/aurora-data.example/index.html", selected)
        self.assertIn("docs/adr/0037-auto-pair-local-extension.md", selected)
        self.assertIn("docs/BETA4_RELEASE_REPORT.md", selected)
        self.assertIn("docs/FINAL_DEMO_SCRIPT.md", selected)
        self.assertIn("docs/adr/0038-bound-extension-run-concurrency.md", selected)
        self.assertIn("docs/adr/0025-merge-provider-candidate-discovery.md", selected)
        self.assertNotIn("samples/evaluation/blind_v3/sealed-input.bin", selected)
        self.assertNotIn(
            "samples/evaluation/live100_fresh_cohort_20260718.json", selected
        )
        self.assertNotIn("artifacts/evaluations/run/results.json", selected)
        self.assertNotIn("capture/snapshots/page.html", selected)
        self.assertNotIn("tests/captures/authenticated-page.html", selected)
        self.assertNotIn("tests/capture/authenticated-page.html", selected)
        self.assertNotIn("tests/live/results.json", selected)
        self.assertNotIn("tests/tokens/bridge.txt", selected)
        self.assertNotIn("tests/token/bridge.txt", selected)
        self.assertNotIn("tests/raw/linkedin.html", selected)
        self.assertNotIn("tests/secrets/local.txt", selected)
        self.assertNotIn("tests/secret/local.txt", selected)
        self.assertNotIn(".DS_Store", selected)

    def test_rejects_path_traversal(self):
        with self.assertRaisesRegex(BetaReleaseError, "unsafe tracked path"):
            select_release_files(["../outside.txt"])

    def test_reviewer_product_files_are_release_requirements(self):
        self.assertTrue(
            {
                "extension/background.js",
                "extension/popup.js",
                "scripts/reviewer_start.py",
                "tests/test_extension_background.py",
                "tests/test_extension_popup.py",
                "tests/test_reviewer_start.py",
            }.issubset(REQUIRED_PRODUCT_FILES)
        )


if __name__ == "__main__":
    unittest.main()
