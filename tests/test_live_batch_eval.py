import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from job_source_agent.models import CompanyInput, DiscoveryResult
from job_source_agent.web import Fetcher
from scripts.live_batch_eval import (
    build_summary,
    load_batch_companies,
    prepare_replay_company_for_resume,
    prepare_company,
    record_checkpoint,
    resume_uses_replay_upstream,
    run_company,
)


class LiveBatchEvalTests(unittest.TestCase):
    def test_input_mode_loads_fixed_companies_without_linkedin_search(self):
        args = SimpleNamespace(
            input="samples/live_benchmark_companies.json",
            limit=2,
            linkedin_keywords=None,
            linkedin_location="United States",
            linkedin_pages=1,
        )

        companies = load_batch_companies(args, Fetcher(offline=True))

        self.assertEqual([company.company_name for company in companies], ["Anthropic", "PostHog"])

    def test_prepare_company_preserves_provided_website(self):
        company = CompanyInput(
            company_name="Example Robotics",
            company_website_url="example-robotics.test",
            linkedin_company_url="https://www.linkedin.com/company/example-robotics",
        )
        args = SimpleNamespace(
            fetch_timeout=0.1,
            render_js=False,
            render_budget=0,
            verify_limit=1,
        )

        prepared = prepare_company(company, args)

        self.assertEqual(prepared.company_website_url, "https://example-robotics.test")
        self.assertEqual(
            prepared.source_trace["website_resolution"]["selected"]["reason"],
            "provided by input record",
        )

    def test_input_mode_respects_limit_for_generated_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "companies.json"
            path.write_text(
                """[
                  {"company_name": "A", "company_website_url": "https://a.example"},
                  {"company_name": "B", "company_website_url": "https://b.example"},
                  {"company_name": "C", "company_website_url": "https://c.example"}
                ]""",
                encoding="utf-8",
            )
            args = SimpleNamespace(
                input=str(path),
                limit=1,
                linkedin_keywords=None,
                linkedin_location="United States",
                linkedin_pages=1,
            )

            companies = load_batch_companies(args, Fetcher(offline=True))

        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0].company_name, "A")

    def test_live_expectations_default_to_present_companies_only(self):
        result = {
            "company_name": "A",
            "company_website_url": "https://a.example",
            "career_page_url": "https://a.example/careers",
            "job_list_page_url": "https://a.example/careers",
            "pipeline_status": "partial",
            "status": "success",
            "stages": [{"stage": "job_board_discovery", "status": "success"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "expectations.json"
            path.write_text(
                """{
                  "A": {"expected_minimum_stage": "job_board_discovery"},
                  "B": {"expected_minimum_stage": "job_board_discovery"}
                }""",
                encoding="utf-8",
            )
            args = SimpleNamespace(expectations=str(path), require_all_expectations=False)

            summary = build_summary([result], args, elapsed_sec=1.0)

        self.assertEqual(summary["expectation_checks"]["total"], 1)
        self.assertEqual(summary["expectation_checks"]["failed"], 0)

    def test_live_expectations_can_require_all_companies(self):
        result = {
            "company_name": "A",
            "company_website_url": "https://a.example",
            "career_page_url": "https://a.example/careers",
            "job_list_page_url": "https://a.example/careers",
            "pipeline_status": "partial",
            "status": "success",
            "stages": [{"stage": "job_board_discovery", "status": "success"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "expectations.json"
            path.write_text(
                """{
                  "A": {"expected_minimum_stage": "job_board_discovery"},
                  "B": {"expected_minimum_stage": "job_board_discovery"}
                }""",
                encoding="utf-8",
            )
            args = SimpleNamespace(expectations=str(path), require_all_expectations=True)

            summary = build_summary([result], args, elapsed_sec=1.0)

        self.assertEqual(summary["expectation_checks"]["total"], 2)
        self.assertEqual(summary["expectation_checks"]["failed"], 1)

    def test_record_checkpoint_writes_results_trace_and_summary(self):
        result = DiscoveryResult(
            company_name="A",
            company_website_url="https://a.example",
            career_page_url="https://a.example/careers",
            job_list_page_url="https://a.example/careers",
            status="success",
            pipeline_status="partial",
        )
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "results.json"
            trace_path = Path(directory) / "trace.json"
            summary_path = Path(directory) / "summary.json"
            args = SimpleNamespace(expectations=None)
            results = []
            traces = []

            record_checkpoint(
                1,
                1,
                result,
                0.1,
                results,
                traces,
                output_path,
                trace_path,
                summary_path,
                args,
                0.0,
            )

            self.assertTrue(output_path.exists())
            self.assertTrue(trace_path.exists())
            self.assertTrue(summary_path.exists())

    def test_resume_from_stage_reuses_replay_upstream_evidence(self):
        company = CompanyInput(
            company_name="PostHog",
            company_website_url="posthog.com",
            career_root_url="https://posthog.com/careers/jobs",
            source="replay_input",
        )
        args = SimpleNamespace(resume_from_stage="opening_match")

        prepared = prepare_replay_company_for_resume(company, args)

        self.assertTrue(resume_uses_replay_upstream(args))
        self.assertEqual(prepared.company_website_url, "https://posthog.com")
        self.assertEqual(prepared.source_trace["resume"]["skipped_stages"], [
            "website_resolution",
            "hiring_identity_resolution",
        ])

    def test_resume_from_stage_requires_replay_website(self):
        company = CompanyInput(company_name="Missing Website", source="replay_input")
        args = SimpleNamespace(
            resume_from_stage="opening_match",
            company_time_budget=1,
            website_time_budget=1,
        )

        result = run_company(company, args)

        self.assertEqual(result.error_code, "WEBSITE_NOT_RESOLVED")
        self.assertIn("requires replay input", result.trace["batch_error_detail"])


if __name__ == "__main__":
    unittest.main()
