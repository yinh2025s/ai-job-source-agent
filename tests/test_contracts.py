import unittest

from job_source_agent.contracts import (
    CheckpointStore,
    FetchBudget,
    FetchClient,
    PipelineContext,
    Stage,
    StageExecution,
)
from job_source_agent.models import CompanyInput, StageResult
from job_source_agent.providers import AdapterResult, JobBoard, JobCandidate, JobQuery, ProviderAdapter
from job_source_agent.providers.base import (
    has_fetch_reserve,
    pagination_fetch_reserve_seconds,
    require_fetch_reserve,
)
from job_source_agent.retrying_fetcher import RetryingFetcher
from job_source_agent.web import FetchError, Fetcher


class ContractTests(unittest.TestCase):
    def test_existing_fetcher_satisfies_minimal_fetch_contract(self):
        self.assertIsInstance(Fetcher(offline=True), FetchClient)
        self.assertNotIsInstance(Fetcher(offline=True), FetchBudget)

    def test_retrying_fetcher_exposes_optional_budget_contract(self):
        fetcher = RetryingFetcher(
            Fetcher(offline=True),
            max_retries=0,
            clock=lambda: 10.0,
            deadline=12.5,
        )

        self.assertIsInstance(fetcher, FetchClient)
        self.assertIsInstance(fetcher, FetchBudget)
        self.assertEqual(fetcher.remaining_fetch_seconds(), 2.5)

    def test_provider_reserve_accounts_for_request_timeout_and_publication(self):
        now = [0.0]
        fetcher = RetryingFetcher(
            Fetcher(offline=True, timeout=6),
            max_retries=0,
            clock=lambda: now[0],
            deadline=8.0,
        )
        reserve = pagination_fetch_reserve_seconds(
            fetcher,
            publication_reserve_seconds=1.0,
        )

        self.assertEqual(reserve, 7.0)
        self.assertTrue(has_fetch_reserve(fetcher, reserve))
        now[0] = 1.5
        self.assertFalse(has_fetch_reserve(fetcher, reserve))
        self.assertTrue(has_fetch_reserve(Fetcher(offline=True), 1000.0))

    def test_provider_reserve_fails_closed_for_unknown_or_nonfinite_values(self):
        now = [0.0]

        class UnknownTimeoutFetcher:
            timeout = None

            def fetch(self, url, data=None, headers=None):
                raise AssertionError("fetch should not run")

        bounded = RetryingFetcher(
            UnknownTimeoutFetcher(),
            max_retries=0,
            clock=lambda: now[0],
            deadline=10.0,
        )

        self.assertEqual(pagination_fetch_reserve_seconds(bounded), float("inf"))
        self.assertEqual(
            pagination_fetch_reserve_seconds(bounded, publication_reserve_seconds=float("nan")),
            float("inf"),
        )
        self.assertFalse(has_fetch_reserve(bounded, float("inf")))
        self.assertFalse(has_fetch_reserve(bounded, float("nan")))

    def test_reserve_guard_records_rejected_request_without_fetching(self):
        class GuardedFetcher:
            timeout = 1.0

            def __init__(self):
                self.recorded = []

            def remaining_fetch_seconds(self):
                return 0.5

            def record_fetch_failure(self, error, url, data=None, headers=None):
                self.recorded.append((error, url, data, headers))

            def fetch(self, url, data=None, headers=None):
                raise AssertionError("guarded request must not be fetched")

        fetcher = GuardedFetcher()
        with self.assertRaisesRegex(FetchError, "cooperative reserve") as raised:
            require_fetch_reserve(
                fetcher,
                2.0,
                url="https://jobs.example/api",
                data=b'{"range":10}',
                headers={"Content-Type": "application/json"},
            )

        self.assertEqual(raised.exception.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(len(fetcher.recorded), 1)

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
