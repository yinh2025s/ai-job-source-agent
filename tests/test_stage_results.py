import unittest
from pathlib import Path

from job_source_agent.models import (
    PIPELINE_STAGES,
    STAGE_CAREER_DISCOVERY,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
    STAGE_RESULT_VALIDATION,
    CompanyInput,
)
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.reasons import classify_fetch_error, make_stage_result
from job_source_agent.web import Fetcher


ROOT = Path(__file__).resolve().parents[1]


class StageResultTests(unittest.TestCase):
    def setUp(self):
        self.agent = JobSourceAgent(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True),
            enable_career_search=False,
        )

    def test_exact_opening_records_all_seven_successful_stages(self):
        result = self.agent.discover(
            CompanyInput(
                company_name="Google",
                company_website_url="https://www.google.com",
                career_root_url="https://www.google.com/about/careers/applications/",
                job_title="Product Manager, Ads",
            )
        )

        record = result.result_record()

        self.assertEqual(record["result_schema_version"], "2.1")
        self.assertEqual(record["pipeline_status"], "success")
        self.assertEqual([stage["stage"] for stage in record["stages"]], list(PIPELINE_STAGES))
        self.assertEqual(record["opening_match_status"], "success")
        self.assertEqual(record["output_validation_status"], "success")
        self.assertTrue(record["open_position_url"])

    def test_job_board_without_matching_opening_is_partial_not_failure(self):
        result = self.agent.discover(
            CompanyInput(
                company_name="Title Filter",
                company_website_url="https://titlefilter.example",
                career_root_url="https://jobs.lever.co/titlefilter",
                job_title="AI Engineer",
            )
        )
        stages = {stage.stage: stage for stage in result.stage_results}

        self.assertEqual(result.status, "success")
        self.assertEqual(result.pipeline_status, "partial")
        self.assertEqual(stages[STAGE_JOB_BOARD_DISCOVERY].status, "success")
        self.assertEqual(stages[STAGE_OPENING_MATCH].status, "partial")
        self.assertEqual(stages[STAGE_OPENING_MATCH].reason_code, "OPENING_NOT_FOUND")
        self.assertFalse(stages[STAGE_OPENING_MATCH].retryable)
        self.assertEqual(stages[STAGE_OPENING_MATCH].owner, "matcher")
        self.assertEqual(
            stages[STAGE_OPENING_MATCH].evidence[0]["disposition"],
            "verified_inventory_no_match",
        )
        self.assertEqual(
            stages[STAGE_OPENING_MATCH].evidence[0]["confidence"],
            "medium",
        )

    def test_career_discovery_failure_marks_later_stages_not_run(self):
        result = self.agent.discover(
            CompanyInput(
                company_name="Missing Company",
                company_website_url="https://missing-company.example",
                job_title="Data Analyst",
            )
        )
        stages = {stage.stage: stage for stage in result.stage_results}

        self.assertEqual(result.pipeline_status, "failed")
        self.assertEqual(result.error, "fetch_budget_exhausted")
        self.assertEqual(result.error_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertEqual(stages[STAGE_CAREER_DISCOVERY].status, "failed")
        self.assertEqual(stages[STAGE_CAREER_DISCOVERY].reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(stages[STAGE_CAREER_DISCOVERY].retryable)
        self.assertEqual(stages[STAGE_JOB_BOARD_DISCOVERY].status, "not_run")
        self.assertEqual(stages[STAGE_OPENING_MATCH].status, "not_run")
        self.assertEqual(stages[STAGE_RESULT_VALIDATION].status, "success")

    def test_fetch_errors_are_mapped_to_actionable_reason_codes(self):
        self.assertEqual(classify_fetch_error("The read operation timed out"), "NETWORK_TIMEOUT")
        self.assertEqual(classify_fetch_error("[Errno 8] nodename nor servname provided"), "DNS_FAILED")
        self.assertEqual(classify_fetch_error("HTTP Error 429: Too Many Requests"), "RATE_LIMITED")
        self.assertEqual(classify_fetch_error("HTTP Error 404: Not Found"), "HTTP_NOT_FOUND")
        self.assertEqual(classify_fetch_error("HTTP status 599"), "SERVER_ERROR")
        self.assertEqual(classify_fetch_error("Temporary failure in name resolution"), "DNS_FAILED")
        self.assertEqual(classify_fetch_error("parser mismatch"), "PARSING_FAILED")
        self.assertEqual(classify_fetch_error("DNS_FAILED"), "DNS_FAILED")
        self.assertEqual(
            classify_fetch_error("No fixture found for https://jobs.example.test/search"),
            "OFFLINE_FIXTURE_MISSING",
        )

    def test_provider_fetch_failures_keep_retry_and_owner_semantics(self):
        result = make_stage_result(
            STAGE_OPENING_MATCH,
            "failed",
            reason_code="PROVIDER_FETCH_FAILED",
            provider="icims",
        )

        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(result.retryable)
        self.assertEqual(result.owner, "network")

    def test_missing_offline_fixture_is_owned_by_replay_and_not_retryable(self):
        result = make_stage_result(
            STAGE_OPENING_MATCH,
            "partial",
            reason_code="OFFLINE_FIXTURE_MISSING",
            provider="talemetry",
        )

        self.assertEqual(result.reason_code, "OFFLINE_FIXTURE_MISSING")
        self.assertFalse(result.retryable)
        self.assertEqual(result.owner, "replay")


if __name__ == "__main__":
    unittest.main()
