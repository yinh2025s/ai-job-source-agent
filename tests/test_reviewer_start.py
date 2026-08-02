from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import reviewer_start


ROOT = Path(__file__).resolve().parents[1]
PYTHON_312 = (3, 12, 9)


class ReviewerStartTests(unittest.TestCase):
    def test_check_only_succeeds_without_starting_server(self):
        execute = Mock()
        output = StringIO()

        with redirect_stdout(output):
            result = reviewer_start.run(
                ["--check-only"],
                version_info=PYTHON_312,
                implementation="CPython",
                occupied=lambda _host, _port: False,
                execv=execute,
            )

        self.assertEqual(result, 0)
        execute.assert_not_called()
        self.assertIn("Preflight passed", output.getvalue())
        self.assertIn("bridge was not started", output.getvalue())

    def test_success_executes_existing_bridge_and_remains_attached(self):
        execute = Mock()

        reviewer_start.run(
            ["--host", "localhost", "--port", "9876"],
            version_info=PYTHON_312,
            implementation="CPython",
            occupied=lambda _host, _port: False,
            execv=execute,
        )

        expected = reviewer_start.bridge_command(
            reviewer_start.sys.executable,
            "localhost",
            9876,
        )
        execute.assert_called_once_with(reviewer_start.sys.executable, expected)
        self.assertNotIn("--token", expected)

    def test_wrong_python_has_actionable_message(self):
        error = StringIO()

        with redirect_stderr(error), patch.object(
            reviewer_start.sys,
            "version_info",
            (3, 13, 1),
        ), patch.object(reviewer_start, "_port_is_occupied", return_value=False):
            result = reviewer_start.main(["--check-only"])

        self.assertEqual(result, 2)
        self.assertIn("use CPython 3.12", error.getvalue())
        self.assertIn("PYTHON=python3.12", error.getvalue())

    def test_missing_extension_file_is_reported_concisely(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in reviewer_start.REQUIRED_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}" if relative.endswith("manifest.json") else "", encoding="utf-8")
            (root / "extension" / "background.js").unlink()

            with self.assertRaisesRegex(
                reviewer_start.PreflightError,
                "missing extension/background.js",
            ):
                reviewer_start.run(
                    ["--check-only"],
                    root=root,
                    version_info=PYTHON_312,
                    implementation="CPython",
                    occupied=lambda _host, _port: False,
                )

    def test_invalid_host_and_port_are_rejected(self):
        for args, message in (
            (["--host", "0.0.0.0", "--check-only"], "must be loopback"),
            (["--port", "70000", "--check-only"], "1 to 65535"),
        ):
            with self.subTest(args=args), self.assertRaisesRegex(
                reviewer_start.PreflightError,
                message,
            ):
                reviewer_start.run(
                    args,
                    version_info=PYTHON_312,
                    implementation="CPython",
                    occupied=lambda _host, _port: False,
                )

    def test_occupied_existing_bridge_is_distinguished(self):
        with self.assertRaisesRegex(
            reviewer_start.PreflightError,
            "already has a Job Source Agent bridge",
        ):
            reviewer_start.run(
                ["--check-only"],
                version_info=PYTHON_312,
                implementation="CPython",
                occupied=lambda _host, _port: True,
                existing_bridge=lambda _host, _port: True,
            )

    def test_occupied_other_service_suggests_free_or_changed_port(self):
        with self.assertRaisesRegex(
            reviewer_start.PreflightError,
            "REVIEWER_PORT=<free-port>",
        ):
            reviewer_start.run(
                ["--check-only"],
                version_info=PYTHON_312,
                implementation="CPython",
                occupied=lambda _host, _port: True,
                existing_bridge=lambda _host, _port: False,
            )

    def test_bridge_command_reuses_automatic_pairing_defaults(self):
        command = reviewer_start.bridge_command("python3.12", "127.0.0.1", 8765)

        self.assertEqual(
            command,
            [
                "python3.12",
                "-m",
                "scripts.extension_bridge",
                "--host",
                "127.0.0.1",
                "--port",
                "8765",
                "--workers",
                "4",
                "--fetch-timeout",
                "8",
            ],
        )
        self.assertNotIn("--token", command)

    def test_makefile_preserves_python_override_and_exposes_both_targets(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("PYTHON ?= python3.12", makefile)
        self.assertIn("reviewer-start:", makefile)
        self.assertIn("reviewer-check:", makefile)
        self.assertIn("$(PYTHON) scripts/reviewer_start.py", makefile)
        self.assertIn("--check-only", makefile)


if __name__ == "__main__":
    unittest.main()
