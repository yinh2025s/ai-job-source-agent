import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.scan_artifact_privacy import (
    ArtifactPrivacyError,
    main,
    scan_artifact_root,
)


JWT = b"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature_value"
AWS = b"AKIAIOSFODNN7EXAMPLE"
GOOGLE = b"AIza" + b"A" * 35
GITHUB = b"ghp_" + b"B" * 36
SLACK = b"xox" + b"b-1234567890-abcdefghijklmnop"
OPENAI = b"sk-proj-" + b"C" * 32
ANTHROPIC = b"sk-ant-" + b"D" * 32
STRIPE = b"sk_live_" + b"E" * 24
PRIVATE_KEY = b"-----BEGIN PRIVATE KEY-----"


class ScanArtifactPrivacyTests(unittest.TestCase):
    def test_scans_nested_binary_files_and_reports_deterministic_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            (root / "z.bin").write_bytes(b"\x00\xffclean")
            (root / "nested" / "a.txt").write_bytes(b"clean text")

            first = scan_artifact_root(root)
            second = scan_artifact_root(root)

            self.assertEqual(first, second)
            self.assertEqual(first["files_scanned"], 2)
            self.assertEqual(first["bytes_scanned"], 17)
            self.assertEqual(first["total_matches"], 0)
            self.assertEqual(first["matches"], [])
            self.assertRegex(first["file_set_sha256"], r"^[0-9a-f]{64}$")

            (root / "nested" / "a.txt").write_bytes(b"changed")
            changed = scan_artifact_root(root)
            self.assertNotEqual(
                first["file_set_sha256"], changed["file_set_sha256"]
            )

    def test_detects_supported_shapes_without_exposing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"\n".join(
                (
                    JWT,
                    AWS,
                    GOOGLE,
                    PRIVATE_KEY,
                    GITHUB,
                    GITHUB,
                    SLACK,
                    OPENAI,
                    ANTHROPIC,
                    STRIPE,
                )
            )
            (root / "credentials.bin").write_bytes(payload)

            report = scan_artifact_root(root)
            by_type = {
                row["type"]: row["count"]
                for row in report["matches"]
            }

            self.assertEqual(
                by_type,
                {
                    "anthropic_api_key": 1,
                    "aws_access_key_id": 1,
                    "github_token": 2,
                    "google_api_key": 1,
                    "jwt": 1,
                    "openai_api_key": 1,
                    "private_key_header": 1,
                    "slack_token": 1,
                    "stripe_secret_key": 1,
                },
            )
            self.assertEqual(report["total_matches"], 10)
            encoded_report = json.dumps(report, sort_keys=True)
            for secret in (
                JWT,
                AWS,
                GOOGLE,
                GITHUB,
                SLACK,
                OPENAI,
                ANTHROPIC,
                STRIPE,
                PRIVATE_KEY,
            ):
                self.assertNotIn(secret.decode("ascii"), encoded_report)

    def test_redacts_credential_shape_in_reported_relative_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret_name = GOOGLE.decode("ascii")
            (root / secret_name).write_bytes(AWS)

            report = scan_artifact_root(root)

            self.assertEqual(report["matches"][0]["relative_path"], "[REDACTED]")
            self.assertNotIn(secret_name, json.dumps(report))

    def test_skips_file_and_directory_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "artifacts"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "clean.txt").write_text("clean", encoding="ascii")
            (outside / "secret.txt").write_bytes(GITHUB)
            (root / "linked-file").symlink_to(outside / "secret.txt")
            (root / "linked-directory").symlink_to(
                outside,
                target_is_directory=True,
            )

            report = scan_artifact_root(root)

            self.assertEqual(report["files_scanned"], 1)
            self.assertEqual(report["total_matches"], 0)

    def test_rejects_missing_non_directory_and_symlink_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            missing = base / "missing"
            with self.assertRaisesRegex(
                ArtifactPrivacyError, "does not exist"
            ):
                scan_artifact_root(missing)

            regular_file = base / "artifact.txt"
            regular_file.write_text("clean", encoding="ascii")
            with self.assertRaisesRegex(
                ArtifactPrivacyError, "must be a directory"
            ):
                scan_artifact_root(regular_file)

            real_root = base / "real"
            real_root.mkdir()
            linked_root = base / "linked"
            linked_root.symlink_to(real_root, target_is_directory=True)
            with self.assertRaisesRegex(ArtifactPrivacyError, "symlink"):
                scan_artifact_root(linked_root)

    def test_rejects_unreadable_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unreadable = root / "unreadable.bin"
            unreadable.write_bytes(b"clean")
            unreadable.chmod(0)
            try:
                with self.assertRaisesRegex(
                    ArtifactPrivacyError, "not readable"
                ):
                    scan_artifact_root(root)
            finally:
                unreadable.chmod(0o600)

    def test_cli_emits_json_and_fail_on_match_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact.txt").write_bytes(SLACK)
            output = root / "privacy.json"

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    [
                        str(root),
                        "--fail-on-match",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["total_matches"], 1)
            self.assertNotIn(SLACK.decode("ascii"), stdout.getvalue())
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), payload)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(main([str(root)]), 0)

    def test_cli_scan_error_fails_closed_without_json_report(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(["/definitely/not/a/real/artifact/root"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("scan failed", stderr.getvalue())

    def test_executable_entrypoint_uses_same_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "clean.txt").write_text("clean", encoding="ascii")
            script = Path(__file__).resolve().parents[1] / "scripts"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script / "scan_artifact_privacy.py"),
                    str(root),
                    "--fail-on-match",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["total_matches"], 0)


if __name__ == "__main__":
    unittest.main()
