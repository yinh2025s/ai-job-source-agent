import unittest

from job_source_agent.retrying_fetcher import RetryingFetcher
from job_source_agent.web import FetchError, Page


class RetryFetcherTests(unittest.TestCase):
    def test_retryable_error_is_retried_until_success(self):
        class FlakyFetcher:
            timeout = 1

            def __init__(self):
                self.calls = 0

            def fetch(self, url, data=None, headers=None):
                self.calls += 1
                if self.calls == 1:
                    raise FetchError("The read operation timed out")
                return Page(url=url, final_url=url, html="<html>ok</html>")

        base = FlakyFetcher()
        fetcher = RetryingFetcher(base, max_retries=1, base_delay=0)

        page = fetcher.fetch("https://example.com")

        self.assertEqual(page.html, "<html>ok</html>")
        self.assertEqual(base.calls, 2)
        self.assertEqual(fetcher.retry_events[0]["reason_code"], "NETWORK_TIMEOUT")
        self.assertTrue(fetcher.retry_events[0]["retryable"])

    def test_non_retryable_error_is_not_retried(self):
        class ForbiddenFetcher:
            timeout = 1

            def __init__(self):
                self.calls = 0

            def fetch(self, url, data=None, headers=None):
                self.calls += 1
                raise FetchError("HTTP Error 403: Forbidden")

        base = ForbiddenFetcher()
        fetcher = RetryingFetcher(base, max_retries=3, base_delay=0)

        with self.assertRaises(FetchError):
            fetcher.fetch("https://example.com")

        self.assertEqual(base.calls, 1)
        self.assertEqual(fetcher.retry_events[0]["reason_code"], "HTTP_FORBIDDEN")
        self.assertFalse(fetcher.retry_events[0]["retryable"])

    def test_retryable_error_raises_after_budget(self):
        class TimeoutFetcher:
            timeout = 1

            def __init__(self):
                self.calls = 0

            def fetch(self, url, data=None, headers=None):
                self.calls += 1
                raise FetchError("timeout")

        base = TimeoutFetcher()
        fetcher = RetryingFetcher(base, max_retries=2, base_delay=0)

        with self.assertRaises(FetchError):
            fetcher.fetch("https://example.com")

        self.assertEqual(base.calls, 3)
        self.assertEqual(len(fetcher.retry_events), 3)


if __name__ == "__main__":
    unittest.main()
