import unittest

from job_source_agent.page_cache import PageCacheFetcher
from job_source_agent.web import FetchError, Page


class RecordingFetcher:
    def __init__(self, *, fail=False, final_url=None):
        self.fail = fail
        self.final_url = final_url
        self.calls = []
        self.timeout = 4

    def fetch(self, url, data=None, headers=None):
        self.calls.append((url, data, headers))
        if self.fail:
            raise FetchError("temporary failure")
        return Page(
            url=url,
            final_url=self.final_url or url,
            html=f"response {len(self.calls)}",
        )


class PageCacheFetcherTests(unittest.TestCase):
    def test_reuses_successful_uncredentialed_get(self):
        base = RecordingFetcher()
        fetcher = PageCacheFetcher(base, max_entries=2)

        first = fetcher.fetch("https://example.test/jobs")
        second = fetcher.fetch("https://example.test/jobs")

        self.assertEqual(len(base.calls), 1)
        self.assertEqual(first.html, second.html)
        self.assertIsNot(first, second)
        self.assertEqual(fetcher.cache_hits, 1)
        self.assertEqual(fetcher.cache_misses, 1)

    def test_post_and_header_requests_are_never_cached(self):
        base = RecordingFetcher()
        fetcher = PageCacheFetcher(base)

        fetcher.fetch("https://example.test", data=b"{}")
        fetcher.fetch("https://example.test/", data=b"{}")
        fetcher.fetch("https://example.test", headers={"Accept": "text/html"})
        fetcher.fetch("https://example.test/", headers={"Accept": "text/html"})

        self.assertEqual(len(base.calls), 4)
        self.assertEqual(fetcher.cache_hits, 0)

    def test_failures_are_not_cached(self):
        base = RecordingFetcher(fail=True)
        fetcher = PageCacheFetcher(base)

        for _attempt in range(2):
            with self.assertRaises(FetchError):
                fetcher.fetch("https://example.test/jobs")

        self.assertEqual(len(base.calls), 2)

    def test_reuses_redirect_response_by_final_url(self):
        final_url = "https://careers.example.test/us/en"
        base = RecordingFetcher(final_url=final_url)
        fetcher = PageCacheFetcher(base)

        first = fetcher.fetch("https://careers.example.test")
        second = fetcher.fetch(final_url)

        self.assertEqual(len(base.calls), 1)
        self.assertEqual(first.html, second.html)
        self.assertEqual(fetcher.cache_hits, 1)
        self.assertEqual(fetcher.cache_misses, 1)

    def test_reuses_root_redirect_final_url_without_changing_page_identity(self):
        base = RecordingFetcher(final_url="https://www.akkodis.com/")
        fetcher = PageCacheFetcher(base)

        first = fetcher.fetch("https://akkodis.com")
        second = fetcher.fetch("https://www.akkodis.com")

        self.assertEqual(base.calls, [("https://akkodis.com", None, None)])
        self.assertEqual(first.url, "https://akkodis.com")
        self.assertEqual(first.final_url, "https://www.akkodis.com/")
        self.assertEqual(second.url, "https://akkodis.com")
        self.assertEqual(second.final_url, "https://www.akkodis.com/")

    def test_root_slash_spellings_reuse_in_both_directions(self):
        base = RecordingFetcher()
        fetcher = PageCacheFetcher(base)

        fetcher.fetch("https://first.example.test")
        fetcher.fetch("https://first.example.test/")
        fetcher.fetch("https://second.example.test/")
        fetcher.fetch("https://second.example.test")

        self.assertEqual(
            base.calls,
            [
                ("https://first.example.test", None, None),
                ("https://second.example.test/", None, None),
            ],
        )

    def test_root_alias_preserves_query_order_and_fragment(self):
        base = RecordingFetcher()
        fetcher = PageCacheFetcher(base)

        fetcher.fetch("HTTPS://EXAMPLE.test?b=2&a=1#top")
        fetcher.fetch("HTTPS://EXAMPLE.test/?b=2&a=1#top")
        fetcher.fetch("HTTPS://EXAMPLE.test/?a=1&b=2#top")
        fetcher.fetch("HTTPS://EXAMPLE.test/?b=2&a=1#other")

        self.assertEqual(
            [call[0] for call in base.calls],
            [
                "HTTPS://EXAMPLE.test?b=2&a=1#top",
                "HTTPS://EXAMPLE.test/?a=1&b=2#top",
                "HTTPS://EXAMPLE.test/?b=2&a=1#other",
            ],
        )

    def test_does_not_merge_non_root_trailing_slash(self):
        base = RecordingFetcher()
        fetcher = PageCacheFetcher(base)

        fetcher.fetch("https://example.test/jobs")
        fetcher.fetch("https://example.test/jobs/")

        self.assertEqual(len(base.calls), 2)

    def test_does_not_merge_http_and_https_roots(self):
        base = RecordingFetcher()
        fetcher = PageCacheFetcher(base)

        fetcher.fetch("http://example.test")
        fetcher.fetch("https://example.test/")

        self.assertEqual(len(base.calls), 2)

    def test_redirect_aliases_are_removed_when_entry_is_evicted(self):
        final_url = "https://careers.example.test/us/en"
        base = RecordingFetcher(final_url=final_url)
        fetcher = PageCacheFetcher(base, max_entries=1)

        fetcher.fetch("https://careers.example.test")
        base.final_url = None
        fetcher.fetch("https://other.example.test/jobs")
        fetcher.fetch(final_url)

        self.assertEqual(len(base.calls), 3)

    def test_canonical_root_alias_is_removed_when_entry_is_evicted(self):
        base = RecordingFetcher()
        fetcher = PageCacheFetcher(base, max_entries=1)

        fetcher.fetch("https://example.test")
        fetcher.fetch("https://other.example.test/jobs")
        fetcher.fetch("https://example.test/")

        self.assertEqual(len(base.calls), 3)

    def test_lru_bound_evicts_oldest_page(self):
        base = RecordingFetcher()
        fetcher = PageCacheFetcher(base, max_entries=2)

        fetcher.fetch("https://example.test/1")
        fetcher.fetch("https://example.test/2")
        fetcher.fetch("https://example.test/3")
        fetcher.fetch("https://example.test/1")

        self.assertEqual(len(base.calls), 4)


if __name__ == "__main__":
    unittest.main()
