import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import job_source_agent.snapshot as snapshot_module
from job_source_agent.snapshot import (
    SnapshotStore,
    SnapshottingFetcher,
    sanitize_snapshot_body,
    sanitize_url,
    snapshot_artifact_path_for_url,
)
from job_source_agent.web import FetchError, Fetcher, Page


class SnapshotTests(unittest.TestCase):
    def test_sanitize_url_redacts_sensitive_query_values(self):
        sanitized = sanitize_url("https://example.com/jobs?access_token=abc123&query=data")

        self.assertIn("access_token=%5BREDACTED%5D", sanitized)
        self.assertIn("query=data", sanitized)

    def test_sanitize_url_redacts_apikey_spelling_variants(self):
        for key in ("apikey", "api_key", "api-key"):
            with self.subTest(key=key):
                sanitized = sanitize_url(f"https://example.com/jobs?{key}=abc123")
                self.assertNotIn("abc123", sanitized)
                self.assertIn("%5BREDACTED%5D", sanitized)

    def test_sanitize_snapshot_body_redacts_tokens(self):
        body = (
            'window.cfg = {"api_key": "secret-value", "sessionJWT": "public-session-token", '
            '"authToken": "private-auth-token", "sessionCSRFToken": "csrf-secret", '
            '"name": "Acme"}; '
            'Authorization: Bearer abcdefghijklmnop'
        )

        sanitized = sanitize_snapshot_body(body)

        self.assertNotIn("secret-value", sanitized)
        self.assertNotIn("public-session-token", sanitized)
        self.assertNotIn("private-auth-token", sanitized)
        self.assertNotIn("csrf-secret", sanitized)
        self.assertNotIn("abcdefghijklmnop", sanitized)
        self.assertIn("[REDACTED]", sanitized)

    def test_sanitize_snapshot_body_redacts_ceipal_credential_path(self):
        body = (
            '{"next":"https://careerapi.ceipal.com/private-key/'
            'CareerPortalJobPostings/?page=2"}'
        )

        sanitized = sanitize_snapshot_body(body)

        self.assertNotIn("private-key", sanitized)
        self.assertIn(
            "https://careerapi.ceipal.com/[REDACTED]/CareerPortalJobPostings/",
            sanitized,
        )

    def test_multipart_ceipal_snapshot_replays_without_persisting_path_key(self):
        boundary = "----ceipal-snapshot-boundary"

        def body(api_key):
            fields = (("page", "1"), ("api_key", api_key), ("cp_id", "portal-one"))
            return (
                "".join(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                    f"\r\n\r\n{value}\r\n"
                    for name, value in fields
                )
                + f"--{boundary}--\r\n"
            ).encode()

        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        first_url = (
            "https://careerapi.ceipal.com/private-one/"
            "CareerPortalJobPostings/?page=1"
        )
        second_url = first_url.replace("private-one", "private-two")
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            record = store.write_page(
                Page(
                    url=first_url,
                    final_url=first_url,
                    html='{"next":"https://careerapi.ceipal.com/private-one/'
                    'CareerPortalJobPostings/?page=2"}',
                    source="live",
                ),
                request_url=first_url,
                data=body("private-one"),
                headers=headers,
            )
            page = Fetcher(fixtures_dir=store.fixtures_dir, offline=True).fetch(
                second_url,
                data=body("private-two"),
                headers=headers,
            )
            metadata = Path(store.index_path).read_text(encoding="utf-8")

        self.assertIn(".__request_", record.path)
        self.assertNotIn("private-one", metadata)
        self.assertNotIn("private-one", page.html)
        self.assertIn("[REDACTED]", page.html)

    def test_sanitize_snapshot_body_redacts_hidden_input_tokens_in_any_order(self):
        body = (
            '<input type="hidden" id="token" value="public-routing-token">'
            '<input value="second-routing-token" name="authToken" type="hidden">'
        )

        sanitized = sanitize_snapshot_body(body)

        self.assertNotIn("public-routing-token", sanitized)
        self.assertNotIn("second-routing-token", sanitized)
        self.assertEqual(sanitized.count("[REDACTED]"), 2)

    def test_sanitize_snapshot_body_redacts_meta_csrf_in_any_order(self):
        body = (
            '<meta name="_csrf" content="eightfold-csrf-token">'
            '<meta content="second-csrf-token" property="token">'
        )

        sanitized = sanitize_snapshot_body(body)

        self.assertNotIn("eightfold-csrf-token", sanitized)
        self.assertNotIn("second-csrf-token", sanitized)
        self.assertEqual(sanitized.count("[REDACTED]"), 2)

    def test_snapshot_store_writes_fixture_compatible_page(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            record = store.write_page(
                Page(
                    url="https://jobs.example.com/search?token=secret",
                    final_url="https://jobs.example.com/search?token=secret",
                    html="<html><body>Jobs access_token=secret</body></html>",
                    source="live",
                )
            )

            replay_page = Fetcher(fixtures_dir=store.fixtures_dir, offline=True).fetch("https://jobs.example.com/search")
            metadata = [json.loads(line) for line in Path(store.index_path).read_text(encoding="utf-8").splitlines()]

        self.assertEqual(replay_page.html, "<html><body>Jobs access_token=[REDACTED]</body></html>")
        self.assertEqual(metadata[0]["path"], record.path)
        self.assertEqual(metadata[0]["source"], "live")
        self.assertIn("token=%5BREDACTED%5D", metadata[0]["final_url"])

    def test_snapshotting_fetcher_wraps_successful_fetches(self):
        class FakeFetcher:
            timeout = 1

            def fetch(self, url, data=None, headers=None):
                return Page(url=url, final_url=url, html="<html>ok</html>", source="fake")

        with tempfile.TemporaryDirectory() as directory:
            fetcher = SnapshottingFetcher(FakeFetcher(), directory)
            page = fetcher.fetch("https://example.com/careers")
            index_path = Path(directory) / "snapshots.jsonl"
            index_exists = index_path.exists()

        self.assertIn("snapshot:sites/example.com/careers/index.html", page.source)
        self.assertTrue(index_exists)

    def test_snapshot_store_writes_page_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            record = store.write_page(
                Page(
                    url="https://jobs.example.com/search",
                    final_url="https://jobs.example.com/search",
                    html="<html>ok</html>",
                    source="browser_after_static_shell|artifact:screenshot_png",
                    artifacts={"screenshot_png": b"fake-png"},
                )
            )
            artifact_path = Path(directory) / record.artifact_paths["screenshot_png"]
            metadata = [json.loads(line) for line in Path(store.index_path).read_text(encoding="utf-8").splitlines()]
            artifact_bytes = artifact_path.read_bytes()

        self.assertEqual(artifact_bytes, b"fake-png")
        self.assertEqual(metadata[0]["artifact_paths"]["screenshot_png"], record.artifact_paths["screenshot_png"])

    def test_snapshot_index_is_not_published_before_page_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_write = snapshot_module._write_bytes_atomic

            def fail_fixture_publish(path, content):
                if "sites" in path.parts:
                    raise OSError("injected fixture publication failure")
                return original_write(path, content)

            with patch(
                "job_source_agent.snapshot._write_bytes_atomic",
                side_effect=fail_fixture_publish,
            ):
                with self.assertRaisesRegex(OSError, "injected fixture"):
                    SnapshotStore(root).write_page(
                        Page(
                            url="https://jobs.example.com/search",
                            html="<html>not committed</html>",
                            source="live",
                        )
                    )

            self.assertFalse((root / "snapshots.jsonl").exists())
            self.assertFalse((root / ".snapshot-sequence").exists())

    def test_snapshot_publisher_fsyncs_content_and_metadata_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("job_source_agent.snapshot._fsync_directory") as fsync_directory:
                SnapshotStore(root).write_page(
                    Page(
                        url="https://jobs.example.com/search",
                        html="<html>durable</html>",
                        source="live",
                    )
                )

            fsynced = [call.args[0] for call in fsync_directory.call_args_list]

        self.assertIn(root, fsynced)
        self.assertTrue(any("sites" in path.parts for path in fsynced))
        self.assertTrue(any("blobs" in path.parts for path in fsynced))

    def test_snapshot_artifact_path_uses_safe_extension(self):
        path = snapshot_artifact_path_for_url("/tmp/artifacts", "https://example.com/jobs", "screenshot_png")

        self.assertEqual(path.name, "screenshot_png.png")

    def test_query_snapshots_use_distinct_paths_and_fixture_fetcher_selects_each(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            first = store.write_page(
                Page(url="https://jobs.example.com/search?from=0", html="first", source="live")
            )
            second = store.write_page(
                Page(url="https://jobs.example.com/search?from=10", html="second", source="live")
            )
            fetcher = Fetcher(fixtures_dir=store.fixtures_dir, offline=True)

            first_page = fetcher.fetch("https://jobs.example.com/search?from=0")
            second_page = fetcher.fetch("https://jobs.example.com/search?from=10")

        self.assertNotEqual(first.path, second.path)
        self.assertEqual(first_page.html, "first")
        self.assertEqual(second_page.html, "second")

    def test_post_body_snapshots_use_distinct_request_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            headers = {"Content-Type": "application/json"}
            first_data = b'{"range": 0, "api_key": "secret-one"}'
            second_data = b'{"range": 10, "api_key": "secret-two"}'
            first = store.write_page(
                Page(url="https://jobs.example.com/api", html="first", source="live"),
                data=first_data,
                headers=headers,
            )
            second = store.write_page(
                Page(url="https://jobs.example.com/api", html="second", source="live"),
                data=second_data,
                headers=headers,
            )
            fetcher = Fetcher(fixtures_dir=store.fixtures_dir, offline=True)

            first_page = fetcher.fetch(
                "https://jobs.example.com/api", data=first_data, headers=headers
            )
            second_page = fetcher.fetch(
                "https://jobs.example.com/api", data=second_data, headers=headers
            )
            metadata = Path(store.index_path).read_text(encoding="utf-8")

        self.assertNotEqual(first.path, second.path)
        self.assertIn(".__request_", first.path)
        self.assertEqual(first_page.html, "first")
        self.assertEqual(second_page.html, "second")
        self.assertNotIn("secret-one", metadata)
        self.assertNotIn("secret-two", metadata)

    def test_snapshotting_fetcher_records_structured_terminal_failure(self):
        class FailingFetcher:
            timeout = 1

            def fetch(self, url, data=None, headers=None):
                raise FetchError(
                    "HTTP Error 403: Forbidden",
                    status=403,
                    reason_code="HTTP_FORBIDDEN",
                    retryable=False,
                )

        with tempfile.TemporaryDirectory() as directory:
            fetcher = SnapshottingFetcher(FailingFetcher(), directory)

            with self.assertRaises(FetchError):
                fetcher.fetch("https://jobs.example.com/?apikey=private")
            failure_text = (Path(directory) / "fetch-failures.jsonl").read_text(
                encoding="utf-8"
            )

        self.assertIn('"reason_code": "HTTP_FORBIDDEN"', failure_text)
        self.assertIn('"status": 403', failure_text)
        self.assertNotIn("private", failure_text)

    def test_sensitive_query_snapshot_fingerprint_uses_redacted_value(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            record = store.write_page(
                Page(url="https://jobs.example.com/search?token=secret&from=10", html="page", source="live")
            )
            page = Fetcher(fixtures_dir=store.fixtures_dir, offline=True).fetch(
                "https://jobs.example.com/search?token=different-secret&from=10"
            )

        self.assertIn(".__query_", record.path)
        self.assertEqual(page.html, "page")

    def test_missing_query_variant_does_not_fall_back_when_specific_fixtures_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            store.write_page(
                Page(url="https://jobs.example.com/search?from=0", html="first", source="live")
            )
            fetcher = Fetcher(fixtures_dir=store.fixtures_dir, offline=True)

            with self.assertRaises(FetchError):
                fetcher.fetch("https://jobs.example.com/search?from=10")

    def test_multiple_store_instances_serialize_shared_index_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def write_snapshot(index: int):
                return SnapshotStore(root).write_page(
                    Page(
                        url=f"https://jobs.example.com/{index}",
                        final_url=f"https://jobs.example.com/{index}",
                        html=f"<html>{index}</html>",
                        source="live",
                    )
                )

            with ThreadPoolExecutor(max_workers=8) as executor:
                records = list(executor.map(write_snapshot, range(24)))
            metadata = [
                json.loads(line)
                for line in (root / "snapshots.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(len(records), 24)
        self.assertEqual(len(metadata), 24)
        self.assertEqual(len({record["blob_path"] for record in metadata}), 24)


if __name__ == "__main__":
    unittest.main()
