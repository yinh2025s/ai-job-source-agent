import json
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import extension_bridge as extension_bridge_script
from job_source_agent.composition import (
    LINKEDIN_EVIDENCE_CACHE_FILENAME,
    FetcherConfig,
    build_application,
)
from job_source_agent.extension_bridge import (
    COMPANY_DISCOVERY_EVIDENCE_FILENAME,
    MAX_RECORDS,
    ExtensionBridgeConfig,
    ExtensionBridgeServer,
    ExtensionRunManager,
    has_valid_bearer,
    is_chrome_extension_origin,
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
                        "job_title": "AI Algorithm Engineer Intern",
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

    def test_manager_exposes_running_before_background_work_completes(self):
        started = threading.Event()
        release = threading.Event()
        manager = ExtensionRunManager(
            ExtensionBridgeConfig(fetcher=FetcherConfig(offline=True), workers=1)
        )

        def blocking_execute(run_id, companies):
            manager._update(run_id, status="running")
            started.set()
            release.wait(timeout=3)
            manager._replace(
                run_id,
                {
                    "run_id": run_id,
                    "status": "complete",
                    "submitted": len(companies),
                    "summary": {},
                    "results": [],
                },
            )

        try:
            with patch.object(manager, "_execute", side_effect=blocking_execute):
                run_id = manager.submit([
                    {
                        "company_name": "Example Robotics",
                        "linkedin_job_url": "https://www.linkedin.com/jobs/view/123",
                    }
                ])
                self.assertTrue(started.wait(timeout=1))
                self.assertEqual(manager.get(run_id)["status"], "running")
                release.set()
                self._wait_for_run(manager, run_id)
        finally:
            release.set()
            manager.close()

    def test_manager_parallelizes_companies_and_reports_monotonic_progress(self):
        manager = ExtensionRunManager(
            ExtensionBridgeConfig(fetcher=FetcherConfig(offline=True), workers=2)
        )
        companies = company_inputs_from_records([
            {
                "company_name": "First Systems",
                "linkedin_job_url": "https://www.linkedin.com/jobs/view/201",
            },
            {
                "company_name": "Second Systems",
                "linkedin_job_url": "https://www.linkedin.com/jobs/view/202",
            },
        ])
        releases = {
            "First Systems": threading.Event(),
            "Second Systems": threading.Event(),
        }
        all_started = threading.Event()
        started: set[str] = set()
        started_lock = threading.Lock()
        outcome: list = []

        def discover(company):
            with started_lock:
                started.add(company.company_name)
                if len(started) == 2:
                    all_started.set()
            releases[company.company_name].wait(timeout=3)
            return company.company_name

        run_id = "progress-run"
        manager._runs[run_id] = {
            "run_id": run_id,
            "status": "running",
            "submitted": 2,
            "completed": 0,
        }
        worker = threading.Thread(
            target=lambda: outcome.extend(manager._discover_companies(run_id, companies))
        )
        try:
            with patch.object(manager, "_discover_company", side_effect=discover):
                worker.start()
                self.assertTrue(all_started.wait(timeout=1))
                self.assertEqual(manager.get(run_id)["completed"], 0)
                releases["First Systems"].set()
                self._wait_for_completed(manager, run_id, 1)
                self.assertEqual(manager.get(run_id)["completed"], 1)
                releases["Second Systems"].set()
                worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(manager.get(run_id)["completed"], 2)
            self.assertEqual(outcome, ["First Systems", "Second Systems"])
        finally:
            for release in releases.values():
                release.set()
            worker.join(timeout=3)
            manager.close()

    def test_manager_background_exception_becomes_queryable_failed_run(self):
        manager = ExtensionRunManager(
            ExtensionBridgeConfig(fetcher=FetcherConfig(offline=True), workers=1)
        )
        try:
            with patch(
                "job_source_agent.extension_bridge.build_application",
                side_effect=RuntimeError("pipeline unavailable"),
            ):
                run_id = manager.submit([
                    {
                        "company_name": "Example Robotics",
                        "linkedin_job_url": "https://www.linkedin.com/jobs/view/123",
                    }
                ])
                run = self._wait_for_run(manager, run_id)

            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["error"], "RuntimeError: pipeline unavailable")
            self.assertEqual(manager.get(run_id), run)
        finally:
            manager.close()

    def test_manager_executor_rejection_becomes_queryable_failed_run(self):
        manager = ExtensionRunManager(
            ExtensionBridgeConfig(fetcher=FetcherConfig(offline=True), workers=1)
        )
        try:
            with patch.object(
                manager._executor,
                "submit",
                side_effect=RuntimeError("cannot schedule new futures after shutdown"),
            ):
                run_id = manager.submit([
                    {
                        "company_name": "Example Robotics",
                        "linkedin_job_url": "https://www.linkedin.com/jobs/view/123",
                    }
                ])

            run = manager.get(run_id)
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["error"], "bridge_executor_unavailable")
        finally:
            manager.close()

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
            "job_title": "AI Algorithm Engineer Intern",
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
        expected_company_evidence_path = (
            output_dir / COMPANY_DISCOVERY_EVIDENCE_FILENAME
        )
        self.assertEqual(build.call_count, 2)
        self.assertEqual(
            [call.kwargs["linkedin_evidence_cache_path"] for call in build.call_args_list],
            [expected_path, expected_path],
        )
        self.assertEqual(
            [
                call.kwargs["company_discovery_evidence_path"]
                for call in build.call_args_list
            ],
            [expected_company_evidence_path, expected_company_evidence_path],
        )

    def test_manager_uses_explicit_company_discovery_evidence_path(self):
        record = {
            "company_name": "Aurora Data",
            "company_website_url": "https://aurora-data.example",
            "linkedin_job_url": "https://www.linkedin.com/jobs/view/123",
            "job_title": "AI Algorithm Engineer Intern",
            "source": "linkedin_browser_extension",
        }
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "runs"
            evidence_path = Path(directory) / "shared" / "verified-evidence.json"
            manager = ExtensionRunManager(
                ExtensionBridgeConfig(
                    fetcher=FetcherConfig(
                        fixtures_dir=ROOT / "samples" / "sites",
                        offline=True,
                    ),
                    workers=1,
                    output_dir=output_dir,
                    company_discovery_evidence_path=evidence_path,
                )
            )
            try:
                with patch(
                    "job_source_agent.extension_bridge.build_application",
                    wraps=build_application,
                ) as build:
                    run_id = manager.submit([record])
                    self._wait_for_run(manager, run_id)
            finally:
                manager.close()

        self.assertEqual(
            build.call_args.kwargs["company_discovery_evidence_path"],
            evidence_path,
        )

    def test_script_passes_explicit_company_discovery_evidence_path(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "runs"
            evidence_path = Path(directory) / "shared-evidence.json"
            with (
                patch.object(extension_bridge_script, "ExtensionRunManager") as manager,
                patch.object(extension_bridge_script, "ExtensionBridgeServer") as server,
            ):
                extension_bridge_script.main([
                    "--token",
                    "test-token",
                    "--offline",
                    "--output-dir",
                    str(output_dir),
                    "--company-discovery-evidence-store",
                    str(evidence_path),
                ])

        config = manager.call_args.args[0]
        self.assertEqual(config.output_dir, output_dir)
        self.assertEqual(config.company_discovery_evidence_path, evidence_path)
        server.return_value.serve_forever.assert_called_once_with()
        manager.return_value.close.assert_called_once_with()

    def test_bridge_auth_contract_allows_only_extension_origin_and_exact_token(self):
        self.assertTrue(is_allowed_origin(None))
        origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
        self.assertTrue(is_chrome_extension_origin(origin))
        self.assertTrue(is_allowed_origin(origin))
        self.assertTrue(is_allowed_origin(origin, origin))
        self.assertFalse(
            is_allowed_origin(
                "chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                origin,
            )
        )
        self.assertFalse(is_allowed_origin("chrome-extension://abcdefghijklmnop"))
        self.assertFalse(is_allowed_origin(f"{origin}/popup.html"))
        self.assertFalse(is_allowed_origin("https://attacker.example"))
        self.assertTrue(has_valid_bearer("Bearer test-token", "test-token"))
        self.assertFalse(has_valid_bearer("Bearer wrong-token", "test-token"))

    def test_bridge_rejects_non_loopback_bind(self):
        self.assertEqual(validate_loopback_host("127.0.0.1"), "127.0.0.1")
        with self.assertRaisesRegex(ValueError, "loopback"):
            validate_loopback_host("0.0.0.0")

    def test_script_never_prints_generated_or_explicit_token(self):
        for args, secret in (
            ([], "generated-secret-token-generated-secret"),
            (["--token", "explicit-secret-token-explicit-secret"],
             "explicit-secret-token-explicit-secret"),
        ):
            with self.subTest(args=args):
                output = StringIO()
                with (
                    patch.object(extension_bridge_script, "ExtensionRunManager") as manager,
                    patch.object(extension_bridge_script, "ExtensionBridgeServer") as server,
                    patch.object(
                        extension_bridge_script.secrets,
                        "token_urlsafe",
                        return_value="generated-secret-token-generated-secret",
                    ) as generate,
                    redirect_stdout(output),
                ):
                    extension_bridge_script.main([
                        *args,
                        "--offline",
                        "--output-dir",
                        "/tmp/extension-bridge-test-runs",
                    ])

                rendered = output.getvalue()
                self.assertNotIn(secret, rendered)
                self.assertNotIn("token:", rendered)
                self.assertIn("bridge: http://127.0.0.1:8765", rendered)
                expected_pairing = (
                    "auto-pair: disabled (explicit token)"
                    if args
                    else "auto-pair: waiting for first extension claim"
                )
                self.assertIn(expected_pairing, rendered)
                self.assertIn("runs: /tmp/extension-bridge-test-runs", rendered)
                server.return_value.serve_forever.assert_called_once_with()
                self.assertEqual(
                    server.call_args.kwargs["pairing_enabled"],
                    not args,
                )
                manager.return_value.close.assert_called_once_with()
                if args:
                    generate.assert_not_called()
                else:
                    generate.assert_called_once_with(24)

    def test_auto_pairing_requires_urlsafe_high_entropy_token(self):
        manager = ExtensionRunManager(
            ExtensionBridgeConfig(fetcher=FetcherConfig(offline=True), workers=1)
        )
        try:
            with self.assertRaisesRegex(ValueError, "Automatic pairing"):
                ExtensionBridgeServer(("127.0.0.1", 0), manager, "short")
            server = ExtensionBridgeServer(
                ("127.0.0.1", 0),
                manager,
                "short",
                pairing_enabled=False,
            )
            server.server_close()
        finally:
            manager.close()

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

    def _wait_for_completed(
        self,
        manager: ExtensionRunManager,
        run_id: str,
        expected: int,
    ) -> None:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if manager.get(run_id).get("completed", 0) >= expected:
                return
            time.sleep(0.01)
        self.fail(f"Extension run did not report {expected} completed records.")


if __name__ == "__main__":
    unittest.main()
