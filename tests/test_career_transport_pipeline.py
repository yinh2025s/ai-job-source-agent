import unittest

from job_source_agent.career_transport_budget import CareerTransportBudgetFetcher
from job_source_agent.errors import DiscoveryError
from job_source_agent.page_cache import PageCacheFetcher
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import FetchError, Page


class RecordingCareerFetcher:
    timeout = 1.0

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append(url)
        value = self.pages.get(url)
        if isinstance(value, FetchError):
            raise value
        if value is None:
            raise FetchError(
                f"not found: {url}",
                reason_code="HTTP_NOT_FOUND",
                retryable=False,
            )
        return Page(url=url, final_url=url, html=value, source="fixture")


def build_agent(base, *, limit):
    fetcher = PageCacheFetcher(CareerTransportBudgetFetcher(base))
    return JobSourceAgent(
        fetcher,
        max_candidates=2,
        max_career_candidate_fetches=2,
        max_career_discovery_transport_calls=limit,
        max_ats_board_fetches=0,
        enable_sitemap_discovery=False,
        enable_career_search=False,
    )


class CareerTransportPipelineTests(unittest.TestCase):
    def test_one_scope_stops_before_dispatch_after_homepage_and_candidate(self):
        homepage = "https://company.example"
        first = "https://company.example/careers-one"
        second = "https://company.example/careers-two"
        base = RecordingCareerFetcher(
            {
                homepage: (
                    f'<a href="{first}">Careers</a>'
                    f'<a href="{second}">Careers</a>'
                ),
                first: FetchError(
                    "temporary timeout",
                    reason_code="NETWORK_TIMEOUT",
                    retryable=True,
                ),
            }
        )
        agent = build_agent(base, limit=2)

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_career_page(homepage, company_name="Company")

        self.assertEqual(raised.exception.code, "FETCH_BUDGET_EXHAUSTED")
        self.assertEqual(base.calls, [homepage, first])
        self.assertEqual(
            raised.exception.trace["transport_budget"],
            {
                "policy": "stage_transport_dispatch_budget",
                "limit": 2,
                "dispatched": 2,
                "remaining": 0,
                "exhausted": True,
                "rejected": 1,
                "by_phase": {
                    "homepage": 1,
                    "homepage_and_common_paths_candidates": 1,
                },
                "cache_hits": 0,
            },
        )

    def test_success_trace_reports_stage_wide_dispatches(self):
        homepage = "https://company.example"
        jobs = "https://company.example/jobs"
        base = RecordingCareerFetcher(
            {
                homepage: f'<a href="{jobs}">Search jobs</a>',
                jobs: "<html><body>Open roles and careers</body></html>",
            }
        )
        agent = build_agent(base, limit=2)

        selected, trace = agent.find_career_page(homepage, company_name="Company")

        self.assertEqual(selected, jobs)
        self.assertEqual(base.calls, [homepage, jobs])
        self.assertEqual(trace["transport_budget"]["dispatched"], 2)
        self.assertEqual(trace["transport_budget"]["rejected"], 0)
        self.assertEqual(
            trace["transport_budget"]["by_phase"],
            {
                "homepage": 1,
                "homepage_and_common_paths_candidates": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
