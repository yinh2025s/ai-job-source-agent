import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.models import CompanyInput, StageResult
from job_source_agent.stages import ResultValidationStage


class ValidationStageTests(unittest.TestCase):
    def test_s7_succeeds_and_reports_derived_pipeline_status(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.stage_results.extend(
            [
                StageResult(stage="career_discovery", status="success"),
                StageResult(stage="job_board_discovery", status="success"),
                StageResult(stage="opening_match", status="partial"),
            ]
        )

        execution = ResultValidationStage().run(context)

        self.assertEqual(execution.result.status, "success")
        self.assertEqual(
            execution.result.evidence,
            [{"field": "pipeline_status", "value": "partial"}],
        )

    def test_s7_rejects_duplicate_stage_results(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.stage_results.extend(
            [
                StageResult(stage="career_discovery", status="success"),
                StageResult(stage="career_discovery", status="success"),
            ]
        )

        execution = ResultValidationStage().run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(execution.result.reason_code, "RESULT_VALIDATION_FAILED")
        self.assertEqual(execution.result.detail, "Duplicate stage results were produced.")

    def test_s7_accepts_injected_validation_service(self):
        class RejectingValidator:
            def validate(self, context):
                return ["Custom invariant failed."]

        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))

        execution = ResultValidationStage(RejectingValidator()).run(context)

        self.assertEqual(execution.result.status, "failed")
        self.assertEqual(execution.trace["issues"], ["Custom invariant failed."])


if __name__ == "__main__":
    unittest.main()
