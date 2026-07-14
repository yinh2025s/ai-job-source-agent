import unittest
from dataclasses import dataclass

from job_source_agent.contracts import PipelineContext
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


@dataclass
class FakeIdentity:
    hiring_entity_name: str
    career_root_url: str | None = None
    official_website_url: str | None = None


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

    def test_s2_missing_result_has_existing_failure_semantics(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Missing"))

        execution = WebsiteResolutionStage(FakeWebsiteResolver(None)).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(execution.result.reason_code, "WEBSITE_NOT_RESOLVED")
        self.assertEqual(execution.result.output_count, 0)

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
