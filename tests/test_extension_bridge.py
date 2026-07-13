import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from job_source_agent.composition import (
    LINKEDIN_EVIDENCE_CACHE_FILENAME,
    FetcherConfig,
    build_application,
)
from job_source_agent.extension_bridge import (
    MAX_RECORDS,
    ExtensionBridgeConfig,
    ExtensionRunManager,
    has_valid_bearer,
    is_allowed_origin,
    validate_loopback_host,
)
from job_source_agent.linkedin import company_inputs_from_records


ROOT = Path(__file__).resolve().parents[1]


class ExtensionBridgeTests(unittest.TestCase):
    def test_job_url_is_sufficient_browser_source_evidence(self):
        companies = company_inputs_from_records([
            {
                "company_name": "Example Robotics",
                "linkedin_job_url": "https://www.linkedin.com/jobs/view/123",
                "job_title": "AI Engineer",
                "source": "linkedin_browser_extension",
            }
        ])

        self.assertEqual(companies[0].company_name, "Example Robotics")
        self.assertEqual(companies[0].source, "linkedin_browser_extension")

    def test_manager_runs_browser_record_through_existing_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ExtensionRunManager(
                ExtensionBridgeConfig(
                    fetcher=FetcherConfig(
                        fixtures_dir=ROOT / "samples" / "sites",
                        offline=True,
                    ),
                    workers=1,
                    output_dir=Path(directory),
                )
            )
            try:
                run_id = manager.submit([
                    {
                        "company_name": "Aurora Data",
                        "company_website_url": "https://aurora-data.example",
                        "linkedin_job_url": "https://www.linkedin.com/jobs/view/123",
                        "job_title": "AI Engineer",
                        "job_location": "Remote",
                        "source": "linkedin_browser_extension",
                    }
                ])
                run = self._wait_for_run(manager, run_id)
            finally:
                manager.close()

            self.assertEqual(run["status"], "complete")
            self.assertEqual(run["summary"]["with_job_list"], 1)
            self.assertEqual(run["summary"]["with_opening"], 1)
            self.assertTrue((Path(directory) / run_id / "results.json").is_file())
            self.assertTrue((Path(directory) / run_id / "trace.json").is_file())

    def test_manager_rejects_oversized_batch(self):
        manager = ExtensionRunManager(
            ExtensionBridgeConfig(fetcher=FetcherConfig(offline=True), workers=1)
        )
        records = [
            {
                "company_name": f"Company {index}",
                "linkedin_job_url": f"https://www.linkedin.com/jobs/view/{index}",
            }
            for index in range(MAX_RECORDS + 1)
        ]
        try:
            with self.assertRaisesRegex(ValueError, "at most 30"):
                manager.submit(records)
        finally:
            manager.close()

    def test_manager_reuses_output_directory_evidence_cache_across_runs(self):
        record = {
            "company_name": "Aurora Data",
            "company_website_url": "https://aurora-data.example",
            "linkedin_job_url": "https://www.linkedin.com/jobs/view/123",
            "job_title": "AI Engineer",
            "source": "linkedin_browser_extension",
        }
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            manager = ExtensionRunManager(
                ExtensionBridgeConfig(
                    fetcher=FetcherConfig(
                        fixtures_dir=ROOT / "samples" / "sites",
                        offline=True,
                    ),
                    workers=1,
                    output_dir=output_dir,
                )
            )
            try:
                with patch(
                    "job_source_agent.extension_bridge.build_application",
                    wraps=build_application,
                ) as build:
                    first_run = manager.submit([record])
                    self._wait_for_run(manager, first_run)
                    second_run = manager.submit([record])
                    self._wait_for_run(manager, second_run)
            finally:
                manager.close()

        expected_path = output_dir / LINKEDIN_EVIDENCE_CACHE_FILENAME
        self.assertEqual(build.call_count, 2)
        self.assertEqual(
            [call.kwargs["linkedin_evidence_cache_path"] for call in build.call_args_list],
            [expected_path, expected_path],
        )

    def test_bridge_auth_contract_allows_only_extension_origin_and_exact_token(self):
        self.assertTrue(is_allowed_origin(None))
        self.assertTrue(is_allowed_origin("chrome-extension://abcdefghijklmnop"))
        self.assertFalse(is_allowed_origin("https://attacker.example"))
        self.assertTrue(has_valid_bearer("Bearer test-token", "test-token"))
        self.assertFalse(has_valid_bearer("Bearer wrong-token", "test-token"))

    def test_bridge_rejects_non_loopback_bind(self):
        self.assertEqual(validate_loopback_host("127.0.0.1"), "127.0.0.1")
        with self.assertRaisesRegex(ValueError, "loopback"):
            validate_loopback_host("0.0.0.0")

    def test_manifest_permissions_are_scoped(self):
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["manifest_version"], 3)
        self.assertNotIn("<all_urls>", manifest["host_permissions"])
        self.assertEqual(
            manifest["content_scripts"][0]["matches"],
            ["https://www.linkedin.com/jobs/*"],
        )
        self.assertIn("http://127.0.0.1/*", manifest["host_permissions"])

    def _wait_for_run(self, manager: ExtensionRunManager, run_id: str) -> dict:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            run = manager.get(run_id)
            if run and run["status"] in {"complete", "failed"}:
                return run
            time.sleep(0.01)
        self.fail("Extension run did not complete before the test deadline.")


if __name__ == "__main__":
    unittest.main()
