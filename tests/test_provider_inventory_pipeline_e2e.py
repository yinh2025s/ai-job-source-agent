from __future__ import annotations

from dataclasses import dataclass
import json
import unittest
from urllib.parse import urlparse

from job_source_agent.application_runner import ApplicationRunner
from job_source_agent.models import CompanyInput
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.pipeline_application import PipelineApplication
from job_source_agent.providers import ProviderRegistry
from job_source_agent.providers.cws import CWSAdapter
from job_source_agent.providers.smartrecruiters import SmartRecruitersAdapter
from job_source_agent.stages import (
    CareerDiscoveryStage,
    HiringIdentityResolutionStage,
    JobBoardDiscoveryStage,
    OpeningMatchStage,
    ResultValidationStage,
)
from job_source_agent.web import Page


COMPANY = "Example Systems"
WEBSITE = "https://www.example-systems.test"
CWS_CAREERS = "https://careers.example-systems.test/open-roles"
CWS_API = "https://jobsapi-google.m-cloud.io/api/"
CWS_ORG = "companies/11111111-2222-3333-4444-555555555555"
SMART_CAREERS = "https://work.example-systems.test/open-roles"
SMART_TENANT = "ExampleSystems"


@dataclass(frozen=True)
class _ResolvedIdentity:
    hiring_entity_name: str
    career_root_url: str
    official_website_url: str = WEBSITE
    relationship_type: str = "same_entity"
    relationship_verified: bool = True
    verification_method: str = "same_entity"
    evidence_url: str = WEBSITE


class _IdentityResolver:
    def __init__(self, career_root_url: str, *, verified: bool = True) -> None:
        self.identity = _ResolvedIdentity(
            hiring_entity_name=COMPANY if verified else "Unverified Client",
            career_root_url=career_root_url,
            relationship_type="same_entity" if verified else "alternate_employer",
            relationship_verified=verified,
            verification_method="same_entity" if verified else "input_asserted",
        )

    def resolve(self, *_args):
        return self.identity, {"fixture": "verified_same_entity"}


class _FixtureFetcher:
    def __init__(
        self,
        career_url: str,
        career_html: str,
        inventory,
        *,
        api_final_url: str | None = None,
    ) -> None:
        self.career_url = career_url
        self.career_html = career_html
        self.inventory = inventory
        self.api_final_url = api_final_url
        self.requests: list[str] = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append(url)
        if url.rstrip("/") == self.career_url.rstrip("/"):
            return Page(url=url, final_url=self.career_url, html=self.career_html, source="fixture-page")
        if url.startswith(CWS_API + "job?") or url.startswith(
            f"https://api.smartrecruiters.com/v1/companies/{SMART_TENANT}/postings?"
        ):
            payload = self.inventory(url) if callable(self.inventory) else self.inventory
            return Page(
                url=url,
                final_url=self.api_final_url or url,
                html=payload if isinstance(payload, str) else json.dumps(payload),
                source="fixture-inventory",
            )
        parsed = urlparse(url)
        if (
            parsed.scheme == "https"
            and parsed.netloc == "jobs.smartrecruiters.com"
            and parsed.path.rstrip("/") == f"/{SMART_TENANT}"
        ):
            return Page(url=url, final_url=url, html="<html><body></body></html>", source="fixture-board")
        raise AssertionError(f"unexpected fixture request: {url}")


def _cws_page() -> str:
    return f"""
      <script>
        CWS.jobs.set_api({json.dumps(CWS_API)});
        CWS.jobs.set_options({{
          org_id: {json.dumps(CWS_ORG)},
          jobdetail_path: "/job-description",
          limit: 100
        }});
      </script>
    """


def _smart_page() -> str:
    return (
        '<script src="https://static.smartrecruiters.com/job-widget/widget.js"></script>'
        "<script>SmartRecruitersWidget.init({"
        f'company_code: "{SMART_TENANT}", '
        'api_url: "https://api.smartrecruiters.com/v1/companies/", '
        'job_ad_url: "https://jobs.smartrecruiters.com/"'
        "});</script>"
    )


def _application(fetcher, career_url: str, *, identity_verified: bool = True):
    registry = ProviderRegistry((CWSAdapter(), SmartRecruitersAdapter()))
    agent = JobSourceAgent(
        fetcher,
        provider_registry=registry,
        enable_career_search=False,
        max_ats_board_fetches=0,
    )
    runner = ApplicationRunner(
        (
            HiringIdentityResolutionStage(
                _IdentityResolver(career_url, verified=identity_verified)
            ),
            CareerDiscoveryStage(agent),
            JobBoardDiscoveryStage(agent, registry),
            OpeningMatchStage(agent, registry),
            ResultValidationStage(),
        )
    )
    return PipelineApplication(runner)


def _discover(fetcher, career_url: str, *, identity_verified: bool = True):
    return _application(
        fetcher,
        career_url,
        identity_verified=identity_verified,
    ).discover(
        CompanyInput(
            company_name=COMPANY,
            company_website_url=WEBSITE,
            job_title="Platform Engineer",
            job_location="Paris, FR",
        )
    )


class ProviderInventoryPipelineE2ETests(unittest.TestCase):
    def test_cws_complete_page_aware_inventory_publishes_exact_result(self):
        inventory = {
            "organization": CWS_ORG,
            "totalHits": 2,
            "queryResult": [
                {
                    "id": "role-101",
                    "title": "Platform Engineer",
                    "primary_city": "Paris",
                    "primary_country": "FR",
                    "organization": CWS_ORG,
                },
                {
                    "id": "role-102",
                    "title": "Product Designer",
                    "primary_city": "Paris",
                    "primary_country": "FR",
                    "organization": CWS_ORG,
                },
            ],
        }
        fetcher = _FixtureFetcher(CWS_CAREERS, _cws_page(), inventory)

        result = _discover(fetcher, CWS_CAREERS)

        self.assertEqual(
            result.open_position_url,
            (
                "https://careers.example-systems.test/job-description/"
                "role-101/platform-engineer-paris-fr"
            ),
        )
        self.assertEqual(result.identity_assertion["verdict"], "verified")
        self.assertEqual(result.trace["stages"]["result_validation"]["issues"], [])

    def test_smartrecruiters_complete_page_aware_inventory_publishes_exact_result(self):
        inventory = {
            "totalFound": 2,
            "offset": 0,
            "limit": 100,
            "content": [
                {
                    "id": "role-201",
                    "name": "Platform Engineer",
                    "location": {"city": "Paris", "country": "FR"},
                    "company": {"identifier": SMART_TENANT, "name": COMPANY},
                },
                {
                    "id": "role-202",
                    "name": "Finance Analyst",
                    "location": {"city": "Paris", "country": "FR"},
                    "company": {"identifier": SMART_TENANT, "name": COMPANY},
                },
            ],
        }
        fetcher = _FixtureFetcher(SMART_CAREERS, _smart_page(), inventory)

        result = _discover(fetcher, SMART_CAREERS)

        self.assertEqual(
            result.open_position_url,
            f"https://jobs.smartrecruiters.com/{SMART_TENANT}/role-201",
        )
        self.assertEqual(result.identity_assertion["verdict"], "verified")
        self.assertEqual(
            result.trace["stages"]["result_validation"]["issues"],
            [],
        )

    def test_empty_or_incomplete_inventory_never_publishes_exact(self):
        cases = (
            (
                "smart empty",
                _FixtureFetcher(
                    SMART_CAREERS,
                    _smart_page(),
                    {
                        "totalFound": 0,
                        "offset": 0,
                        "limit": 100,
                        "content": [],
                    },
                ),
                SMART_CAREERS,
            ),
            (
                "smart truncated",
                _FixtureFetcher(
                    SMART_CAREERS,
                    _smart_page(),
                    {
                        "totalFound": 2,
                        "offset": 0,
                        "limit": 100,
                        "content": [
                            {
                                "id": "role-302",
                                "name": "Platform Engineer",
                                "company": {
                                    "identifier": SMART_TENANT,
                                    "name": COMPANY,
                                },
                            }
                        ],
                    },
                ),
                SMART_CAREERS,
            ),
        )
        for label, fetcher, career_url in cases:
            with self.subTest(case=label):
                result = _discover(fetcher, career_url)
                self.assertIsNone(result.open_position_url)

    def test_cross_tenant_redirect_and_unverified_hiring_identity_fail_closed(self):
        cross_tenant = _FixtureFetcher(
            SMART_CAREERS,
            _smart_page(),
            {
                "totalFound": 1,
                "offset": 0,
                "limit": 100,
                "content": [
                    {
                        "id": "role-401",
                        "name": "Platform Engineer",
                        "company": {"identifier": "OtherTenant", "name": COMPANY},
                    }
                ],
            },
        )
        smart_inventory = {
            "totalFound": 1,
            "offset": 0,
            "limit": 100,
            "content": [
                {
                    "id": "role-402",
                    "name": "Platform Engineer",
                    "company": {"identifier": SMART_TENANT, "name": COMPANY},
                }
            ],
        }
        redirected = _FixtureFetcher(
            SMART_CAREERS,
            _smart_page(),
            smart_inventory,
            api_final_url=(
                "https://api.smartrecruiters.com/v1/companies/OtherTenant/postings"
            ),
        )
        valid_inventory = _FixtureFetcher(
            SMART_CAREERS,
            _smart_page(),
            {
                "totalFound": 1,
                "offset": 0,
                "limit": 100,
                "content": [
                    {
                        "id": "role-403",
                        "name": "Platform Engineer",
                        "company": {"identifier": SMART_TENANT, "name": COMPANY},
                    }
                ],
            },
        )

        for label, fetcher, career_url, verified in (
            ("cross tenant", cross_tenant, SMART_CAREERS, True),
            ("redirect", redirected, SMART_CAREERS, True),
            ("hiring identity", valid_inventory, SMART_CAREERS, False),
        ):
            with self.subTest(case=label):
                result = _discover(
                    fetcher,
                    career_url,
                    identity_verified=verified,
                )
                self.assertIsNone(result.open_position_url)


if __name__ == "__main__":
    unittest.main()
