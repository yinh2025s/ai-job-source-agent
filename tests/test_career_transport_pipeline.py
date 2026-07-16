import unittest

from job_source_agent.career_transport_budget import CareerTransportBudgetFetcher
from job_source_agent.errors import DiscoveryError
from job_source_agent.homepage_navigation import HomepageNavigationEvidence
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
    def test_verified_homepage_navigation_saves_homepage_dispatch(self):
        homepage = "https://company.example"
        careers = "https://company.example/careers"
        base = RecordingCareerFetcher(
            {careers: "<html><body>Open roles and careers</body></html>"}
        )
        agent = build_agent(base, limit=1)
        evidence = HomepageNavigationEvidence(
            homepage_url=homepage,
            candidate_urls=(careers,),
        )

        selected, trace = agent.find_career_page(
            homepage,
            company_name="Company",
            homepage_navigation_evidence=evidence,
        )

        self.assertEqual(selected, careers)
        self.assertEqual(base.calls, [careers])
        self.assertEqual(trace["homepage_navigation_evidence"]["candidate_count"], 1)
        self.assertIsNone(trace["homepage_navigation_evidence"]["fallback"])
        self.assertEqual(
            trace["transport_budget"]["by_phase"],
            {"verified_homepage_navigation_candidates": 1},
        )

    def test_www_equivalent_homepage_navigation_saves_homepage_dispatch(self):
        homepage = "https://company.example"
        careers = "https://www.company.example/careers"
        base = RecordingCareerFetcher(
            {careers: "<html><body>Open roles and careers</body></html>"}
        )
        agent = build_agent(base, limit=1)
        evidence = HomepageNavigationEvidence(
            homepage_url="https://www.company.example/",
            candidate_urls=(careers,),
        )

        selected, trace = agent.find_career_page(
            homepage,
            company_name="Company",
            homepage_navigation_evidence=evidence,
        )

        self.assertEqual(selected, careers)
        self.assertEqual(base.calls, [careers])
        self.assertEqual(
            trace["homepage_navigation_evidence"]["status"],
            "candidate_verification",
        )

    def test_non_www_subdomain_navigation_evidence_is_not_equivalent(self):
        homepage = "https://company.example"
        careers = "https://company.example/careers"
        base = RecordingCareerFetcher(
            {
                homepage: f'<a href="{careers}">Careers</a>',
                careers: "<html><body>Open roles and careers</body></html>",
            }
        )
        agent = build_agent(base, limit=2)
        evidence = HomepageNavigationEvidence(
            homepage_url="https://jobs.company.example",
            candidate_urls=("https://jobs.company.example/careers",),
        )

        selected, trace = agent.find_career_page(
            homepage,
            company_name="Company",
            homepage_navigation_evidence=evidence,
        )

        self.assertEqual(selected, careers)
        self.assertEqual(base.calls, [homepage, careers])
        self.assertEqual(
            trace["homepage_navigation_evidence"]["status"],
            "homepage_url_mismatch",
        )

    def test_mismatched_homepage_navigation_evidence_uses_homepage_fallback(self):
        homepage = "https://company.example"
        careers = "https://company.example/careers"
        base = RecordingCareerFetcher(
            {
                homepage: f'<a href="{careers}">Careers</a>',
                careers: "<html><body>Open roles and careers</body></html>",
            }
        )
        agent = build_agent(base, limit=2)
        evidence = HomepageNavigationEvidence(
            homepage_url="https://other.example",
            candidate_urls=("https://other.example/careers",),
        )

        selected, trace = agent.find_career_page(
            homepage,
            company_name="Company",
            homepage_navigation_evidence=evidence,
        )

        self.assertEqual(selected, careers)
        self.assertEqual(base.calls, [homepage, careers])
        self.assertEqual(
            trace["homepage_navigation_evidence"]["status"],
            "homepage_url_mismatch",
        )

    def test_failed_homepage_navigation_candidate_falls_back_without_retrying_it(self):
        homepage = "https://company.example"
        failed = "https://company.example/old-careers"
        careers = "https://company.example/careers"
        base = RecordingCareerFetcher(
            {
                homepage: (
                    f'<a href="{failed}">Careers</a>'
                    f'<a href="{careers}">Careers</a>'
                ),
                failed: FetchError(
                    "not found",
                    reason_code="HTTP_NOT_FOUND",
                    retryable=False,
                ),
                careers: "<html><body>Open roles and careers</body></html>",
            }
        )
        agent = build_agent(base, limit=3)
        evidence = HomepageNavigationEvidence(
            homepage_url=homepage,
            candidate_urls=(failed,),
        )

        selected, trace = agent.find_career_page(
            homepage,
            company_name="Company",
            homepage_navigation_evidence=evidence,
        )

        self.assertEqual(selected, careers)
        self.assertEqual(base.calls, [failed, homepage, careers])
        self.assertEqual(
            trace["homepage_navigation_evidence"]["status"],
            "no_verified_candidate",
        )

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

    def test_cached_homepage_preserves_budget_for_candidate_and_trace_counts_cache_hit(self):
        homepage = "https://company.example"
        jobs = "https://company.example/jobs"
        base = RecordingCareerFetcher(
            {
                homepage: f'<a href="{jobs}">Search jobs</a>',
                jobs: "<html><body>Open roles and careers</body></html>",
            }
        )
        agent = build_agent(base, limit=1)

        agent.fetcher.fetch(homepage)
        selected, trace = agent.find_career_page(homepage, company_name="Company")

        self.assertEqual(selected, jobs)
        self.assertEqual(base.calls, [homepage, jobs])
        self.assertEqual(
            trace["transport_budget"],
            {
                "policy": "stage_transport_dispatch_budget",
                "limit": 1,
                "dispatched": 1,
                "remaining": 0,
                "exhausted": True,
                "rejected": 0,
                "by_phase": {"homepage_and_common_paths_candidates": 1},
                "cache_hits": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
