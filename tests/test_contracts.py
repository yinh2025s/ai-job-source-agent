import unittest

from job_source_agent.contracts import (
    CheckpointStore,
    FetchClient,
    PipelineContext,
    Stage,
    StageExecution,
)
from job_source_agent.models import CompanyInput, StageResult
from job_source_agent.providers import AdapterResult, JobBoard, JobCandidate, JobQuery, ProviderAdapter
from job_source_agent.web import Fetcher


class ContractTests(unittest.TestCase):
    def test_existing_fetcher_satisfies_minimal_fetch_contract(self):
        self.assertIsInstance(Fetcher(offline=True), FetchClient)

    def test_pipeline_context_applies_only_declared_stage_outputs(self):
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", company_website_url="https://acme.example")
        )
        execution = StageExecution(
            result=StageResult(stage="career_discovery", status="success"),
            updates={"career_page_url": "https://acme.example/careers"},
            trace={"selected": "https://acme.example/careers"},
        )

        context.apply(execution)

        self.assertEqual(context.career_page_url, "https://acme.example/careers")
        self.assertEqual(context.stage_results, [execution.result])
        self.assertEqual(context.trace["stages"]["career_discovery"], execution.trace)

    def test_pipeline_context_rejects_undeclared_stage_outputs(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        execution = StageExecution(
            result=StageResult(stage="career_discovery", status="success"),
            updates={"internal_parser_state": "leak"},
        )

        with self.assertRaises(ValueError):
            context.apply(execution)

    def test_stage_checkpoint_and_provider_protocols_are_structural(self):
        class FakeStage:
            name = "career_discovery"

            def run(self, context):
                return StageExecution(StageResult(stage=self.name, status="success"))

        class FakeStore:
            def load(self, input_fingerprint, stage):
                return None

            def save(self, input_fingerprint, execution):
                return None

            def invalidate_from(self, input_fingerprint, stage):
                return None

        class FakeProvider:
            name = "fake"
            supports_listing = True

            def recognizes(self, url):
                return "fake.example" in url

            def identify_board(self, url):
                return JobBoard(url=url, provider=self.name, identifier="fake")

            def list_jobs(self, fetcher, board, query):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    candidates=[JobCandidate(query.title or "", f"{board.url}/1", self.name)],
                )

        self.assertIsInstance(FakeStage(), Stage)
        self.assertIsInstance(FakeStore(), CheckpointStore)
        self.assertIsInstance(FakeProvider(), ProviderAdapter)

        result = FakeProvider().list_jobs(
            Fetcher(offline=True),
            JobBoard("https://fake.example/jobs", "fake"),
            JobQuery(title="Engineer"),
        )
        self.assertEqual(result.candidates[0].title, "Engineer")


if __name__ == "__main__":
    unittest.main()
