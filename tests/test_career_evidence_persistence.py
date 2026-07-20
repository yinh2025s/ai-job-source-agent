import unittest

from job_source_agent.company_discovery_evidence import (
    VerifiedCareerEvidence,
    VerifiedCompanyDiscoveryEvidence,
    VerifiedWebsiteEvidence,
)
from job_source_agent.contracts import PipelineContext
from job_source_agent.errors import DiscoveryError
from job_source_agent.models import CompanyInput
from job_source_agent.stages.discovery import CareerDiscoveryStage


LINKEDIN_URL = "https://www.linkedin.com/company/example"
WEBSITE_URL = "https://example.test"
CAREER_URL = "https://example.test/projects/electrical-jobs"


class EvidenceStore:
    def __init__(self, record=None):
        self.record = record
        self.saved = []
        self.invalidated = []

    def load(self, company_name, linkedin_company_url):
        return self.record

    def save(self, company_name, linkedin_company_url, **layers):
        self.saved.append(layers)

    def invalidate(self, company_name, linkedin_company_url, **kwargs):
        self.invalidated.append(kwargs)
        if kwargs.get("layer") == "career" and self.record is not None:
            self.record = VerifiedCompanyDiscoveryEvidence(
                company_name=self.record.company_name,
                linkedin_company_url=self.record.linkedin_company_url,
                website=self.record.website,
                provider_boards=self.record.provider_boards,
            )


def context() -> PipelineContext:
    pipeline_context = PipelineContext.from_company(
        CompanyInput(
            company_name="Example",
            company_website_url=WEBSITE_URL,
            linkedin_company_url=LINKEDIN_URL,
        )
    )
    pipeline_context.company_website_url = WEBSITE_URL
    return pipeline_context


def stored_record() -> VerifiedCompanyDiscoveryEvidence:
    return VerifiedCompanyDiscoveryEvidence(
        company_name="Example",
        linkedin_company_url=LINKEDIN_URL,
        website=VerifiedWebsiteEvidence(
            url=WEBSITE_URL,
            source="verified_resolver",
            evidence_url=LINKEDIN_URL,
            observed_at=1.0,
        ),
        career=VerifiedCareerEvidence(
            url=CAREER_URL,
            website_url=WEBSITE_URL,
            source="verified_career_search",
            evidence_url=f"{WEBSITE_URL}/sitemap.xml",
            observed_at=1.0,
        ),
    )


class CareerEvidencePersistenceTests(unittest.TestCase):
    def test_sitemap_origin_and_plural_reasons_are_not_saved_as_navigation(self):
        class SitemapCareerService:
            def find_career_page(self, *args, **kwargs):
                return CAREER_URL, {
                    "selected": {
                        "url": CAREER_URL,
                        "source_url": f"{WEBSITE_URL}/sitemap.xml",
                        "origin": "sitemap",
                        "reasons": ["URL contains jobs", "sitemap source"],
                    },
                    "selected_page_source": "network",
                }

        store = EvidenceStore()

        execution = CareerDiscoveryStage(SitemapCareerService(), store).run(context())

        self.assertEqual(execution.result.status, "success")
        persisted = store.saved[0]["career"]
        self.assertEqual(persisted.source, "verified_career_search")
        self.assertEqual(persisted.evidence_url, f"{WEBSITE_URL}/sitemap.xml")

    def test_plural_provider_reason_preserves_provider_handoff(self):
        class ProviderCareerService:
            def find_career_page(self, *args, **kwargs):
                return "https://jobs.vendor.test/example", {
                    "selected": {
                        "url": "https://jobs.vendor.test/example",
                        "source_url": WEBSITE_URL,
                        "origin": "search_result",
                        "reasons": ["ATS provider tenant verified"],
                    },
                    "selected_page_source": "provider_adapter",
                }

        store = EvidenceStore()

        CareerDiscoveryStage(ProviderCareerService(), store).run(context())

        self.assertEqual(store.saved[0]["career"].source, "provider_handoff")

    def test_current_semantic_rejection_invalidates_only_career_layer(self):
        class SemanticallyRejectedCareerService:
            def find_career_page(self, *args, **kwargs):
                raise DiscoveryError(
                    "career_page_not_found",
                    "Current page is not an employment surface",
                    trace={
                        "preferred_career_root": CAREER_URL,
                        "candidate_schedules": [
                            {
                                "scheduled": [
                                    {
                                        "url": CAREER_URL,
                                        "origin": "identity_career_root",
                                    }
                                ]
                            }
                        ],
                        "candidate_fetch_errors": [],
                    },
                )

        store = EvidenceStore(stored_record())

        execution = CareerDiscoveryStage(
            SemanticallyRejectedCareerService(),
            store,
        ).run(context())

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(store.invalidated, [{"layer": "career", "evidence_url": CAREER_URL}])
        self.assertIsNotNone(store.record.website)
        self.assertIsNone(store.record.career)

    def test_fetch_failure_does_not_invalidate_stored_career(self):
        class UnavailableCareerService:
            def find_career_page(self, *args, **kwargs):
                raise DiscoveryError(
                    "network_timeout",
                    "Timed out",
                    trace={
                        "preferred_career_root": CAREER_URL,
                        "candidate_schedules": [
                            {
                                "scheduled": [
                                    {
                                        "url": CAREER_URL,
                                        "origin": "identity_career_root",
                                    }
                                ]
                            }
                        ],
                        "candidate_fetch_errors": [
                            {"url": CAREER_URL, "retryable": True}
                        ],
                    },
                )

        store = EvidenceStore(stored_record())

        CareerDiscoveryStage(UnavailableCareerService(), store).run(context())

        self.assertEqual(store.invalidated, [])
        self.assertIsNotNone(store.record.career)


if __name__ == "__main__":
    unittest.main()
