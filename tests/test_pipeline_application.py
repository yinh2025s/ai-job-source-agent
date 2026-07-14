import json
import tempfile
import unittest
from pathlib import Path

from job_source_agent.composition import AgentConfig, FetcherConfig, build_application
from job_source_agent.linkedin import load_company_inputs
from job_source_agent.models import (
    PIPELINE_STAGES,
    STAGE_CAREER_DISCOVERY,
    STAGE_HIRING_IDENTITY_RESOLUTION,
    STAGE_WEBSITE_RESOLUTION,
    CompanyInput,
    StageResult,
)
from job_source_agent.contracts import PipelineContext
from job_source_agent.pipeline_application import discovery_result_from_context
from job_source_agent.run_configuration import DeterministicRunConfig


ROOT = Path(__file__).resolve().parents[1]


class PipelineApplicationTests(unittest.TestCase):
    def build_application(self, checkpoint_dir=None, agent_config=None, snapshot_dir=None):
        return build_application(
            FetcherConfig(
                fixtures_dir=ROOT / "samples" / "sites",
                offline=True,
                snapshot_dir=snapshot_dir,
            ),
            agent_config,
            checkpoint_dir=checkpoint_dir,
        )

    def test_runs_all_seven_stages_and_preserves_result_shape(self):
        company = load_company_inputs(ROOT / "samples" / "linkedin_jobs.json")[0]
        attempt_id = "capture-attempt-0001"

        result = self.build_application().pipeline.discover(
            company,
            capture_attempt_id=attempt_id,
        )

        self.assertEqual([stage.stage for stage in result.stage_results], list(PIPELINE_STAGES))
        self.assertEqual(result.status, "success")
        self.assertEqual(result.pipeline_status, "success")
        self.assertEqual(result.company_website_url, "https://aurora-data.example")
        self.assertEqual(result.career_page_url, "https://jobs.lever.co/aurora-data")
        self.assertIn("d9d64766", result.open_position_url)
        self.assertEqual(result.result_record()["output_validation_status"], "success")
        self.assertEqual(result.result_schema_version, "2.1")
        self.assertEqual(result.run_configuration["schema_version"], "1.2")
        self.assertEqual(
            result.run_configuration["agent"]["max_job_board_attempts"],
            3,
        )
        self.assertRegex(result.run_configuration_digest, r"^[0-9a-f]{64}$")
        self.assertRegex(result.execution_fingerprint, r"^[0-9a-f]{64}$")
        lineage = result.trace["stage_evidence_lineage"]
        self.assertEqual([item["stage"] for item in lineage], list(PIPELINE_STAGES))
        self.assertTrue(
            all(item["producer_attempt_id"] == attempt_id for item in lineage)
        )
        self.assertTrue(
            all(item["execution_fingerprint"] == result.execution_fingerprint for item in lineage)
        )
        self.assertTrue(all(item["snapshot_scope"] is None for item in lineage))

    def test_snapshot_enabled_application_freezes_scope_for_every_executed_stage(self):
        company = load_company_inputs(ROOT / "samples" / "linkedin_jobs.json")[0]
        with tempfile.TemporaryDirectory() as directory:
            snapshot_dir = Path(directory) / "snapshots"
            result = self.build_application(snapshot_dir=snapshot_dir).pipeline.discover(
                company,
                capture_attempt_id="capture-attempt-scoped",
            )
            records = []
            for filename in ("snapshots.jsonl", "fetch-failures.jsonl"):
                path = snapshot_dir / filename
                if path.exists():
                    records.extend(
                        json.loads(line)
                        for line in path.read_text(encoding="utf-8").splitlines()
                    )

        lineage = result.trace["stage_evidence_lineage"]
        self.assertEqual([item["stage"] for item in lineage], list(PIPELINE_STAGES))
        scopes = [item["snapshot_scope"] for item in lineage]
        self.assertTrue(all(scope is not None for scope in scopes))
        self.assertEqual(sum(scope["request_count"] for scope in scopes), len(records))
        counts_by_scope = {
            scope["scope_id"]: sum(
                record["scope_id"] == scope["scope_id"] for record in records
            )
            for scope in scopes
        }
        self.assertEqual(
            counts_by_scope,
            {scope["scope_id"]: scope["request_count"] for scope in scopes},
        )
        self.assertTrue(any(scope["request_count"] == 0 for scope in scopes))
        self.assertTrue(any(scope["request_count"] > 0 for scope in scopes))

    def test_result_records_the_exact_deterministic_run_configuration(self):
        company = load_company_inputs(ROOT / "samples" / "linkedin_jobs.json")[0]
        agent_config = AgentConfig(
            max_candidates=7,
            max_job_pages=4,
            max_career_candidate_fetches=6,
            max_career_search_queries=3,
            max_ats_board_fetches=2,
            enable_sitemap_discovery=False,
            enable_career_search=False,
            career_search_timeout=2.5,
        )
        expected = DeterministicRunConfig.from_agent_config(agent_config)

        result = self.build_application(agent_config=agent_config).pipeline.discover(
            company,
            stop_after=STAGE_HIRING_IDENTITY_RESOLUTION,
        )

        self.assertEqual(result.run_configuration, expected.to_payload())
        self.assertEqual(result.run_configuration_digest, expected.digest)
        self.assertEqual(result.trace["run_configuration_digest"], expected.digest)
        self.assertEqual(result.trace["execution_fingerprint"], result.execution_fingerprint)

    def test_authoritative_replay_execution_fingerprint_override_is_strict(self):
        company = load_company_inputs(ROOT / "samples" / "linkedin_jobs.json")[0]
        authoritative = "f" * 64

        result = self.build_application().pipeline.discover(
            company,
            execution_fingerprint_override=authoritative,
        )

        self.assertEqual(result.execution_fingerprint, authoritative)
        self.assertTrue(
            all(
                item["execution_fingerprint"] == authoritative
                for item in result.trace["stage_evidence_lineage"]
            )
        )
        with self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
            self.build_application().pipeline.discover(
                company,
                execution_fingerprint_override="not-a-digest",
            )

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

    def test_external_apply_recovers_when_website_resolution_fails(self):
        external = (
            "https://company.wd5.myworkdayjobs.com/en-US/acme/job/New-York-NY/"
            "Data-Analyst_R123"
        )
        company = CompanyInput(
            company_name="Missing Marketing Site",
            external_apply_url=external,
            job_title="Data Analyst",
            job_location="New York, NY",
            source="linkedin_browser_extension",
        )

        result = self.build_application().pipeline.discover(company)
        statuses = {stage.stage: stage.status for stage in result.stage_results}

        self.assertEqual(statuses[STAGE_WEBSITE_RESOLUTION], "failed")
        self.assertEqual(statuses[STAGE_CAREER_DISCOVERY], "not_run")
        self.assertEqual(result.job_list_page_url, "https://company.wd5.myworkdayjobs.com/en-US/acme")
        self.assertIn("Data-Analyst_R123", result.open_position_url)
        self.assertEqual(result.pipeline_status, "success")
        self.assertIsNone(result.error_code)

    def test_resume_hydrates_upstream_updates_from_stage_checkpoints(self):
        company = load_company_inputs(ROOT / "samples" / "linkedin_jobs.json")[0]
        with tempfile.TemporaryDirectory() as directory:
            application = self.build_application(directory)
            first = application.pipeline.discover(
                company,
                capture_attempt_id="capture-attempt-first",
            )
            resumed = application.pipeline.discover(
                company,
                start_at=STAGE_HIRING_IDENTITY_RESOLUTION,
                capture_attempt_id="capture-attempt-second",
            )

        self.assertEqual(first.company_website_url, resumed.company_website_url)
        self.assertEqual(first.open_position_url, resumed.open_position_url)
        self.assertEqual(
            resumed.trace["stages"][STAGE_WEBSITE_RESOLUTION],
            first.trace["stages"][STAGE_WEBSITE_RESOLUTION],
        )
        self.assertIn(
            {"stage": STAGE_WEBSITE_RESOLUTION, "action": "restore"},
            resumed.trace["checkpoint_events"],
        )
        self.assertEqual(
            resumed.trace["checkpoint_prefix"]["requested_start"],
            STAGE_HIRING_IDENTITY_RESOLUTION,
        )
        self.assertEqual(
            resumed.trace["checkpoint_prefix"]["effective_start"],
            STAGE_HIRING_IDENTITY_RESOLUTION,
        )
        attempts = {
            item["stage"]: item["producer_attempt_id"]
            for item in resumed.trace["stage_evidence_lineage"]
        }
        self.assertEqual(attempts["linkedin_discovery"], "capture-attempt-first")
        self.assertEqual(attempts[STAGE_WEBSITE_RESOLUTION], "capture-attempt-first")
        self.assertTrue(
            all(
                attempts[stage] == "capture-attempt-second"
                for stage in PIPELINE_STAGES[2:]
            )
        )

    def test_resume_exposes_prefix_fallback_diagnostics_in_result_trace(self):
        company = load_company_inputs(ROOT / "samples" / "linkedin_jobs.json")[0]
        with tempfile.TemporaryDirectory() as directory:
            application = self.build_application(directory)
            application.pipeline.discover(company)
            website_checkpoint = next(
                Path(directory).rglob(f"{STAGE_WEBSITE_RESOLUTION}.json")
            )
            website_checkpoint.unlink()

            resumed = application.pipeline.discover(
                company,
                start_at=STAGE_CAREER_DISCOVERY,
                stop_after=STAGE_CAREER_DISCOVERY,
            )

        prefix = resumed.trace["checkpoint_prefix"]
        self.assertEqual(prefix["requested_start"], STAGE_CAREER_DISCOVERY)
        self.assertEqual(prefix["effective_start"], STAGE_WEBSITE_RESOLUTION)
        self.assertEqual(prefix["defect_class"], "missing_corrupt_or_incompatible")
        self.assertIn(STAGE_WEBSITE_RESOLUTION, prefix["defect_stages"])

    def test_linkedin_native_only_is_partial_in_both_result_statuses(self):
        context = PipelineContext.from_company(
            CompanyInput(
                company_name="Acme",
                linkedin_job_url="https://www.linkedin.com/jobs/view/123",
            )
        )
        context.stage_results.extend(
            [
                StageResult(
                    stage="career_discovery",
                    status="failed",
                    reason_code="CAREER_PAGE_NOT_FOUND",
                ),
                StageResult(
                    stage="job_board_discovery",
                    status="partial",
                    reason_code="LINKEDIN_NATIVE_ONLY",
                    detail="LinkedIn-native apply remains available.",
                ),
                StageResult(stage="opening_match", status="not_run"),
            ]
        )

        result = discovery_result_from_context(context)

        self.assertEqual(result.pipeline_status, "partial")
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.error_code, "LINKEDIN_NATIVE_ONLY")
        self.assertEqual(result.error, "linkedin_native_only")

    def test_unsupported_terminal_reason_is_preserved(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.stage_results.append(
            StageResult(
                stage="job_board_discovery",
                status="unsupported",
                reason_code="PROVIDER_UNSUPPORTED",
            )
        )

        result = discovery_result_from_context(context)

        self.assertEqual(result.pipeline_status, "unsupported")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "PROVIDER_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
