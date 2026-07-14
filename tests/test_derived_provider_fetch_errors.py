import unittest
from urllib.parse import urlparse

from job_source_agent.errors import DiscoveryError
from job_source_agent.job_board import JobBoard
from job_source_agent.models import LinkCandidate
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.providers.base import AdapterResult, JobCandidate
from job_source_agent.providers.registry import ProviderRegistry
from job_source_agent.request_identity import build_request_identity
from job_source_agent.web import FetchError, Page


class DerivedProviderFetcher:
    def __init__(self, *, homepage_url=None, successful_tenant=None):
        self.homepage_url = homepage_url
        self.successful_tenant = successful_tenant
        self.requested = []

    def fetch(self, url, data=None, headers=None):
        self.requested.append(url)
        if url == self.homepage_url:
            return Page(url=url, final_url=url, html="<html><body></body></html>")
        if self.successful_tenant and f"/{self.successful_tenant}/" in url:
            return Page(url=url, html="ok")
        raise FetchError(
            "provider request timed out",
            status=503,
            reason_code="NETWORK_TIMEOUT",
            retryable=True,
            request_identity=build_request_identity(url, data=data, headers=headers).as_dict(),
        )


class DerivedAdapter:
    name = "derived_test"
    supports_listing = True

    def recognizes(self, url):
        return urlparse(url).hostname == "derived.example"

    def identify_board(self, url):
        if not self.recognizes(url):
            return None
        tenant = urlparse(url).path.strip("/").split("/", 1)[0]
        return JobBoard(url=f"https://derived.example/{tenant}", provider=self.name, identifier=tenant)

    def list_jobs(self, fetcher, board, query):
        fetcher.fetch(f"{board.url}/api")
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=[
                JobCandidate(
                    title="AI Engineer",
                    url=f"{board.url}/jobs/1",
                    provider=self.name,
                )
            ],
            trace={"tenant_identity_verified": True},
        )


def derived_candidate(tenant, *, score, trusted_configuration=False):
    return LinkCandidate(
        url=f"https://derived.example/{tenant}",
        text="",
        source_url="https://example.com",
        score=score,
        reasons=[
            "derived provider configuration"
            if trusted_configuration
            else "derived test provider board candidate"
        ],
        origin="derived_provider_config" if trusted_configuration else "blind_ats_probe",
    )


class DerivedProviderFetchErrorTests(unittest.TestCase):
    def test_adapter_fetch_error_retains_typed_projection(self):
        agent = JobSourceAgent(
            DerivedProviderFetcher(),
            provider_registry=ProviderRegistry((DerivedAdapter(),)),
        )

        decision = agent._verify_derived_provider_with_adapter(
            "https://derived.example/TimedOut",
            target_title="AI Engineer",
            trusted_configuration=False,
        )

        self.assertIsNotNone(decision)
        self.assertIsNone(decision[0])
        failure = decision[1]["fetch_failure"]
        self.assertEqual(failure["reason_code"], "NETWORK_TIMEOUT")
        self.assertEqual(failure["reason_code_source"], "exception")
        self.assertTrue(failure["retryable"])
        self.assertEqual(failure["status"], 503)
        self.assertEqual(
            failure["request_identity"]["sanitized_url"],
            agent.fetcher.requested[0],
        )

    def test_adapter_fetch_error_allows_later_candidate_and_stays_in_trace(self):
        fetcher = DerivedProviderFetcher(successful_tenant="HealthyTenant")
        agent = JobSourceAgent(
            fetcher,
            provider_registry=ProviderRegistry((DerivedAdapter(),)),
        )
        trace = {"candidate_fetch_errors": []}

        selected = agent._select_verified_career_candidate(
            [
                derived_candidate("TimedOut", score=200),
                derived_candidate("HealthyTenant", score=190),
            ],
            trace,
            max_fetches=2,
            target_title="AI Engineer",
        )

        self.assertEqual(selected, "https://derived.example/HealthyTenant")
        self.assertEqual(len(trace["candidate_fetch_errors"]), 2)
        failure = next(
            item
            for item in trace["candidate_fetch_errors"]
            if item.get("provider") == "derived_test"
        )
        self.assertEqual(failure["reason_code"], "NETWORK_TIMEOUT")
        self.assertTrue(failure["retryable"])
        self.assertEqual(failure["provider"], "derived_test")
        self.assertIsNotNone(failure["request_identity"])

    def test_retryable_adapter_failure_controls_final_discovery_semantics(self):
        homepage = "https://example.com"
        candidate = derived_candidate("TimedOut", score=200, trusted_configuration=True)

        class DerivedOnlyAgent(JobSourceAgent):
            def _common_path_candidates(self, homepage_url):
                return []

            def _ats_board_candidates(self, company_name, homepage_url):
                return [candidate]

        agent = DerivedOnlyAgent(
            DerivedProviderFetcher(homepage_url=homepage),
            provider_registry=ProviderRegistry((DerivedAdapter(),)),
            max_ats_board_fetches=1,
            enable_sitemap_discovery=False,
            enable_career_search=False,
        )

        with self.assertRaises(DiscoveryError) as raised:
            agent.find_career_page(
                homepage,
                company_name="Example",
                target_title="AI Engineer",
            )

        self.assertEqual(raised.exception.code, "NETWORK_TIMEOUT")
        failure = raised.exception.trace["ats_board_discovery"]["candidate_fetch_errors"][0]
        self.assertEqual(failure["reason_code"], "NETWORK_TIMEOUT")
        self.assertTrue(failure["retryable"])
        self.assertEqual(failure["status"], 503)
        self.assertIsNotNone(failure["request_identity"])


if __name__ == "__main__":
    unittest.main()
