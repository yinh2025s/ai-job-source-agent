import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.errors import DiscoveryError
from job_source_agent.models import CompanyInput
from job_source_agent.stages import (
    CareerDiscoveryStage,
    JobBoardDiscoveryStage,
    OpeningMatchStage,
    PipelineStageRunner,
)


class FakeDiscoveryService:
    def find_career_page(self, company_website_url, company_name=None, preferred_url=None, target_title=None):
        return f"{company_website_url}/careers", {"method": "fake-career"}

    def find_job_board(self, career_page_url, company_name=None):
        return "https://boards.greenhouse.io/acme", {"method": "fake-board"}

    def match_opening(self, job_list_url, target_title=None, target_location=None):
        return f"{job_list_url}/jobs/123", job_list_url, {"method": "fake-match"}


class DiscoveryStageTests(unittest.TestCase):
    def test_s4_s5_s6_can_run_through_versioned_context(self):
        service = FakeDiscoveryService()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                job_title="Data Analyst",
            )
        )
        runner = PipelineStageRunner(
            [CareerDiscoveryStage(service), JobBoardDiscoveryStage(service), OpeningMatchStage(service)]
        )

        runner.run(context)

        self.assertEqual(context.career_page_url, "https://acme.example/careers")
        self.assertEqual(context.job_list_page_url, "https://boards.greenhouse.io/acme")
        self.assertEqual(context.open_position_url, "https://boards.greenhouse.io/acme/jobs/123")
        self.assertEqual([result.status for result in context.stage_results], ["success", "success", "success"])

    def test_job_board_stage_can_run_independently_from_saved_career_context(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = "https://acme.example/careers"

        execution = JobBoardDiscoveryStage(FakeDiscoveryService()).run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(execution.updates["job_list_page_url"], "https://boards.greenhouse.io/acme")

    def test_direct_input_career_root_is_trusted_without_network_revalidation(self):
        class MustNotFetchCareer(FakeDiscoveryService):
            def find_career_page(self, *args, **kwargs):
                raise AssertionError("trusted direct root should not be re-fetched")

        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                career_root_url="https://jobs.lever.co/acme",
                source="input",
            )
        )

        execution = CareerDiscoveryStage(MustNotFetchCareer()).run(context)

        self.assertEqual(execution.updates["career_page_url"], "https://jobs.lever.co/acme")
        self.assertEqual(execution.trace["preferred_root_validation"], "trusted_provenance")

    def test_replay_career_root_is_revalidated(self):
        class CapturingCareer(FakeDiscoveryService):
            def __init__(self):
                self.preferred_url = None

            def find_career_page(self, company_website_url, company_name=None, preferred_url=None, target_title=None):
                self.preferred_url = preferred_url
                return "https://job-boards.greenhouse.io/acme", {"validated": True}

        service = CapturingCareer()
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                company_website_url="https://acme.example",
                career_root_url="https://wrong.example/careers",
                source="replay_input",
                source_trace={"replay": {"source_result_file": "old.json"}},
            )
        )

        execution = CareerDiscoveryStage(service).run(context)

        self.assertEqual(service.preferred_url, "https://wrong.example/careers")
        self.assertEqual(execution.updates["career_page_url"], "https://job-boards.greenhouse.io/acme")

    def test_job_board_stage_accepts_provider_from_verified_page_evidence(self):
        class PageAwareService(FakeDiscoveryService):
            def find_job_board(self, career_page_url, company_name=None):
                return career_page_url, {
                    "provider": "icims",
                    "provider_detection": {"method": "page_evidence"},
                }

        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = "https://jobs.acme.example/region/jobs"

        execution = JobBoardDiscoveryStage(PageAwareService()).run(context)

        self.assertEqual(execution.result.provider, "icims")
        self.assertEqual(execution.updates["provider"], "icims")

    def test_opening_no_match_is_partial_not_failed(self):
        class NoMatchService(FakeDiscoveryService):
            def match_opening(self, job_list_url, target_title=None, target_location=None):
                return None, job_list_url, {"opening_error": "specific_opening_not_found"}

        context = PipelineContext.from_company(CompanyInput(company_name="Acme", job_title="Engineer"))
        context.job_list_page_url = "https://boards.greenhouse.io/acme"
        context.provider = "greenhouse"

        execution = OpeningMatchStage(NoMatchService()).run(context)

        self.assertEqual(execution.result.status, "partial")
        self.assertEqual(execution.result.reason_code, "OPENING_NOT_FOUND")

    def test_career_failure_makes_downstream_stages_not_run(self):
        class MissingCareerService(FakeDiscoveryService):
            def find_career_page(self, company_website_url, company_name=None, preferred_url=None, target_title=None):
                raise DiscoveryError("career_page_not_found", "missing", trace={"searched": True})

        service = MissingCareerService()
        context = PipelineContext.from_company(
            CompanyInput(company_name="Missing", company_website_url="https://missing.example", job_title="Engineer")
        )

        PipelineStageRunner(
            [CareerDiscoveryStage(service), JobBoardDiscoveryStage(service), OpeningMatchStage(service)]
        ).run(context)

        self.assertEqual([result.status for result in context.stage_results], ["failed", "not_run", "not_run"])
        self.assertEqual(context.stage_results[0].reason_code, "CAREER_PAGE_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
