from __future__ import annotations

import json
import unittest
from urllib.parse import parse_qs, urlparse

from job_source_agent.search_backend import SearchQuery
from job_source_agent.searxng_search_backend import SearxngSearchBackend
from job_source_agent.web import FetchError, Page


class RecordingFetcher:
    def __init__(self, response: Page | BaseException) -> None:
        self.response = response
        self.calls: list[tuple[str, bytes | None, dict[str, str] | None]] = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append((url, data, headers))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class SearxngSearchBackendTests(unittest.TestCase):
    def test_search_maps_json_results_and_dispatches_once(self):
        payload = {
            "results": [
                {
                    "url": "https://jobs.example.com/openings/123",
                    "title": "Software Engineer",
                    "content": "Build reliable systems.",
                },
                {
                    "url": "https://jobs.example.com/openings/456",
                    "title": "Platform Engineer",
                },
            ]
        }
        fetcher = RecordingFetcher(
            Page(
                "https://search.example.net/search",
                json.dumps(payload),
                final_url="https://search.example.net/search?served=1",
            )
        )
        backend = SearxngSearchBackend("https://search.example.net")

        response = backend.search(
            SearchQuery('"Example" "Software Engineer"'),
            fetcher=fetcher,
        )

        self.assertEqual(len(fetcher.calls), 1)
        request_url, data, headers = fetcher.calls[0]
        parsed = urlparse(request_url)
        self.assertEqual(parsed.path, "/search")
        self.assertEqual(
            parse_qs(parsed.query),
            {
                "format": ["json"],
                "language": ["en-US"],
                "q": ['"Example" "Software Engineer"'],
                "safesearch": ["1"],
            },
        )
        self.assertIsNone(data)
        self.assertEqual(headers, {"Accept": "application/json"})
        self.assertEqual(response.disposition, "ok")
        self.assertEqual(urlparse(response.request_url).hostname, "search-backend.invalid")
        self.assertEqual(urlparse(response.final_url).hostname, "search-backend.invalid")
        self.assertNotIn("search.example.net", response.request_url)
        self.assertNotIn("search.example.net", response.final_url)
        self.assertNotIn("Example", response.request_url)
        self.assertNotIn("Software Engineer", response.final_url)
        self.assertEqual(len(response.hits), 2)
        self.assertEqual(response.hits[0].title, "Software Engineer")
        self.assertEqual(response.hits[0].snippet, "Build reliable systems.")
        self.assertEqual(response.hits[1].snippet, "")
        self.assertNotIn("search.example.net", repr(response))
        self.assertNotIn("Example", repr(response))
        self.assertNotIn("Software Engineer", repr(response))

    def test_malformed_json_returns_invalid_response(self):
        fetcher = RecordingFetcher(
            Page("https://search.example.net/search", "{not-json")
        )

        response = SearxngSearchBackend(
            "https://search.example.net/search"
        ).search(SearchQuery("query"), fetcher=fetcher)

        self.assertEqual(response.disposition, "invalid_response")
        self.assertEqual(response.reason, "malformed_json")
        self.assertEqual(response.hits, ())
        self.assertEqual(len(fetcher.calls), 1)

    def test_invalid_results_shape_returns_invalid_response(self):
        fetcher = RecordingFetcher(
            Page("https://search.example.net/search", '{"results": {}}')
        )

        response = SearxngSearchBackend(
            "https://search.example.net"
        ).search(SearchQuery("query"), fetcher=fetcher)

        self.assertEqual(response.disposition, "invalid_response")
        self.assertEqual(response.reason, "invalid_results_shape")
        self.assertEqual(response.hits, ())

    def test_invalid_result_items_are_skipped(self):
        payload = {
            "results": [
                None,
                {},
                {"url": ""},
                {"url": 42},
                {"url": "https://jobs.example/1", "title": 42},
                {"url": "https://jobs.example/2", "content": []},
                {
                    "url": "javascript:alert(1)",
                    "title": "Untrusted but structurally valid",
                    "content": "Resolver must clean this later.",
                },
            ]
        }
        fetcher = RecordingFetcher(
            Page("https://search.example.net/search", json.dumps(payload))
        )

        response = SearxngSearchBackend(
            "https://search.example.net"
        ).search(SearchQuery("query"), fetcher=fetcher)

        self.assertEqual(len(response.hits), 1)
        self.assertEqual(response.hits[0].url, "javascript:alert(1)")

    def test_fetch_error_is_raised_unchanged(self):
        expected = FetchError(
            "network timeout",
            reason_code="NETWORK_TIMEOUT",
            retryable=True,
        )
        fetcher = RecordingFetcher(expected)

        with self.assertRaises(FetchError) as raised:
            SearxngSearchBackend("https://search.example.net").search(
                SearchQuery("query"),
                fetcher=fetcher,
            )

        self.assertIs(raised.exception, expected)
        self.assertEqual(len(fetcher.calls), 1)

    def test_public_configuration_contains_only_name_and_profile_digest(self):
        endpoint = "https://search.example.net/team/search"
        backend = SearxngSearchBackend(endpoint)

        configuration = backend.public_configuration()

        self.assertEqual(
            set(configuration),
            {
                "search_backend_kind",
                "search_backend_contract_version",
                "search_backend_profile_digest",
            },
        )
        self.assertEqual(configuration["search_backend_kind"], "searxng")
        self.assertEqual(configuration["search_backend_contract_version"], "1")
        self.assertRegex(
            configuration["search_backend_profile_digest"],
            r"\A[0-9a-f]{64}\Z",
        )
        self.assertNotIn(endpoint, repr(backend))
        self.assertNotIn(endpoint, json.dumps(configuration))

    def test_profile_digest_covers_canonical_endpoint(self):
        root = SearxngSearchBackend("https://search.example.net")
        explicit_search = SearxngSearchBackend(
            "https://search.example.net/search"
        )
        different_profile = SearxngSearchBackend(
            "https://search.example.net/team"
        )

        self.assertEqual(
            root.public_configuration()["search_backend_profile_digest"],
            explicit_search.public_configuration()[
                "search_backend_profile_digest"
            ],
        )
        self.assertNotEqual(
            root.public_configuration()["search_backend_profile_digest"],
            different_profile.public_configuration()[
                "search_backend_profile_digest"
            ],
        )

    def test_profile_digest_covers_server_profile(self):
        first = SearxngSearchBackend(
            "https://search.example.net",
            server_profile_digest="a" * 64,
        )
        second = SearxngSearchBackend(
            "https://search.example.net",
            server_profile_digest="b" * 64,
        )

        self.assertNotEqual(
            first.public_configuration()["search_backend_profile_digest"],
            second.public_configuration()["search_backend_profile_digest"],
        )
        for invalid in ("", "A" * 64, "not-a-digest"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "server profile"):
                    SearxngSearchBackend(
                        "https://search.example.net",
                        server_profile_digest=invalid,
                    )

    def test_endpoint_path_gets_one_search_suffix(self):
        cases = {
            "https://search.example.net": "/search",
            "https://search.example.net/": "/search",
            "https://search.example.net/team": "/team/search",
            "https://search.example.net/team/search": "/team/search",
            "http://localhost:8080/searx": "/searx/search",
            "http://[::1]:8080/search": "/search",
        }
        for endpoint, expected_path in cases.items():
            with self.subTest(endpoint=endpoint):
                fetcher = RecordingFetcher(Page(endpoint, '{"results": []}'))
                SearxngSearchBackend(endpoint).search(
                    SearchQuery("query"),
                    fetcher=fetcher,
                )
                self.assertEqual(
                    urlparse(fetcher.calls[0][0]).path,
                    expected_path,
                )

    def test_rejects_unsafe_or_ambiguous_endpoints(self):
        invalid = (
            "",
            " search.example.net ",
            "search.example.net",
            "ftp://search.example.net",
            "http://search.example.net",
            "http://localhost.example.net",
            "https://user:pass@search.example.net",
            "https://search.example.net?token=secret",
            "https://search.example.net#fragment",
            "https://search.example.net:invalid",
        )
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    SearxngSearchBackend(endpoint)

    def test_accepts_https_and_literal_loopback_http(self):
        valid = (
            "https://search.example.net",
            "http://localhost:8080",
            "http://127.0.0.1:8080",
            "http://127.42.1.9",
            "http://[::1]:8080",
        )
        for endpoint in valid:
            with self.subTest(endpoint=endpoint):
                SearxngSearchBackend(endpoint)


if __name__ == "__main__":
    unittest.main()
