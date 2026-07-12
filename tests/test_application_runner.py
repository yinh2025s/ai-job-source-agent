import unittest

from job_source_agent.application_runner import ApplicationRunner
from job_source_agent.contracts import PipelineContext, StageExecution
from job_source_agent.models import (
    PIPELINE_STAGES,
    STAGE_CAREER_DISCOVERY,
    STAGE_HIRING_IDENTITY_RESOLUTION,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
    STAGE_RESULT_VALIDATION,
    STAGE_WEBSITE_RESOLUTION,
    CompanyInput,
    StageResult,
)


class RecordingStage:
    def __init__(self, name, calls, updates=None):
        self.name = name
        self.calls = calls
        self.updates = updates or {}

    def run(self, context):
        self.calls.append(self.name)
        return StageExecution(
            StageResult(stage=self.name, status="success"),
            updates=self.updates,
            trace={"executed": True},
        )


class ApplicationRunnerTests(unittest.TestCase):
    def test_executes_in_canonical_order_and_fills_missing_stages(self):
        calls = []
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        runner = ApplicationRunner(
            [
                RecordingStage(STAGE_OPENING_MATCH, calls),
                RecordingStage(STAGE_WEBSITE_RESOLUTION, calls),
                RecordingStage(STAGE_CAREER_DISCOVERY, calls),
            ]
        )

        returned = runner.run(context)

        self.assertIs(returned, context)
        self.assertEqual(
            calls,
            [STAGE_WEBSITE_RESOLUTION, STAGE_CAREER_DISCOVERY, STAGE_OPENING_MATCH],
        )
        self.assertEqual([result.stage for result in context.stage_results], list(PIPELINE_STAGES))
        statuses = {result.stage: result.status for result in context.stage_results}
        self.assertEqual(statuses[STAGE_WEBSITE_RESOLUTION], "success")
        self.assertEqual(statuses[STAGE_JOB_BOARD_DISCOVERY], "not_run")
        self.assertEqual(
            context.trace["stages"][STAGE_JOB_BOARD_DISCOVERY]["scheduler"]["reason"],
            "implementation_not_supplied",
        )

    def test_start_at_reuses_upstream_results_and_recomputes_selected_range(self):
        calls = []
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        website_result = StageResult(stage=STAGE_WEBSITE_RESOLUTION, status="success")
        context.stage_results = [website_result]
        context.trace["stages"] = {
            STAGE_WEBSITE_RESOLUTION: {"checkpoint": True},
        }
        runner = ApplicationRunner(
            [
                RecordingStage(
                    STAGE_CAREER_DISCOVERY,
                    calls,
                    updates={"career_page_url": "https://acme.example/careers"},
                ),
                RecordingStage(STAGE_JOB_BOARD_DISCOVERY, calls),
            ]
        )

        runner.run(
            context,
            start_at=STAGE_CAREER_DISCOVERY,
            stop_after=STAGE_JOB_BOARD_DISCOVERY,
        )

        self.assertEqual(calls, [STAGE_CAREER_DISCOVERY, STAGE_JOB_BOARD_DISCOVERY])
        self.assertIs(
            next(
                result
                for result in context.stage_results
                if result.stage == STAGE_WEBSITE_RESOLUTION
            ),
            website_result,
        )
        self.assertEqual(
            context.trace["stages"][STAGE_WEBSITE_RESOLUTION],
            {"checkpoint": True},
        )
        self.assertEqual(context.career_page_url, "https://acme.example/careers")
        self.assertEqual(
            context.trace["stages"][STAGE_RESULT_VALIDATION]["scheduler"]["reason"],
            "after_stop_after",
        )

    def test_start_at_without_checkpoint_marks_preceding_stages_not_run(self):
        calls = []
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))

        ApplicationRunner([RecordingStage(STAGE_OPENING_MATCH, calls)]).run(
            context,
            start_at=STAGE_OPENING_MATCH,
            stop_after=STAGE_OPENING_MATCH,
        )

        self.assertEqual(calls, [STAGE_OPENING_MATCH])
        self.assertEqual(
            context.trace["stages"][STAGE_HIRING_IDENTITY_RESOLUTION]["scheduler"]["reason"],
            "before_start_at",
        )
        self.assertEqual(
            context.trace["stages"][STAGE_RESULT_VALIDATION]["scheduler"]["reason"],
            "after_stop_after",
        )

    def test_selected_range_replaces_stale_results_and_downstream_results(self):
        calls = []
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.stage_results = [
            StageResult(stage=STAGE_CAREER_DISCOVERY, status="failed"),
            StageResult(stage=STAGE_OPENING_MATCH, status="success"),
        ]
        runner = ApplicationRunner([RecordingStage(STAGE_CAREER_DISCOVERY, calls)])

        runner.run(
            context,
            start_at=STAGE_CAREER_DISCOVERY,
            stop_after=STAGE_CAREER_DISCOVERY,
        )

        results = {result.stage: result for result in context.stage_results}
        self.assertEqual(results[STAGE_CAREER_DISCOVERY].status, "success")
        self.assertEqual(results[STAGE_OPENING_MATCH].status, "not_run")

    def test_rejects_invalid_configuration_and_invalid_stage_results(self):
        calls = []
        website = RecordingStage(STAGE_WEBSITE_RESOLUTION, calls)
        with self.assertRaisesRegex(ValueError, "Duplicate pipeline stage"):
            ApplicationRunner([website, website])
        with self.assertRaisesRegex(ValueError, "Unknown pipeline stage"):
            ApplicationRunner([RecordingStage("custom", calls)])

        runner = ApplicationRunner([website])
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        with self.assertRaisesRegex(ValueError, "comes after"):
            runner.run(
                context,
                start_at=STAGE_OPENING_MATCH,
                stop_after=STAGE_CAREER_DISCOVERY,
            )
        with self.assertRaisesRegex(ValueError, "Unknown start_at"):
            runner.run(context, start_at="custom")

        class WrongResultStage:
            name = STAGE_WEBSITE_RESOLUTION

            def run(self, context):
                return StageExecution(
                    StageResult(stage=STAGE_CAREER_DISCOVERY, status="success")
                )

        with self.assertRaisesRegex(ValueError, "returned a result"):
            ApplicationRunner([WrongResultStage()]).run(context)


if __name__ == "__main__":
    unittest.main()
