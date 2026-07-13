import unittest

from job_source_agent.retrying_fetcher import RetryingFetcher
from job_source_agent.web import FetchError, Page


class SequenceFetcher:
    timeout = 1

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def fetch(self, url, data=None, headers=None):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return response


class RetryFetcherTests(unittest.TestCase):
    def test_retryable_error_is_retried_until_success_and_traced(self):
        page = Page(url="https://example.com", final_url="https://example.com", html="ok")
        base = SequenceFetcher([FetchError("read timed out"), page])
        delays = []
        fetcher = RetryingFetcher(
            base, max_retries=1, base_delay=2, jitter_ratio=0.25,
            rng=lambda: 0.5, sleeper=delays.append,
        )

        self.assertIs(fetcher.fetch("https://example.com"), page)
        self.assertEqual(base.calls, 2)
        self.assertEqual(delays, [2.0])
        self.assertEqual(
            [event["outcome"] for event in fetcher.retry_events],
            ["retry_scheduled", "succeeded"],
        )
        self.assertEqual(fetcher.retry_events[0]["reason_code"], "NETWORK_TIMEOUT")
        self.assertEqual(fetcher.retry_events[0]["delay"], 2.0)

    def test_exponential_jitter_is_bounded_and_rng_is_clamped(self):
        delays = []
        samples = iter([-3, 1, 99])
        base = SequenceFetcher([FetchError("timeout")] * 3 + [Page("u", "ok")])
        fetcher = RetryingFetcher(
            base, max_retries=3, base_delay=2, backoff_factor=2,
            max_delay=5, jitter_ratio=0.5, rng=lambda: next(samples), sleeper=delays.append,
        )

        fetcher.fetch("u")

        self.assertEqual(delays, [1.0, 5.0, 5.0])
        self.assertTrue(all(0 <= delay <= 5 for delay in delays))

    def test_retryable_matrix(self):
        cases = {
            "HTTP Error 429: Too Many Requests": "RATE_LIMITED",
            "HTTP Error 500: Internal Server Error": "SERVER_ERROR",
            "HTTP status 599": "SERVER_ERROR",
            "The read operation timed out": "NETWORK_TIMEOUT",
            "Temporary failure in name resolution": "DNS_FAILED",
        }
        for message, reason in cases.items():
            with self.subTest(message=message):
                page = Page("u", "ok")
                base = SequenceFetcher([FetchError(message), page])
                fetcher = RetryingFetcher(base, max_retries=1, base_delay=0)
                self.assertIs(fetcher.fetch("u"), page)
                self.assertEqual(base.calls, 2)
                self.assertEqual(fetcher.retry_events[0]["reason_code"], reason)

    def test_non_retryable_matrix_preserves_original_fetch_error(self):
        cases = {
            "HTTP Error 403: Forbidden": "HTTP_FORBIDDEN",
            "Login required": "LOGIN_REQUIRED",
            "parser mismatch for jobs payload": "PARSING_FAILED",
            "invalid structured data": "PARSING_FAILED",
        }
        for message, reason in cases.items():
            with self.subTest(message=message):
                expected = FetchError(message)
                base = SequenceFetcher([expected])
                fetcher = RetryingFetcher(base, max_retries=3, base_delay=0)
                with self.assertRaises(FetchError) as raised:
                    fetcher.fetch("u")
                self.assertIs(raised.exception, expected)
                self.assertEqual(base.calls, 1)
                self.assertEqual(fetcher.retry_events[0]["reason_code"], reason)
                self.assertEqual(fetcher.retry_events[0]["outcome"], "not_retryable")

    def test_retry_budget_exhaustion_preserves_last_error(self):
        errors = [FetchError("timeout one"), FetchError("timeout two")]
        base = SequenceFetcher(errors)
        fetcher = RetryingFetcher(base, max_retries=1, base_delay=0)

        with self.assertRaises(FetchError) as raised:
            fetcher.fetch("u")

        self.assertIs(raised.exception, errors[-1])
        self.assertEqual(base.calls, 2)
        self.assertEqual(fetcher.retry_events[-1]["outcome"], "retry_budget_exhausted")

    def test_deadline_prevents_sleep_and_next_attempt(self):
        now = [10.0]
        expected = FetchError("timeout")
        base = SequenceFetcher([expected])
        sleeps = []
        fetcher = RetryingFetcher(
            base, max_retries=3, base_delay=1, jitter_ratio=0,
            sleeper=sleeps.append, clock=lambda: now[0], deadline=10.5,
        )

        with self.assertRaises(FetchError) as raised:
            fetcher.fetch("u")

        self.assertIs(raised.exception, expected)
        self.assertEqual(base.calls, 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(fetcher.retry_events[-1]["outcome"], "deadline_exhausted")

    def test_callable_deadline_is_observed_between_attempts(self):
        now = [0.0]
        errors = [FetchError("timeout one"), FetchError("timeout two")]
        base = SequenceFetcher(errors)

        def sleep(delay):
            now[0] += delay

        fetcher = RetryingFetcher(
            base, max_retries=3, base_delay=0.25, jitter_ratio=0,
            sleeper=sleep, clock=lambda: now[0], deadline=lambda: 0.5,
        )
        with self.assertRaises(FetchError) as raised:
            fetcher.fetch("u")

        self.assertIs(raised.exception, errors[-1])
        self.assertEqual(base.calls, 2)
        self.assertEqual(fetcher.retry_events[-1]["outcome"], "deadline_exhausted")

    def test_deadline_clamps_each_underlying_fetch_timeout_and_restores_it(self):
        class TimeoutRecordingFetcher(SequenceFetcher):
            timeout = 8

            def __init__(self):
                super().__init__([Page("u", "ok")])
                self.observed_timeouts = []

            def fetch(self, url, data=None, headers=None):
                self.observed_timeouts.append(self.timeout)
                return super().fetch(url, data=data, headers=headers)

        base = TimeoutRecordingFetcher()
        fetcher = RetryingFetcher(base, max_retries=0, clock=lambda: 10.0, deadline=12.5)

        fetcher.fetch("u")

        self.assertEqual(base.observed_timeouts, [2.5])
        self.assertEqual(base.timeout, 8)

    def test_expired_deadline_prevents_initial_fetch(self):
        base = SequenceFetcher([Page("u", "ok")])
        fetcher = RetryingFetcher(base, max_retries=0, clock=lambda: 10.0, deadline=10.0)

        with self.assertRaisesRegex(FetchError, "caller deadline"):
            fetcher.fetch("u")

        self.assertEqual(base.calls, 0)


if __name__ == "__main__":
    unittest.main()
