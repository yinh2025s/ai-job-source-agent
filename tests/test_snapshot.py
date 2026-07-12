import json
import tempfile
import unittest
from pathlib import Path

from job_source_agent.snapshot import (
    SnapshotStore,
    SnapshottingFetcher,
    sanitize_snapshot_body,
    sanitize_url,
    snapshot_artifact_path_for_url,
)
from job_source_agent.web import Fetcher, Page


class SnapshotTests(unittest.TestCase):
    def test_sanitize_url_redacts_sensitive_query_values(self):
        sanitized = sanitize_url("https://example.com/jobs?access_token=abc123&query=data")

        self.assertIn("access_token=%5BREDACTED%5D", sanitized)
        self.assertIn("query=data", sanitized)

    def test_sanitize_snapshot_body_redacts_tokens(self):
        body = 'window.cfg = {"api_key": "secret-value", "name": "Acme"}; Authorization: Bearer abcdefghijklmnop'

        sanitized = sanitize_snapshot_body(body)

        self.assertNotIn("secret-value", sanitized)
        self.assertNotIn("abcdefghijklmnop", sanitized)
        self.assertIn("[REDACTED]", sanitized)

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

    def test_snapshot_artifact_path_uses_safe_extension(self):
        path = snapshot_artifact_path_for_url("/tmp/artifacts", "https://example.com/jobs?token=x", "screenshot_png")

        self.assertEqual(path.name, "screenshot_png.png")


if __name__ == "__main__":
    unittest.main()
