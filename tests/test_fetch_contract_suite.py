import tempfile
import unittest
from pathlib import Path

from job_source_agent.contracts import FetchBudget, FetchClient
from job_source_agent.page_cache import PageCacheFetcher
from job_source_agent.rendered_fetcher import SmartRenderedFetcher
from job_source_agent.retrying_fetcher import RetryingFetcher
from job_source_agent.snapshot import SnapshottingFetcher
from job_source_agent.web import FetchError, Fetcher, Page


REQUEST_URL = "https://example.com/jobs?team=engineering"
REQUEST_DATA = b'{"query":"platform"}'
REQUEST_HEADERS = {"Accept": "application/json", "X-Test": "contract"}


def contract_page(url=REQUEST_URL):
    return Page(
        url=url,
        final_url="https://example.com/jobs/platform",
        html="<html><body>Platform Engineer</body></html>",
        source="fake",
        artifacts={"response": b"artifact"},
    )


class RecordingFetcher:
    timeout = 7
    capability = "recording"

    def __init__(self, page=None, error=None):
        self.page = page or contract_page()
        self.error = error
        self.calls = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append((url, data, headers))
        if self.error is not None:
            raise self.error
        return self.page


class RecordingLiveFetcher(Fetcher):
    def __init__(self, page=None, error=None):
        super().__init__(timeout=7)
        self.page = page or contract_page()
        self.error = error
        self.calls = []

    def _fetch_live(self, url, data=None, headers=None):
        self.calls.append((url, data, headers))
        if self.error is not None:
            raise self.error
        return self.page


class RecordingSmartFetcher(SmartRenderedFetcher):
    def __init__(self, page=None, error=None, **kwargs):
        kwargs.setdefault("render_budget", 1)
        kwargs.setdefault("min_visible_text_chars", 20)
        super().__init__(timeout=7, **kwargs)
        self.page = page or contract_page()
        self.error = error
        self.static_calls = []
        self.render_calls = []

    def _static_live(self, url, data=None, headers=None):
        self.static_calls.append((url, data, headers))
        if self.error is not None:
            raise self.error
        return self.page

    def _render_live(self, url, reason="manual"):
        self.render_calls.append((url, reason))
        raise AssertionError("content-rich static responses must not render")


class FetchClientContractSuite(unittest.TestCase):
    def assert_page_semantics(self, actual, expected):
        self.assertIsInstance(actual, Page)
        self.assertEqual(actual.url, expected.url)
        self.assertEqual(actual.final_url, expected.final_url)
        self.assertEqual(actual.html, expected.html)
        self.assertEqual(actual.artifacts, expected.artifacts)

    def test_all_clients_satisfy_runtime_fetch_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            clients = [
                RecordingLiveFetcher(),
                PageCacheFetcher(RecordingFetcher()),
                RetryingFetcher(RecordingFetcher(), max_retries=0, base_delay=0),
                SnapshottingFetcher(RecordingFetcher(), directory),
                RecordingSmartFetcher(),
            ]

            for client in clients:
                with self.subTest(client=type(client).__name__):
                    self.assertIsInstance(client, FetchClient)

    def test_optional_fetch_budget_is_visible_through_production_wrappers(self):
        with tempfile.TemporaryDirectory() as directory:
            client = PageCacheFetcher(
                SnapshottingFetcher(
                    RetryingFetcher(
                        RecordingFetcher(),
                        max_retries=0,
                        clock=lambda: 10.0,
                        deadline=12.5,
                    ),
                    directory,
                )
            )

            self.assertIsInstance(client, FetchBudget)
            self.assertEqual(client.remaining_fetch_seconds(), 2.5)

    def test_successful_clients_preserve_page_semantics(self):
        expected_pages = [contract_page() for _ in range(5)]
        with tempfile.TemporaryDirectory() as directory:
            clients = [
                RecordingLiveFetcher(expected_pages[0]),
                PageCacheFetcher(RecordingFetcher(expected_pages[1])),
                RetryingFetcher(RecordingFetcher(expected_pages[2]), max_retries=0, base_delay=0),
                SnapshottingFetcher(RecordingFetcher(expected_pages[3]), directory),
                RecordingSmartFetcher(expected_pages[4]),
            ]

            for client, expected in zip(clients, expected_pages):
                with self.subTest(client=type(client).__name__):
                    actual = client.fetch(REQUEST_URL)
                    self.assertIs(actual, expected)
                    self.assert_page_semantics(actual, expected)

            self.assertEqual(expected_pages[0].source, "fake")
            self.assertEqual(expected_pages[1].source, "fake")
            self.assertEqual(expected_pages[2].source, "fake")
            self.assertRegex(expected_pages[3].source, r"^fake\|snapshot:sites/")
            self.assertEqual(expected_pages[4].source, "fake")

    def test_request_data_and_headers_are_forwarded_where_supported(self):
        live = RecordingLiveFetcher()
        cache_base = RecordingFetcher()
        retry_base = RecordingFetcher()
        snapshot_base = RecordingFetcher()
        smart = RecordingSmartFetcher()

        with tempfile.TemporaryDirectory() as directory:
            clients = [
                live,
                PageCacheFetcher(cache_base),
                RetryingFetcher(retry_base, max_retries=0, base_delay=0),
                SnapshottingFetcher(snapshot_base, directory),
                smart,
            ]
            for client in clients:
                with self.subTest(client=type(client).__name__):
                    client.fetch(REQUEST_URL, data=REQUEST_DATA, headers=REQUEST_HEADERS)

        expected_call = (REQUEST_URL, REQUEST_DATA, REQUEST_HEADERS)
        self.assertEqual(live.calls, [expected_call])
        self.assertEqual(cache_base.calls, [expected_call])
        self.assertEqual(retry_base.calls, [expected_call])
        self.assertEqual(snapshot_base.calls, [expected_call])
        self.assertEqual(smart.static_calls, [expected_call])
        self.assertEqual(smart.render_calls, [])

    def test_fetch_errors_propagate_without_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            factories = [
                lambda error: RecordingLiveFetcher(error=error),
                lambda error: PageCacheFetcher(RecordingFetcher(error=error)),
                lambda error: RetryingFetcher(
                    RecordingFetcher(error=error), max_retries=0, base_delay=0
                ),
                lambda error: SnapshottingFetcher(RecordingFetcher(error=error), directory),
                lambda error: RecordingSmartFetcher(error=error, render_budget=0),
            ]

            for factory in factories:
                expected = FetchError("HTTP Error 403: Forbidden")
                client = factory(expected)
                with self.subTest(client=type(client).__name__):
                    with self.assertRaises(FetchError) as raised:
                        client.fetch(REQUEST_URL)
                    self.assertIs(raised.exception, expected)

    def test_retry_replays_the_identical_request(self):
        class FlakyRecordingFetcher(RecordingFetcher):
            def fetch(self, url, data=None, headers=None):
                self.calls.append((url, data, headers))
                if len(self.calls) == 1:
                    raise FetchError("The read operation timed out")
                return self.page

        base = FlakyRecordingFetcher()
        client = RetryingFetcher(base, max_retries=1, base_delay=0)

        page = client.fetch(REQUEST_URL, data=REQUEST_DATA, headers=REQUEST_HEADERS)

        self.assertIs(page, base.page)
        self.assertEqual(
            base.calls,
            [(REQUEST_URL, REQUEST_DATA, REQUEST_HEADERS)] * 2,
        )

    def test_snapshot_records_only_success_and_preserves_artifacts(self):
        page = contract_page()
        with tempfile.TemporaryDirectory() as directory:
            client = SnapshottingFetcher(RecordingFetcher(page), directory)

            actual = client.fetch(REQUEST_URL)
            index_path = Path(directory) / "snapshots.jsonl"
            artifact_path = Path(directory) / "artifacts" / "example.com" / "jobs" / "platform" / "response.bin"

            self.assertIs(actual, page)
            self.assertTrue(index_path.exists())
            self.assertEqual(artifact_path.read_bytes(), b"artifact")

        with tempfile.TemporaryDirectory() as directory:
            client = SnapshottingFetcher(
                RecordingFetcher(error=FetchError("HTTP Error 403: Forbidden")),
                directory,
            )

            with self.assertRaises(FetchError):
                client.fetch(REQUEST_URL)

            self.assertFalse((Path(directory) / "snapshots.jsonl").exists())

    def test_wrappers_transparently_expose_underlying_capabilities(self):
        base = RecordingFetcher()
        with tempfile.TemporaryDirectory() as directory:
            wrappers = [
                PageCacheFetcher(base),
                RetryingFetcher(base, max_retries=0, base_delay=0),
                SnapshottingFetcher(base, directory),
            ]

            for wrapper in wrappers:
                with self.subTest(wrapper=type(wrapper).__name__):
                    self.assertEqual(wrapper.timeout, 7)
                    self.assertEqual(wrapper.capability, "recording")
                    self.assertIs(wrapper.fetcher, base)

    def test_smart_render_static_success_is_transparent(self):
        page = contract_page()
        client = RecordingSmartFetcher(page=page)

        actual = client.fetch(REQUEST_URL, headers=REQUEST_HEADERS)

        self.assertIs(actual, page)
        self.assertEqual(client.static_calls, [(REQUEST_URL, None, REQUEST_HEADERS)])
        self.assertEqual(client.render_calls, [])
        self.assertEqual(client.render_attempts, 0)
        self.assertEqual(client.render_events, [])


if __name__ == "__main__":
    unittest.main()
