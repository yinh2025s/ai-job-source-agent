import json
import unittest
from urllib.parse import parse_qs, urlparse

from job_source_agent.composition import build_application_from_fetcher
from job_source_agent.models import (
    STAGE_CAREER_DISCOVERY,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
    STAGE_RESULT_VALIDATION,
    STAGE_WEBSITE_RESOLUTION,
    CompanyInput,
)
from job_source_agent.run_configuration import AgentConfig
from job_source_agent.web import FetchError, Page


ORACLE_DETAIL = (
    "https://eohh.fa.us2.oraclecloud.com/hcmUI/"
    "CandidateExperience/en/sites/CX_1/job/425798"
)
WORKDAY_BOARD = "https://acme.wd5.myworkdayjobs.com/en-US/acme"
WORKDAY_API = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/acme/jobs"
WORKDAY_DETAIL = (
    "https://acme.wd5.myworkdayjobs.com/en-US/acme/"
    "job/Remote/AI-Engineer_R123"
)


class FrozenSearchBackend:
    """A deterministic search boundary; results remain untrusted URL leads."""

    def __init__(self, urls, *, target_title="AI Engineer"):
        self.urls = tuple(urls)
        self.target_title = target_title
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        query = parse_qs(urlparse(url).query).get("q", [""])[0]
        urls = self.urls if self.target_title.casefold() in query.casefold() else ()
        items = "".join(f"<item><link>{item}</link></item>" for item in urls)
        return Page(
            url=url,
            final_url=url,
            html=f"<rss><channel>{items}</channel></rss>",
            source="frozen-search",
        )


class FrozenFetcher:
    def __init__(self, search_backend, routes=None):
        self.search_backend = search_backend
        self.routes = dict(routes or {})
        self.calls = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append({"url": url, "data": data, "headers": headers})
        if urlparse(url).hostname in {"www.bing.com", "html.duckduckgo.com"}:
            return self.search_backend.fetch(url)
        response = self.routes.get(url)
        if callable(response):
            response = response(url, data, headers)
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, Page):
            return response
        if isinstance(response, str):
            return Page(url=url, final_url=url, html=response, source="frozen-provider")
        raise FetchError(
            f"No frozen response for {url}",
            reason_code="CONNECTION_FAILED",
            retryable=False,
        )


def oracle_job_page(organization):
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "identifier": {"value": "425798"},
        "title": "AI Engineer",
        "url": ORACLE_DETAIL,
        "hiringOrganization": {"@type": "Organization", "name": organization},
        "jobLocationType": "TELECOMMUTE",
        "validThrough": "2099-01-01T00:00:00Z",
    }
    return '<script type="application/ld+json">' + json.dumps(posting) + "</script>"


def workday_inventory(*, tenant="acme", total=1, count=1, include_target=True):
    return json.dumps(
        {
            "total": total,
            "jobPostings": [
                {
                    "title": (
                        "AI Engineer"
                        if include_target and index == 0
                        else f"Other Role {index}"
                    ),
                    "externalPath": (
                        "/job/Remote/AI-Engineer_R123"
                        if index == 0
                        else f"/job/Remote/Other-Role-{index}_R{index}"
                    ),
                    "locationsText": "Remote",
                }
                for index in range(count)
            ],
            "tenant": tenant,
        }
    )


class TargetedCandidatePipelineE2ETests(unittest.TestCase):
    def run_pipeline(self, fetcher, *, company_name="Acme", website=""):
        application = build_application_from_fetcher(
            fetcher,
            AgentConfig(
                max_candidates=2,
                max_job_pages=2,
                max_job_board_attempts=2,
                max_career_candidate_fetches=2,
                max_career_search_queries=2,
                max_ats_board_fetches=2,
                enable_sitemap_discovery=False,
                enable_career_search=True,
                enable_parallel_candidate_discovery=True,
            ),
        )
        return application.pipeline.discover(
            CompanyInput(
                company_name=company_name,
                company_website_url=website,
                job_title="AI Engineer",
                job_location="Remote",
                source="frozen_contract_fixture",
            )
        )

    @staticmethod
    def statuses(result):
        return {stage.stage: stage.status for stage in result.stage_results}

    @staticmethod
    def s5_trace(result):
        return result.trace["stages"][STAGE_JOB_BOARD_DISCOVERY]

    def assert_bounded_targeted_search(self, result, backend):
        trace = self.s5_trace(result)
        pool = trace["candidate_discovery"]["pool"]
        self.assertLessEqual(pool["candidate_count"], 2)
        targeted_queries = {
            parse_qs(urlparse(url).query).get("q", [""])[0]
            for url in backend.calls
            if "AI Engineer" in parse_qs(urlparse(url).query).get("q", [""])[0]
        }
        self.assertLessEqual(len(targeted_queries), 2)
        self.assertTrue(trace["selected"]["source_kind"].startswith("targeted_"))

    def run_oracle_pipeline(self, fetcher):
        try:
            return self.run_pipeline(fetcher)
        except ValueError as exc:
            if str(exc) == "Job board locator is not replay-safe for this provider":
                raise AssertionError(
                    "Production S5 rejects OracleHCMAdapter's replay_safe exact-detail "
                    "locator because job_board._REPLAY_SAFE_POLICIES has no oracle_hcm "
                    "policy; targeted Oracle candidates cannot reach native inventory."
                ) from None
            raise

    def test_s2_empty_oracle_search_detail_native_inventory_reaches_s7_exact(self):
        backend = FrozenSearchBackend([ORACLE_DETAIL])
        fetcher = FrozenFetcher(backend, {ORACLE_DETAIL: oracle_job_page("Acme")})

        result = self.run_oracle_pipeline(fetcher)

        statuses = self.statuses(result)
        self.assertEqual(statuses[STAGE_WEBSITE_RESOLUTION], "failed")
        self.assertEqual(statuses[STAGE_CAREER_DISCOVERY], "not_run")
        self.assertEqual(statuses[STAGE_JOB_BOARD_DISCOVERY], "success")
        self.assertEqual(statuses[STAGE_OPENING_MATCH], "success")
        self.assertEqual(statuses[STAGE_RESULT_VALIDATION], "success")
        self.assertEqual(result.open_position_url, ORACLE_DETAIL)
        self.assertEqual(result.identity_assertion["verdict"], "verified")
        self.assertEqual(
            result.identity_assertion["hiring"]["verification_method"],
            "provider_inventory",
        )
        attempt = result.trace["stages"][STAGE_OPENING_MATCH]["board_portfolio"][
            "attempts"
        ][0]
        inventory = attempt["trace"]["provider_api"]["inventory"]
        self.assertEqual(inventory["source"], "native_adapter")
        self.assertTrue(inventory["complete"])
        self.assert_bounded_targeted_search(result, backend)

    def test_s4_unreachable_workday_search_native_tenant_reaches_s7_exact(self):
        backend = FrozenSearchBackend([WORKDAY_BOARD])
        fetcher = FrozenFetcher(
            backend,
            {
                "https://acme.example": "<title>Acme</title><main>Acme products</main>",
                WORKDAY_API: workday_inventory(),
            },
        )

        result = self.run_pipeline(fetcher, website="https://acme.example")

        statuses = self.statuses(result)
        self.assertEqual(statuses[STAGE_WEBSITE_RESOLUTION], "success")
        self.assertEqual(statuses[STAGE_CAREER_DISCOVERY], "failed")
        self.assertEqual(statuses[STAGE_JOB_BOARD_DISCOVERY], "success")
        self.assertEqual(statuses[STAGE_OPENING_MATCH], "success")
        self.assertEqual(statuses[STAGE_RESULT_VALIDATION], "success")
        self.assertEqual(result.open_position_url, WORKDAY_DETAIL)
        self.assertEqual(result.identity_assertion["verdict"], "verified")
        self.assertEqual(result.identity_assertion["provider"]["tenant"], "acme/acme")
        self.assert_bounded_targeted_search(result, backend)

    def test_wrong_oracle_hiring_organization_is_rejected_by_s7(self):
        backend = FrozenSearchBackend([ORACLE_DETAIL])
        fetcher = FrozenFetcher(
            backend,
            {ORACLE_DETAIL: oracle_job_page("Unrelated Health System")},
        )

        result = self.run_oracle_pipeline(fetcher)

        self.assertEqual(self.statuses(result)[STAGE_RESULT_VALIDATION], "failed")
        self.assertEqual(result.identity_assertion["verdict"], "rejected")
        self.assertIn(
            "PROVIDER_RELATIONSHIP_UNVERIFIED",
            result.identity_assertion["failure_codes"],
        )

    def test_cross_tenant_workday_result_is_rejected_by_s7(self):
        other_board = "https://other.wd5.myworkdayjobs.com/en-US/other"
        other_api = "https://other.wd5.myworkdayjobs.com/wday/cxs/other/other/jobs"
        backend = FrozenSearchBackend([other_board])
        fetcher = FrozenFetcher(
            backend,
            {
                "https://acme.example": "<title>Acme</title><main>Acme products</main>",
                other_api: workday_inventory(tenant="other"),
            },
        )

        result = self.run_pipeline(fetcher, website="https://acme.example")

        self.assertEqual(self.statuses(result)[STAGE_RESULT_VALIDATION], "failed")
        self.assertEqual(result.identity_assertion["verdict"], "rejected")
        self.assertIn(
            "PROVIDER_RELATIONSHIP_UNVERIFIED",
            result.identity_assertion["failure_codes"],
        )

    def test_search_snippet_without_provider_evidence_cannot_be_exact(self):
        backend = FrozenSearchBackend([WORKDAY_BOARD])
        fetcher = FrozenFetcher(
            backend,
            {"https://acme.example": "<title>Acme</title><main>Acme products</main>"},
        )

        result = self.run_pipeline(fetcher, website="https://acme.example")

        self.assertIsNone(result.open_position_url)
        self.assertNotEqual(result.identity_assertion["verdict"], "verified")
        opening_trace = result.trace["stages"][STAGE_OPENING_MATCH]
        self.assertNotEqual(
            opening_trace["board_portfolio"]["attempts"][0]["status"],
            "exact",
        )

    def test_incomplete_native_inventory_cannot_claim_verified_no_match(self):
        backend = FrozenSearchBackend([WORKDAY_BOARD])

        def partial_inventory(url, data, headers):
            offset = json.loads(data)["offset"]
            if offset:
                raise FetchError(
                    "frozen page two timeout",
                    reason_code="NETWORK_TIMEOUT",
                    retryable=True,
                )
            return Page(
                url=url,
                final_url=url,
                html=workday_inventory(total=40, count=20, include_target=False),
                source="frozen-provider",
            )

        fetcher = FrozenFetcher(
            backend,
            {
                "https://acme.example": "<title>Acme</title><main>Acme products</main>",
                WORKDAY_API: partial_inventory,
            },
        )

        result = self.run_pipeline(fetcher, website="https://acme.example")

        opening_trace = result.trace["stages"][STAGE_OPENING_MATCH]
        attempt_trace = opening_trace["board_portfolio"]["attempts"][0]["trace"]
        inventory = attempt_trace["provider_api"]["inventory"]
        self.assertFalse(inventory["complete"])
        self.assertIsNone(result.open_position_url)
        self.assertNotEqual(result.identity_assertion["verdict"], "verified")
        self.assertNotEqual(
            self.statuses(result)[STAGE_OPENING_MATCH],
            "success",
        )


if __name__ == "__main__":
    unittest.main()
