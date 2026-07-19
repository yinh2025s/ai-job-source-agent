import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from job_source_agent.contracts import PipelineContext
from job_source_agent.homepage_navigation import HomepageNavigationEvidence
from job_source_agent.models import CompanyInput
from job_source_agent.stages import (
    HiringIdentityResolutionStage,
    PipelineStageRunner,
    WebsiteResolutionStage,
)


class FakeWebsiteResolver:
    def __init__(self, result="https://acme.example"):
        self.result = result
        self.calls = []

    def resolve(
        self,
        company_name,
        linkedin_company_url=None,
        job_location=None,
        preferred_url=None,
    ):
        self.calls.append((company_name, linkedin_company_url, preferred_url))
        return self.result, {"method": "fake-website"}


class EvidenceWebsiteResolver(FakeWebsiteResolver):
    def resolve_with_navigation_evidence(
        self,
        company_name,
        linkedin_company_url=None,
        job_location=None,
        preferred_url=None,
    ):
        self.calls.append((company_name, linkedin_company_url, preferred_url))
        return (
            self.result,
            {"method": "fake-website-with-evidence"},
            HomepageNavigationEvidence(
                homepage_url=self.result,
                candidate_urls=(f"{self.result}/careers",),
            ),
        )


class BlockedWebsiteResolver(FakeWebsiteResolver):
    def resolve(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return None, {
            "resolution_failure": {
                "reason_code": "HTTP_FORBIDDEN",
                "status": 403,
                "retryable": False,
                "error": "HTTP Error 403: Forbidden",
            }
        }


class StoredEvidenceResolver(FakeWebsiteResolver):
    def resolve_with_navigation_evidence(
        self,
        company_name,
        linkedin_company_url=None,
        job_location=None,
        preferred_url=None,
        stored_candidate_url=None,
    ):
        self.calls.append(
            (
                company_name,
                linkedin_company_url,
                preferred_url,
                stored_candidate_url,
            )
        )
        return self.result, {"method": "stored-revalidation", "fetch_errors": []}, None


class MemoryCompanyEvidenceStore:
    def __init__(self, website_url=None):
        self.record = (
            SimpleNamespace(website=SimpleNamespace(url=website_url))
            if website_url
            else None
        )
        self.saved = []
        self.invalidated = []

    def load(self, company_name, linkedin_company_url):
        return self.record

    def save(self, company_name, linkedin_company_url, **kwargs):
        self.saved.append((company_name, linkedin_company_url, kwargs))

    def invalidate(self, company_name, linkedin_company_url, **kwargs):
        self.invalidated.append((company_name, linkedin_company_url, kwargs))


@dataclass
class FakeIdentity:
    hiring_entity_name: str
    career_root_url: str | None = None
    official_website_url: str | None = None
    relationship_verified: bool = False


class FakeIdentityResolver:
    def __init__(self, identity=None, trace=None):
        self.identity = identity
        self.trace = trace or {"method": "fake-identity"}
        self.calls = []

    def resolve(
        self,
        company_name,
        website_url=None,
        linkedin_company_url=None,
        linkedin_job_url=None,
        job_location=None,
    ):
        self.calls.append((company_name, website_url, linkedin_company_url))
        return self.identity, self.trace


class UpstreamStageTests(unittest.TestCase):
    def test_s2_revalidates_stored_company_candidate_without_using_preferred_input(self):
        resolver = StoredEvidenceResolver("https://acme.example/")
        store = MemoryCompanyEvidenceStore("https://acme.example")
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )

        execution = WebsiteResolutionStage(
            resolver,
            company_discovery_evidence_store=store,
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(
            resolver.calls,
            [
                (
                    "Acme",
                    "https://www.linkedin.com/company/acme",
                    None,
                    "https://acme.example",
                )
            ],
        )
        self.assertEqual(len(store.saved), 1)
        self.assertEqual(store.saved[0][2]["website"].url, "https://acme.example/")

    def test_access_controlled_regional_handoff_does_not_replace_stored_gateway(self):
        class AccessControlledHandoffResolver(StoredEvidenceResolver):
            def resolve_with_navigation_evidence(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return (
                    "https://www.lacoste.com/us/",
                    {
                        "selected": {
                            "reasons": [
                                "verified regional gateway declares access-controlled locale root"
                            ]
                        },
                        "fetch_errors": [],
                    },
                    None,
                )

        store = MemoryCompanyEvidenceStore(
            "https://prod-deleg-sfcc.lacoste.com/id/en/"
        )
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Lacoste",
                linkedin_company_url="https://www.linkedin.com/company/lacoste",
            )
        )

        execution = WebsiteResolutionStage(
            AccessControlledHandoffResolver(),
            company_discovery_evidence_store=store,
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.updates["company_website_url"], "https://www.lacoste.com/us/")
        self.assertEqual(store.saved, [])

    def test_access_controlled_sibling_handoff_is_not_persisted(self):
        class AccessControlledSiblingResolver(StoredEvidenceResolver):
            def resolve_with_navigation_evidence(self, *args, **kwargs):
                return (
                    "https://michaelkors.com",
                    {
                        "selected": {
                            "reasons": [
                                "verified regional gateway supports access-controlled sibling root"
                            ]
                        },
                        "fetch_errors": [],
                    },
                    None,
                )

        store = MemoryCompanyEvidenceStore("https://www.michaelkors.cn/")
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Michael Kors",
                linkedin_company_url="https://www.linkedin.com/company/michael-kors",
            )
        )

        execution = WebsiteResolutionStage(
            AccessControlledSiblingResolver(),
            company_discovery_evidence_store=store,
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(store.saved, [])

    def test_explicit_website_input_takes_precedence_over_stored_candidate(self):
        resolver = StoredEvidenceResolver("https://new.example/")
        store = MemoryCompanyEvidenceStore("https://stored.example")
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
                company_website_url="https://provided.example",
            )
        )

        WebsiteResolutionStage(
            resolver,
            company_discovery_evidence_store=store,
        ).run(context)

        self.assertEqual(
            resolver.calls[0],
            (
                "Acme",
                "https://www.linkedin.com/company/acme",
                "https://provided.example",
                None,
            ),
        )

    def test_retryable_stored_candidate_failure_does_not_invalidate_store(self):
        stored = "https://acme.example"

        class RetryableStoredResolver(StoredEvidenceResolver):
            def resolve_with_navigation_evidence(self, *args, **kwargs):
                return None, {
                    "fetch_errors": [
                        {
                            "url": stored,
                            "reason_code": "NETWORK_TIMEOUT",
                            "retryable": True,
                        }
                    ],
                    "resolution_failure": {
                        "reason_code": "NETWORK_TIMEOUT",
                        "error": "timed out",
                    },
                }, None

        store = MemoryCompanyEvidenceStore(stored)
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )

        execution = WebsiteResolutionStage(
            RetryableStoredResolver(None),
            company_discovery_evidence_store=store,
        ).run(context)

        self.assertEqual(execution.result.reason_code, "NETWORK_TIMEOUT")
        self.assertEqual(store.invalidated, [])

    def test_nonretryable_transport_failure_does_not_invalidate_stored_candidate(self):
        stored = "https://acme.example"

        class ForbiddenStoredResolver(StoredEvidenceResolver):
            def resolve_with_navigation_evidence(self, *args, **kwargs):
                return None, {
                    "fetch_errors": [
                        {
                            "url": stored,
                            "reason_code": "HTTP_FORBIDDEN",
                            "retryable": False,
                        }
                    ],
                    "resolution_failure": {
                        "reason_code": "HTTP_FORBIDDEN",
                        "error": "forbidden",
                    },
                }, None

        store = MemoryCompanyEvidenceStore(stored)
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )

        execution = WebsiteResolutionStage(
            ForbiddenStoredResolver(None),
            company_discovery_evidence_store=store,
        ).run(context)

        self.assertEqual(execution.result.reason_code, "HTTP_FORBIDDEN")
        self.assertEqual(store.invalidated, [])

    def test_deterministic_stored_website_identity_rejection_invalidates_layer(self):
        stored = "https://acme.example"

        class ParkedStoredResolver(StoredEvidenceResolver):
            def resolve_with_navigation_evidence(self, *args, **kwargs):
                return None, {
                    "candidates": [
                        {
                            "url": stored,
                            "reasons": [
                                "candidate source: stored_verified_company_evidence",
                                "parked domain rejected",
                            ],
                        }
                    ],
                    "resolution_failure": {
                        "reason_code": "WEBSITE_NOT_RESOLVED",
                        "error": "identity rejected",
                    },
                }, None

        store = MemoryCompanyEvidenceStore(stored)
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
            )
        )

        WebsiteResolutionStage(
            ParkedStoredResolver(None),
            company_discovery_evidence_store=store,
        ).run(context)

        self.assertEqual(store.invalidated[0][2]["layer"], "website")

    def test_current_page_identity_confirmation_retains_hosted_stored_candidate(self):
        stored = "https://product.parent.example"

        class VerifiedHostedResolver(StoredEvidenceResolver):
            def resolve_with_navigation_evidence(self, *args, **kwargs):
                return None, {
                    "candidates": [
                        {
                            "url": stored,
                            "reasons": [
                                "candidate source: stored_verified_company_evidence",
                                "registrable domain does not establish company ownership",
                                "homepage verified",
                                "homepage title confirms company identity",
                                "homepage body confirms company identity",
                            ],
                        }
                    ],
                    "resolution_failure": {
                        "reason_code": "WEBSITE_NOT_RESOLVED",
                        "error": "LinkedIn verification unavailable",
                    },
                }, None

        store = MemoryCompanyEvidenceStore(stored)
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme Product",
                linkedin_company_url="https://www.linkedin.com/company/acme-product",
            )
        )

        WebsiteResolutionStage(
            VerifiedHostedResolver(None),
            company_discovery_evidence_store=store,
        ).run(context)

        self.assertEqual(store.invalidated, [])

    def test_stored_website_region_conflict_does_not_erase_provider_descendants(self):
        stored = "https://acme.cn"

        class RegionalStoredResolver(StoredEvidenceResolver):
            def resolve_with_navigation_evidence(self, *args, **kwargs):
                return None, {
                    "candidates": [
                        {
                            "url": stored,
                            "reasons": [
                                "candidate source: stored_verified_company_evidence",
                                "homepage verified",
                                "regional website conflicts with job location: cn vs us",
                            ],
                        }
                    ],
                    "resolution_failure": {
                        "reason_code": "WEBSITE_NOT_RESOLVED",
                        "error": "wrong regional entry",
                    },
                }, None

        store = MemoryCompanyEvidenceStore(stored)
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://www.linkedin.com/company/acme",
                job_location="New York, NY",
            )
        )

        WebsiteResolutionStage(
            RegionalStoredResolver(None),
            company_discovery_evidence_store=store,
        ).run(context)

        self.assertEqual(store.invalidated, [])

    def test_s2_uses_only_verified_official_identity_hint_before_network(self):
        website = FakeWebsiteResolver("https://wrong.example")
        identity = FakeIdentityResolver(
            FakeIdentity(
                hiring_entity_name="Meta",
                career_root_url="https://www.metacareers.com/jobs/",
                official_website_url="https://www.meta.com/",
                relationship_verified=True,
            )
        )
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Meta",
                linkedin_company_url="https://www.linkedin.com/company/meta",
            )
        )

        execution = WebsiteResolutionStage(
            website,
            identity_hint_resolver=identity,
        ).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.updates["company_website_url"], "https://www.meta.com/")
        self.assertEqual(execution.trace["method"], "verified_company_identity_hint")
        self.assertEqual(website.calls, [])

    def test_s2_does_not_trust_unverified_identity_hint(self):
        website = FakeWebsiteResolver("https://verified-by-resolver.example")
        identity = FakeIdentityResolver(
            FakeIdentity(
                hiring_entity_name="Acme",
                official_website_url="https://unverified-hint.example",
            )
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))

        execution = WebsiteResolutionStage(
            website,
            identity_hint_resolver=identity,
        ).run(context)

        self.assertEqual(
            execution.updates["company_website_url"],
            "https://verified-by-resolver.example",
        )
        self.assertEqual(len(website.calls), 1)

    def test_s2_revalidates_supplied_website_as_preferred_candidate(self):
        resolver = FakeWebsiteResolver("https://new-acme.example")
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://old-acme.example",
            )
        )

        execution = WebsiteResolutionStage(resolver).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(
            execution.updates["company_website_url"],
            "https://new-acme.example",
        )
        self.assertEqual(
            resolver.calls,
            [("Acme", None, "https://old-acme.example")],
        )
        self.assertIn("revalidated", execution.result.detail)

    def test_s2_revalidates_replay_website_as_preferred_candidate(self):
        resolver = FakeWebsiteResolver("https://new-acme.example")
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://old-acme.example",
                source="replay_input",
            )
        )

        execution = WebsiteResolutionStage(resolver).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(
            execution.updates["company_website_url"], "https://new-acme.example"
        )
        self.assertEqual(
            resolver.calls,
            [("Acme", None, "https://old-acme.example")],
        )
        self.assertIn("revalidated", execution.result.detail)

    def test_s2_resolves_missing_website_and_can_update_context(self):
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_company_url="https://linkedin.com/company/acme",
            )
        )

        PipelineStageRunner([WebsiteResolutionStage(FakeWebsiteResolver())]).run(context)

        self.assertEqual(context.company_website_url, "https://acme.example")
        self.assertEqual(context.stage_results[0].status, "success")
        self.assertIsNone(context.homepage_navigation_evidence)

    def test_s2_emits_typed_navigation_evidence_from_evidence_capable_service(self):
        resolver = EvidenceWebsiteResolver()
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))

        execution = WebsiteResolutionStage(resolver).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(
            execution.updates["homepage_navigation_evidence"].homepage_url,
            "https://acme.example",
        )
        self.assertEqual(
            execution.updates["homepage_navigation_evidence"].candidate_urls,
            ("https://acme.example/careers",),
        )

    def test_s2_missing_result_has_existing_failure_semantics(self):
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Missing",
                company_website_url="https://unverified.example",
            )
        )

        execution = WebsiteResolutionStage(FakeWebsiteResolver(None)).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(execution.result.reason_code, "WEBSITE_NOT_RESOLVED")
        self.assertEqual(execution.result.output_count, 0)
        self.assertEqual(execution.updates["company_website_url"], "")

    def test_s2_projects_retained_typed_resolution_failure(self):
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Blocked",
                company_website_url="https://blocked.example",
            )
        )

        execution = WebsiteResolutionStage(BlockedWebsiteResolver(None)).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(execution.result.reason_code, "HTTP_FORBIDDEN")
        self.assertFalse(execution.result.retryable)
        self.assertEqual(execution.result.detail, "HTTP Error 403: Forbidden")

    def test_s2_failure_clears_unverified_input_before_identity_stage(self):
        identity = FakeIdentityResolver()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Missing",
                company_website_url="https://unverified.example",
            )
        )

        PipelineStageRunner(
            [
                WebsiteResolutionStage(FakeWebsiteResolver(None)),
                HiringIdentityResolutionStage(identity),
            ]
        ).run(context)

        self.assertEqual(context.company_website_url, "")
        self.assertEqual(
            [result.status for result in context.stage_results],
            ["failed", "not_run"],
        )
        self.assertEqual(identity.calls, [])

    def test_s2_failure_preserves_direct_career_handoff(self):
        career_root = "https://jobs.example/direct"
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Direct",
                company_website_url="https://unverified.example",
                career_root_url=career_root,
            )
        )

        execution = WebsiteResolutionStage(FakeWebsiteResolver(None)).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertNotIn("company_website_url", execution.updates)
        self.assertEqual(context.career_root_url, career_root)

    def test_s3_is_not_run_without_resolved_website(self):
        resolver = FakeIdentityResolver()
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))

        execution = HiringIdentityResolutionStage(resolver).run(context)

        self.assertEqual(execution.result.status, "not_run")
        self.assertEqual(resolver.calls, [])

    def test_s3_records_resolved_identity_and_declared_website_override(self):
        resolver = FakeIdentityResolver(
            FakeIdentity(
                hiring_entity_name="Meta",
                career_root_url="https://www.metacareers.com/jobs/",
                official_website_url="https://www.instagram.com/",
            )
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Instagram", company_website_url="instagram.com")
        )

        execution = HiringIdentityResolutionStage(resolver).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.result.output_count, 1)
        self.assertEqual(
            execution.updates["company_website_url"], "https://www.instagram.com/"
        )
        self.assertEqual(execution.updates["hiring_entity_name"], "Meta")
        self.assertEqual(
            execution.updates["career_root_url"],
            "https://www.metacareers.com/jobs/",
        )
        self.assertIn(
            {"field": "hiring_entity_name", "value": "Meta"},
            execution.result.evidence,
        )

    def test_s3_declared_identity_outputs_flow_to_career_stage_context(self):
        resolver = FakeIdentityResolver(
            FakeIdentity(
                hiring_entity_name="Meta",
                career_root_url="https://www.metacareers.com/jobs/",
            )
        )
        context = PipelineContext.from_company(
            CompanyInput(company_name="Instagram", company_website_url="instagram.com")
        )

        PipelineStageRunner([HiringIdentityResolutionStage(resolver)]).run(context)

        self.assertEqual(context.hiring_entity_name, "Meta")
        self.assertEqual(context.career_root_url, "https://www.metacareers.com/jobs/")

    def test_s3_no_alternate_identity_is_still_success(self):
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", company_website_url="https://acme.example")
        )

        execution = HiringIdentityResolutionStage(FakeIdentityResolver()).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertIn("input company remains", execution.result.detail)

    def test_s3_stops_undisclosed_agency_without_selecting_client(self):
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Aventis Solutions",
                company_website_url="https://aventissolutions.example",
                linkedin_job_url="https://www.linkedin.com/jobs/view/job-456",
            )
        )
        resolver = FakeIdentityResolver(
            trace={
                "posting_identity": {
                    "classification": "agency_unresolved",
                    "employer_name": None,
                }
            }
        )

        execution = HiringIdentityResolutionStage(resolver).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(
            execution.result.reason_code,
            "COMPANY_IDENTITY_AMBIGUOUS",
        )
        self.assertEqual(execution.updates, {})
        self.assertIn(
            {"field": "publisher_role", "value": "recruiting_agency"},
            execution.result.evidence,
        )
        self.assertIn("undisclosed client", execution.result.detail)


if __name__ == "__main__":
    unittest.main()
