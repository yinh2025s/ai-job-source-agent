import tempfile
import unittest
from pathlib import Path

from job_source_agent.composition import FetcherConfig, build_application
from job_source_agent.linkedin import load_company_inputs
from job_source_agent.models import (
    PIPELINE_STAGES,
    STAGE_HIRING_IDENTITY_RESOLUTION,
    STAGE_WEBSITE_RESOLUTION,
)


ROOT = Path(__file__).resolve().parents[1]


class PipelineApplicationTests(unittest.TestCase):
    def build_application(self, checkpoint_dir=None):
        return build_application(
            FetcherConfig(fixtures_dir=ROOT / "samples" / "sites", offline=True),
            checkpoint_dir=checkpoint_dir,
        )

    def test_runs_all_seven_stages_and_preserves_result_shape(self):
        company = load_company_inputs(ROOT / "samples" / "linkedin_jobs.json")[0]

        result = self.build_application().pipeline.discover(company)

        self.assertEqual([stage.stage for stage in result.stage_results], list(PIPELINE_STAGES))
        self.assertEqual(result.status, "success")
        self.assertEqual(result.pipeline_status, "success")
        self.assertEqual(result.company_website_url, "https://aurora-data.example")
        self.assertEqual(result.career_page_url, "https://jobs.lever.co/aurora-data")
        self.assertIn("d9d64766", result.open_position_url)
        self.assertEqual(result.result_record()["output_validation_status"], "success")

    def test_stop_after_marks_downstream_stages_not_run(self):
        company = load_company_inputs(ROOT / "samples" / "linkedin_jobs.json")[0]

        result = self.build_application().pipeline.discover(
            company,
            stop_after=STAGE_HIRING_IDENTITY_RESOLUTION,
        )

        statuses = {stage.stage: stage.status for stage in result.stage_results}
        self.assertEqual(statuses[STAGE_WEBSITE_RESOLUTION], "success")
        self.assertEqual(statuses[STAGE_HIRING_IDENTITY_RESOLUTION], "success")
        self.assertTrue(all(
            statuses[stage] == "not_run"
            for stage in PIPELINE_STAGES[PIPELINE_STAGES.index(STAGE_HIRING_IDENTITY_RESOLUTION) + 1 :]
        ))
        self.assertIsNone(result.career_page_url)

    def test_resume_hydrates_upstream_updates_from_stage_checkpoints(self):
        company = load_company_inputs(ROOT / "samples" / "linkedin_jobs.json")[0]
        with tempfile.TemporaryDirectory() as directory:
            application = self.build_application(directory)
            first = application.pipeline.discover(company)
            resumed = application.pipeline.discover(
                company,
                start_at=STAGE_HIRING_IDENTITY_RESOLUTION,
            )

        self.assertEqual(first.company_website_url, resumed.company_website_url)
        self.assertEqual(first.open_position_url, resumed.open_position_url)
        self.assertEqual(
            resumed.trace["stages"][STAGE_WEBSITE_RESOLUTION],
            first.trace["stages"][STAGE_WEBSITE_RESOLUTION],
        )


if __name__ == "__main__":
    unittest.main()
