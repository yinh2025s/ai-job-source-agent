import unittest
from concurrent.futures import ThreadPoolExecutor

from job_source_agent.career_transport_budget import CareerTransportBudgetFetcher
from job_source_agent.page_cache import PageCacheFetcher
from job_source_agent.retrying_fetcher import RetryingFetcher
from job_source_agent.web import FetchError, Page


class RecordingFetcher:
    timeout = 7.0
    capability = "delegate-capability"

    def __init__(self, failures=0):
        self.failures = failures
        self.calls = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append((url, data, headers))
        if len(self.calls) <= self.failures:
            raise FetchError("temporary timeout", retryable=True)
        return Page(url=url, html="ok")

    def remaining_fetch_seconds(self):
        return 2.5


class CareerTransportBudgetFetcherTests(unittest.TestCase):
    def test_exact_limit_dispatches_and_n_plus_one_is_rejected(self):
        base = RecordingFetcher()
        fetcher = CareerTransportBudgetFetcher(base)

        with fetcher.career_discovery_scope(2) as budget:
            fetcher.fetch("https://example.test/one")
            fetcher.fetch("https://example.test/two")
            with self.assertRaises(FetchError) as raised:
                fetcher.fetch(
                    "https://secret.example.test/three?token=private",
                    data=b"private body",
                    headers={"Authorization": "secret"},
                )

        self.assertEqual(len(base.calls), 2)
        self.assertEqual(raised.exception.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(
            budget.snapshot(),
            {
                "limit": 2,
                "dispatched": 2,
                "remaining": 0,
                "exhausted": True,
                "rejected": 1,
                "by_phase": {},
            },
        )
        self.assertNotIn("secret", repr(budget.snapshot()))
        self.assertNotIn("private", repr(budget.snapshot()))

    def test_unbounded_scope_and_fetches_outside_scope_delegate(self):
        base = RecordingFetcher()
        fetcher = CareerTransportBudgetFetcher(base)

        fetcher.fetch("outside-before")
        with fetcher.career_discovery_scope(None) as budget:
            for index in range(3):
                fetcher.fetch(f"inside-{index}")
        fetcher.fetch("outside-after")

        self.assertEqual(len(base.calls), 5)
        self.assertEqual(
            budget.snapshot(),
            {
                "limit": None,
                "dispatched": 3,
                "remaining": None,
                "exhausted": False,
                "rejected": 0,
                "by_phase": {},
            },
        )

    def test_nested_phases_attribute_dispatch_to_innermost_phase(self):
        fetcher = CareerTransportBudgetFetcher(RecordingFetcher())

        with fetcher.career_discovery_scope(4) as budget:
            with fetcher.career_discovery_phase("search"):
                fetcher.fetch("one")
                with fetcher.career_discovery_phase("provider"):
                    fetcher.fetch("two")
                fetcher.fetch("three")
            with fetcher.career_discovery_phase("adapter"):
                fetcher.fetch("four")

        self.assertEqual(
            budget.snapshot()["by_phase"],
            {"adapter": 1, "provider": 1, "search": 2},
        )

    def test_invalid_limits_nested_scopes_and_orphan_phase_are_rejected(self):
        fetcher = CareerTransportBudgetFetcher(RecordingFetcher())

        for invalid in (-1,):
            with self.subTest(limit=invalid), self.assertRaises(ValueError):
                with fetcher.career_discovery_scope(invalid):
                    pass
        for invalid in (True, 1.5, "2"):
            with self.subTest(limit=invalid), self.assertRaises(TypeError):
                with fetcher.career_discovery_scope(invalid):
                    pass

        with fetcher.career_discovery_scope(1):
            with self.assertRaises(RuntimeError):
                with fetcher.career_discovery_scope(1):
                    pass
        with self.assertRaises(RuntimeError):
            with fetcher.career_discovery_phase("search"):
                pass

    def test_timeout_remaining_budget_and_other_capabilities_delegate(self):
        base = RecordingFetcher()
        fetcher = CareerTransportBudgetFetcher(base)

        self.assertEqual(fetcher.timeout, 7.0)
        fetcher.timeout = 1.25
        self.assertEqual(base.timeout, 1.25)
        self.assertEqual(fetcher.remaining_fetch_seconds(), 2.5)
        self.assertEqual(fetcher.capability, "delegate-capability")

    def test_concurrent_dispatches_cannot_exceed_limit(self):
        base = RecordingFetcher()
        fetcher = CareerTransportBudgetFetcher(base)

        def dispatch(index):
            try:
                fetcher.fetch(f"request-{index}")
                return "dispatched"
            except FetchError as error:
                self.assertEqual(error.reason_code, "FETCH_BUDGET_EXHAUSTED")
                return "rejected"

        with fetcher.career_discovery_scope(10) as budget:
            with ThreadPoolExecutor(max_workers=8) as pool:
                outcomes = list(pool.map(dispatch, range(32)))

        self.assertEqual(outcomes.count("dispatched"), 10)
        self.assertEqual(outcomes.count("rejected"), 22)
        self.assertEqual(len(base.calls), 10)
        self.assertEqual(budget.snapshot()["dispatched"], 10)
        self.assertEqual(budget.snapshot()["rejected"], 22)

    def test_outer_page_cache_makes_cache_hits_cost_zero(self):
        base = RecordingFetcher()
        budgeted = CareerTransportBudgetFetcher(base)
        fetcher = PageCacheFetcher(budgeted)

        with fetcher.career_discovery_scope(1) as budget:
            fetcher.fetch("https://example.test/jobs")
            fetcher.fetch("https://example.test/jobs")

        self.assertEqual(len(base.calls), 1)
        self.assertEqual(budget.snapshot()["dispatched"], 1)
        self.assertEqual(fetcher.cache_hits, 1)

    def test_outer_retry_makes_each_attempt_cost_one(self):
        base = RecordingFetcher(failures=1)
        budgeted = CareerTransportBudgetFetcher(base)
        fetcher = RetryingFetcher(budgeted, max_retries=1, base_delay=0)

        with fetcher.career_discovery_scope(2) as budget:
            page = fetcher.fetch("https://example.test/jobs")

        self.assertEqual(page.html, "ok")
        self.assertEqual(len(base.calls), 2)
        self.assertEqual(budget.snapshot()["dispatched"], 2)

        base = RecordingFetcher(failures=1)
        budgeted = CareerTransportBudgetFetcher(base)
        fetcher = RetryingFetcher(budgeted, max_retries=1, base_delay=0)
        with fetcher.career_discovery_scope(1) as budget:
            with self.assertRaises(FetchError) as raised:
                fetcher.fetch("https://example.test/jobs")

        self.assertEqual(raised.exception.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertEqual(len(base.calls), 1)
        self.assertEqual(budget.snapshot()["dispatched"], 1)
        self.assertEqual(budget.snapshot()["rejected"], 1)


if __name__ == "__main__":
    unittest.main()
