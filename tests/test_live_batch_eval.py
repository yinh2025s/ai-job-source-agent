import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from job_source_agent.checkpoint import execution_fingerprint
from job_source_agent.contracts import StageExecution
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard
from job_source_agent.models import (
    PIPELINE_STAGES,
    CompanyInput,
    DiscoveryResult,
    StageResult,
)
from job_source_agent.pipeline_status import derive_pipeline_status
from job_source_agent.pipeline_application import discovery_result_from_context
from job_source_agent.process_budget import RemoteProcessError
from job_source_agent.batch_checkpoint import FilesystemBatchCompletionStore
from job_source_agent.run_configuration import AgentConfig, DeterministicRunConfig
from job_source_agent.snapshot import SnapshotStore
from job_source_agent.stage_checkpoint import FilesystemCheckpointStore
from job_source_agent.web import Fetcher, Page
from scripts.live_batch_eval import (
    build_automatic_failure_bundle,
    build_automatic_replay_bundle,
    build_summary,
    enforce_bundle_gates,
    failure_result,
    _inner_deadline_budget,
    _load_completed_companies,
    _downstream_start_stage,
    _ordered_records,
    _recover_checkpoint_prefix,
    _record_company_completion,
    _run_configuration,
    load_batch_companies,
    prepare_replay_company_for_resume,
    prepare_company,
    print_summary,
    record_checkpoint,
    resume_uses_replay_upstream,
    run_company,
    run_pipeline_phase,
    validate_artifact_args,
)


class LiveBatchEvalTests(unittest.TestCase):
    def run_configuration(self, **overrides):
        return DeterministicRunConfig.from_agent_config(AgentConfig(**overrides))

    def test_print_summary_handles_incompatible_baseline(self):
        summary = {
            "total": 1,
            "success": 1,
            "pipeline_status_counts": {"success": 1},
            "with_job_list": 1,
            "with_opening": 1,
            "elapsed_sec": 1.0,
            "rates": {"opening": 1.0},
            "error_counts": {"none": 1},
            "reason_code_counts": {},
            "provider_counts": {"ashby": 1},
            "regression": {"comparison_status": "no_compatible_baseline"},
        }

        output = io.StringIO()
        with redirect_stdout(output):
            print_summary(summary)

        self.assertIn("baseline_comparison: no_compatible_baseline", output.getvalue())

    def test_inner_deadline_leaves_bounded_checkpoint_reserve(self):
        self.assertEqual(_inner_deadline_budget(45), 44)
        self.assertEqual(_inner_deadline_budget(10), 9.5)
        self.assertEqual(_inner_deadline_budget(0.5), 0.45)

    def pipeline_args(self, directory):
        return SimpleNamespace(
            checkpoint_dir=str(Path(directory) / "checkpoints"),
            fixtures_dir="samples/sites",
            offline=True,
            fetch_timeout=0.1,
            render_js=False,
            render_budget=0,
            render_screenshot=False,
            fetch_retries=0,
            retry_base_delay=0.01,
            snapshot_dir=None,
            max_career_candidates=12,
            max_job_pages=8,
            max_career_fetches=12,
            max_career_search_queries=5,
            max_ats_board_fetches=5,
            skip_sitemap=False,
            career_search_timeout=None,
        )

    def save_checkpoint_chain(self, company, args, *, stages=PIPELINE_STAGES[:5]):
        settings = self.run_configuration(
            max_candidates=args.max_career_candidates,
            max_job_pages=args.max_job_pages,
            max_career_candidate_fetches=args.max_career_fetches,
            max_career_search_queries=args.max_career_search_queries,
            max_ats_board_fetches=args.max_ats_board_fetches,
            enable_sitemap_discovery=not args.skip_sitemap,
            career_search_timeout=args.career_search_timeout,
        )
        fingerprint = execution_fingerprint(company.__dict__, settings.digest)
        store = FilesystemCheckpointStore(args.checkpoint_dir)
        discovered_board = DiscoveredJobBoard(
            board=JobBoard(
                url="https://boards.greenhouse.io/checkpoint",
                provider="greenhouse",
                identifier="custom:boards.greenhouse.io",
                replay_safe=True,
            ),
            detection_method="page_evidence",
            evidence_url="https://boards.greenhouse.io/checkpoint",
        )
        executions = {
            "linkedin_discovery": StageExecution(
                StageResult(stage="linkedin_discovery", status="success"),
                trace={"source": "linkedin"},
            ),
            "website_resolution": StageExecution(
                StageResult(stage="website_resolution", status="success"),
                updates={"company_website_url": "https://checkpoint.example"},
                trace={"selected": "https://checkpoint.example"},
            ),
            "hiring_identity_resolution": StageExecution(
                StageResult(stage="hiring_identity_resolution", status="success"),
                updates={
                    "hiring_entity_name": "Checkpoint Labs",
                    "career_root_url": "https://checkpoint.example/careers",
                },
            ),
            "career_discovery": StageExecution(
                StageResult(stage="career_discovery", status="success"),
                updates={"career_page_url": "https://checkpoint.example/careers"},
            ),
            "job_board_discovery": StageExecution(
                StageResult(
                    stage="job_board_discovery",
                    status="success",
                    provider="greenhouse",
                ),
                updates={
                    "job_list_page_url": "https://boards.greenhouse.io/checkpoint",
                    "provider": "greenhouse",
                    "discovered_job_board": discovered_board,
                },
            ),
            "opening_match": StageExecution(
                StageResult(stage="opening_match", status="success"),
                updates={
                    "open_position_url": (
                        "https://boards.greenhouse.io/checkpoint/jobs/exact-opening"
                    )
                },
            ),
            "result_validation": StageExecution(
                StageResult(stage="result_validation", status="success"),
                trace={"pipeline_status": "success", "issues": []},
            ),
        }
        for stage in stages:
            store.save(fingerprint, executions[stage])
        return store, fingerprint

    def test_hard_timeout_recovers_durable_s1_to_s6_with_auditable_events(self):
        company = CompanyInput(
            company_name="Checkpoint Labs",
            linkedin_company_url="https://www.linkedin.com/company/checkpoint-labs",
            job_title="AI Engineer",
        )
        with tempfile.TemporaryDirectory() as directory:
            args = self.pipeline_args(directory)
            _, fingerprint = self.save_checkpoint_chain(
                company,
                args,
                stages=PIPELINE_STAGES[:6],
            )
            recovered_contexts = []

            def capture_recovered_context(context, **kwargs):
                recovered_contexts.append(context)
                return discovery_result_from_context(context, **kwargs)

            with patch(
                "scripts.live_batch_eval.discovery_result_from_context",
                side_effect=capture_recovered_context,
            ):
                recovered = _recover_checkpoint_prefix(company, args)
            result = failure_result(
                company,
                error="company_time_budget_exhausted",
                detail="Validation exceeded the company budget.",
                completed_result=recovered,
            )

        self.assertEqual(
            [stage.status for stage in result.stage_results],
            ["success", "success", "success", "success", "success", "success", "failed"],
        )
        self.assertEqual(result.company_website_url, "https://checkpoint.example")
        self.assertEqual(result.hiring_entity_name, "Checkpoint Labs")
        self.assertEqual(result.career_root_url, "https://checkpoint.example/careers")
        self.assertEqual(result.career_page_url, "https://checkpoint.example/careers")
        self.assertEqual(
            result.job_list_page_url,
            "https://boards.greenhouse.io/checkpoint",
        )
        self.assertEqual(result.stage_results[4].provider, "greenhouse")
        self.assertEqual(
            result.open_position_url,
            "https://boards.greenhouse.io/checkpoint/jobs/exact-opening",
        )
        self.assertEqual(result.stage_results[6].reason_code, "COMPANY_TIME_BUDGET_EXHAUSTED")
        self.assertEqual(result.error_code, "COMPANY_TIME_BUDGET_EXHAUSTED")
        self.assertEqual(result.pipeline_status, "failed")
        self.assertEqual(len(recovered_contexts), 1)
        self.assertIsInstance(
            recovered_contexts[0].discovered_job_board,
            DiscoveredJobBoard,
        )
        self.assertEqual(
            recovered_contexts[0].discovered_job_board.board.url,
            result.job_list_page_url,
        )
        self.assertEqual(recovered_contexts[0].provider, "greenhouse")
        events = result.trace["checkpoint_events"]
        self.assertEqual(
            [event["stage"] for event in events],
            list(PIPELINE_STAGES[:6]),
        )
        self.assertTrue(
            all(event["action"] == "parent_timeout_restore" for event in events)
        )
        self.assertTrue(
            all(event["execution_fingerprint"] == fingerprint for event in events)
        )
        self.assertTrue(
            all(
                set(event) == {"action", "stage", "execution_fingerprint"}
                for event in events
            )
        )

    def test_checkpoint_recovery_stops_at_corrupt_incompatible_or_missing_gap(self):
        cases = ("corrupt", "incompatible", "missing", "semantic")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                company = CompanyInput(
                    company_name="Checkpoint Labs",
                    linkedin_company_url="https://www.linkedin.com/company/checkpoint-labs",
                )
                args = self.pipeline_args(directory)
                stages = list(PIPELINE_STAGES[:6])
                if case == "missing":
                    stages.remove("hiring_identity_resolution")
                store, fingerprint = self.save_checkpoint_chain(
                    company,
                    args,
                    stages=stages,
                )
                if case in {"corrupt", "incompatible", "semantic"}:
                    path = store._checkpoint_path(fingerprint, "hiring_identity_resolution")
                    if case == "corrupt":
                        path.write_text("{truncated", encoding="utf-8")
                    else:
                        payload = json.loads(path.read_text(encoding="utf-8"))
                        if case == "incompatible":
                            payload["checkpoint_schema_version"] = "incompatible"
                        else:
                            payload["execution"]["updates"]["unsupported_field"] = "blocked"
                        path.write_text(json.dumps(payload), encoding="utf-8")

                recovered = _recover_checkpoint_prefix(company, args)
                result = failure_result(
                    company,
                    error="company_time_budget_exhausted",
                    completed_result=recovered,
                )

                self.assertEqual(
                    [stage.stage for stage in recovered.stage_results],
                    ["linkedin_discovery", "website_resolution"],
                )
                self.assertEqual(result.stage_status("hiring_identity_resolution"), "failed")
                self.assertNotEqual(result.stage_status("opening_match"), "success")
                self.assertIsNone(result.open_position_url)
                self.assertEqual(result.error_code, "COMPANY_TIME_BUDGET_EXHAUSTED")

    def test_complete_checkpoint_chain_recovers_real_result(self):
        company = CompanyInput(
            company_name="Checkpoint Labs",
            linkedin_company_url="https://www.linkedin.com/company/checkpoint-labs",
        )
        with tempfile.TemporaryDirectory() as directory:
            args = self.pipeline_args(directory)
            self.save_checkpoint_chain(company, args, stages=PIPELINE_STAGES)

            recovered = _recover_checkpoint_prefix(company, args)

        self.assertEqual(len(recovered.stage_results), len(PIPELINE_STAGES))
        self.assertEqual(recovered.pipeline_status, "success")
        self.assertEqual(
            recovered.open_position_url,
            "https://boards.greenhouse.io/checkpoint/jobs/exact-opening",
        )

    def test_result_validation_timeout_is_consistently_failed(self):
        completed = DiscoveryResult(
            company_name="Complete Domain Pipeline",
            company_website_url="https://complete.example",
            career_page_url="https://complete.example/careers",
            job_list_page_url="https://boards.greenhouse.io/complete",
            open_position_url="https://boards.greenhouse.io/complete/jobs/123",
            stage_results=[
                StageResult(stage=stage, status="success")
                for stage in PIPELINE_STAGES[:-1]
            ],
            trace={"stages": {}},
        )
        company = CompanyInput(company_name="Complete Domain Pipeline")

        result = failure_result(
            company,
            error="company_time_budget_exhausted",
            completed_result=completed,
        )

        self.assertEqual(result.stage_status("result_validation"), "failed")
        self.assertEqual(result.pipeline_status, "failed")
        self.assertEqual(derive_pipeline_status(result.stage_results), "failed")
        self.assertEqual(
            result.trace["stages"]["result_validation"]["pipeline_status"],
            "failed",
        )

    def test_failure_result_drops_fields_and_trace_after_first_stage_gap(self):
        completed = DiscoveryResult(
            company_name="Gap Example",
            company_website_url="https://gap.example",
            hiring_entity_name="Must Not Leak",
            career_root_url="https://gap.example/career-root",
            career_page_url="https://gap.example/careers",
            job_list_page_url="https://jobs.gap.example",
            open_position_url="https://jobs.gap.example/123",
            stage_results=[
                StageResult(stage="linkedin_discovery", status="success"),
                StageResult(stage="website_resolution", status="success"),
                StageResult(stage="hiring_identity_resolution", status="failed"),
                StageResult(stage="career_discovery", status="success"),
                StageResult(stage="job_board_discovery", status="success"),
                StageResult(stage="opening_match", status="success"),
            ],
            trace={
                "stages": {
                    "website_resolution": {"selected": "https://gap.example"},
                    "opening_match": {"selected": "must-not-leak"},
                }
            },
        )

        result = failure_result(
            CompanyInput(company_name="Gap Example"),
            error="company_time_budget_exhausted",
            completed_result=completed,
        )

        self.assertEqual(result.company_website_url, "https://gap.example")
        self.assertIsNone(result.hiring_entity_name)
        self.assertIsNone(result.career_root_url)
        self.assertIsNone(result.career_page_url)
        self.assertIsNone(result.job_list_page_url)
        self.assertIsNone(result.open_position_url)
        self.assertNotIn("opening_match", result.trace["stages"])

    def test_remote_worker_error_recovers_durable_downstream_prefix(self):
        company = CompanyInput(
            company_name="Remote Failure",
            linkedin_company_url="https://www.linkedin.com/company/remote-failure",
        )
        upstream = DiscoveryResult(
            company_name=company.company_name,
            company_website_url="https://remote.example",
            stage_results=[
                StageResult(stage="linkedin_discovery", status="success"),
                StageResult(stage="website_resolution", status="success"),
                StageResult(stage="hiring_identity_resolution", status="success"),
            ],
        )
        recovered = DiscoveryResult(
            company_name=company.company_name,
            company_website_url="https://remote.example",
            career_page_url="https://remote.example/careers",
            job_list_page_url="https://jobs.remote.example",
            stage_results=[
                StageResult(stage=stage, status="success")
                for stage in PIPELINE_STAGES[:5]
            ],
            trace={"stages": {}},
        )
        args = SimpleNamespace(
            website_time_budget=20,
            company_time_budget=45,
            resume_from_stage=None,
            rerun_stage=None,
        )

        with patch(
            "scripts.live_batch_eval.run_with_process_budget",
            side_effect=[upstream, RemoteProcessError("worker crashed")],
        ), patch(
            "scripts.live_batch_eval._recover_checkpoint_prefix",
            return_value=recovered,
        ), patch(
            "scripts.live_batch_eval._run_configuration",
            return_value=self.run_configuration(),
        ):
            result = run_company(company, args)

        self.assertEqual(result.stage_status("job_board_discovery"), "success")
        self.assertEqual(result.stage_status("opening_match"), "failed")
        self.assertEqual(result.job_list_page_url, "https://jobs.remote.example")
        self.assertEqual(result.error_code, "FETCH_FAILED")
        self.assertEqual(result.error, "batch_worker_failed")

    def test_downstream_failure_preserves_completed_upstream_evidence(self):
        company = CompanyInput(
            company_name="Hadrian",
            linkedin_company_url="https://www.linkedin.com/company/hadrianautomation",
            job_title="Frontend Software Engineer",
            source_trace={"input_marker": "kept"},
        )
        upstream = DiscoveryResult(
            company_name="Hadrian",
            company_website_url="https://www.hadrian.co/",
            hiring_entity_name="Hadrian Automation",
            career_root_url="https://www.hadrian.co/careers",
            stage_results=[
                StageResult(stage="linkedin_discovery", status="success"),
                StageResult(
                    stage="website_resolution",
                    status="success",
                    duration_ms=321,
                    evidence=[
                        {"field": "company_website_url", "url": "https://www.hadrian.co/"}
                    ],
                ),
                StageResult(
                    stage="hiring_identity_resolution",
                    status="success",
                    duration_ms=7,
                ),
            ],
            trace={
                "stages": {"website_resolution": {"selected": "https://www.hadrian.co/"}},
                "source_trace": {"upstream_marker": "kept"},
            },
        )

        result = failure_result(
            company,
            error="company_time_budget_exhausted",
            detail="Career discovery exceeded its budget.",
            completed_result=upstream,
        )

        self.assertEqual(result.company_website_url, "https://www.hadrian.co/")
        self.assertEqual(result.hiring_entity_name, "Hadrian Automation")
        self.assertEqual(result.career_root_url, "https://www.hadrian.co/careers")
        self.assertEqual(result.stage_status("website_resolution"), "success")
        self.assertEqual(result.stage_status("hiring_identity_resolution"), "success")
        self.assertEqual(result.stage_status("career_discovery"), "failed")
        self.assertEqual(result.stage_results[1].duration_ms, 321)
        self.assertEqual(
            result.trace["stages"]["website_resolution"]["selected"],
            "https://www.hadrian.co/",
        )
        self.assertEqual(
            result.trace["source_trace"],
            {"upstream_marker": "kept", "input_marker": "kept"},
        )

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

    def test_fixed_live_expectations_cover_every_input_company(self):
        companies = json.loads(
            Path("samples/live_benchmark_companies.json").read_text(encoding="utf-8")
        )
        expectations = json.loads(
            Path("samples/live_benchmark_expectations.json").read_text(encoding="utf-8")
        )

        self.assertEqual(
            {company["company_name"] for company in companies},
            set(expectations),
        )
        self.assertEqual(len(companies), 51)

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

    def test_linkedin_mode_freezes_and_restores_dynamic_cohort(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(
                input=None,
                limit=30,
                linkedin_keywords="AI Engineer",
                linkedin_location="United States",
                linkedin_pages=5,
                batch_checkpoint_dir=str(Path(directory) / "batch"),
                linkedin_manifest=None,
                no_resume=False,
            )
            with (
                patch("scripts.live_batch_eval.LinkedInJobsDiscoverer") as discoverer,
                patch("scripts.live_batch_eval.linkedin_postings_to_company_inputs") as convert,
            ):
                discoverer.return_value.search.return_value = [object()]
                convert.return_value = [
                    CompanyInput(
                        company_name="Stable Cohort",
                        linkedin_company_url="https://www.linkedin.com/company/stable-cohort",
                    )
                ]

                first = load_batch_companies(args, Fetcher(offline=True))
                second = load_batch_companies(args, Fetcher(offline=True))

        self.assertEqual([company.company_name for company in first], ["Stable Cohort"])
        self.assertEqual([company.company_name for company in second], ["Stable Cohort"])
        discoverer.return_value.search.assert_called_once()
        self.assertEqual(args.linkedin_manifest_action, "restored")

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

    def test_summary_binds_evaluation_to_effective_cohort_and_expectations(self):
        result = {
            "company_name": "A",
            "pipeline_status": "partial",
            "status": "success",
            "stages": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "expectations.json"
            path.write_text(
                json.dumps({"A": {"expected_minimum_stage": "career_discovery"}}),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                expectations=str(path),
                require_all_expectations=False,
                cohort_companies_sha256="a" * 64,
            )

            summary = build_summary([result], args, elapsed_sec=1.0)

        self.assertEqual(
            summary["evaluation_manifest"]["companies_sha256"],
            "a" * 64,
        )
        self.assertRegex(
            summary["evaluation_manifest"]["expectations_sha256"],
            r"^[0-9a-f]{64}$",
        )
        expected = self.run_configuration(
            max_candidates=6,
            max_job_pages=3,
            max_career_candidate_fetches=5,
            max_career_search_queries=5,
            max_ats_board_fetches=5,
            career_search_timeout=6,
        )
        self.assertEqual(summary["run_configuration"], expected.to_payload())
        self.assertEqual(summary["run_configuration_digest"], expected.digest)
        self.assertEqual(
            summary["evaluation_manifest"]["run_configuration_digest"],
            expected.digest,
        )
        self.assertRegex(summary["batch_execution_configuration_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            summary["evaluation_manifest"]["batch_execution_configuration_digest"],
            summary["batch_execution_configuration_digest"],
        )

    def test_batch_execution_settings_change_completion_scope(self):
        base = SimpleNamespace(expectations=None, company_time_budget=45, website_time_budget=20)
        changed = SimpleNamespace(expectations=None, company_time_budget=60, website_time_budget=20)

        first = build_summary([], base, elapsed_sec=0)
        second = build_summary([], changed, elapsed_sec=0)

        self.assertNotEqual(
            first["batch_execution_configuration_digest"],
            second["batch_execution_configuration_digest"],
        )

    def test_failed_or_incomplete_automatic_replay_gate_is_fatal(self):
        for status in ("failed", "incomplete"):
            with self.subTest(status=status):
                with self.assertRaisesRegex(SystemExit, status):
                    enforce_bundle_gates(
                        {"replay_bundle": {"outcome_gate": status}}
                    )

        enforce_bundle_gates({"replay_bundle": {"outcome_gate": "passed"}})

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
            trace={
                "checkpoint_events": [
                    {"stage": "website_resolution", "action": "restore"},
                    {"stage": "career_discovery", "action": "save"},
                ]
            },
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
            result_records = json.loads(output_path.read_text(encoding="utf-8"))
            summary = json.loads(summary_path.read_text(encoding="utf-8"))

            self.assertNotIn("trace", result_records[0])
            self.assertEqual(summary["checkpoint_action_counts"], {"restore": 1, "save": 1})
            self.assertEqual(
                summary["checkpoint_stage_counts"],
                {"website_resolution": 1, "career_discovery": 1},
            )

    def test_company_completion_resume_restores_only_compatible_inputs(self):
        first = CompanyInput(company_name="A", company_website_url="https://a.example")
        second = CompanyInput(company_name="B", company_website_url="https://b.example")
        with tempfile.TemporaryDirectory() as directory:
            store = FilesystemBatchCompletionStore(Path(directory) / "completed")
            store.save(
                {"company_name": "A", "company_website_url": "https://a.example"},
                {"company_name": "A", "status": "success"},
                {"company_name": "A", "trace": {}},
                1.2,
            )

            restored = _load_completed_companies(
                [first, second],
                store,
                SimpleNamespace(no_resume=False, rerun_stage=None),
            )

        self.assertEqual(list(restored), [1])
        self.assertEqual(restored[1][0]["company_name"], "A")
        self.assertEqual(restored[1][2], 1.2)

    def test_company_completion_resume_is_bypassed_for_no_resume_or_rerun(self):
        company = CompanyInput(company_name="A", company_website_url="https://a.example")
        with tempfile.TemporaryDirectory() as directory:
            store = FilesystemBatchCompletionStore(Path(directory) / "completed")
            store.save(
                {"company_name": "A", "company_website_url": "https://a.example"},
                {"company_name": "A"},
                {"company_name": "A"},
                0.1,
            )

            self.assertEqual(
                _load_completed_companies(
                    [company], store, SimpleNamespace(no_resume=True, rerun_stage=None)
                ),
                {},
            )
            self.assertEqual(
                _load_completed_companies(
                    [company], store, SimpleNamespace(no_resume=False, rerun_stage="opening_match")
                ),
                {},
            )

    def test_retryable_completion_invalidates_failed_stage_and_preserves_upstream(self):
        company = CompanyInput(company_name="A", company_website_url="https://a.example")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = SimpleNamespace(
                no_resume=False,
                rerun_stage=None,
                checkpoint_dir=str(root / "stages"),
            )
            store = FilesystemBatchCompletionStore(root / "completed")
            stages = []
            for stage in PIPELINE_STAGES:
                if stage == "career_discovery":
                    stages.append(
                        {
                            "stage": stage,
                            "status": "failed",
                            "retryable": True,
                            "reason_code": "NETWORK_TIMEOUT",
                        }
                    )
                elif len(stages) >= 4 and stage != "result_validation":
                    stages.append({"stage": stage, "status": "not_run", "retryable": False})
                else:
                    stages.append({"stage": stage, "status": "success", "retryable": False})
            input_record = {
                "linkedin_job_url": "",
                "external_apply_url": None,
                "company_name": "A",
                "company_website_url": "https://a.example",
                "hiring_entity_name": None,
                "career_root_url": None,
                "linkedin_html_path": None,
                "linkedin_company_url": None,
                "job_title": None,
                "job_location": None,
                "source": "input",
                "source_trace": {},
            }
            store.save(
                input_record,
                {"company_name": "A", "pipeline_status": "failed", "stages": stages},
                {"company_name": "A", "pipeline_status": "failed", "stages": stages, "trace": {}},
                1.0,
            )
            fingerprint = execution_fingerprint(
                input_record,
                _run_configuration(args).digest,
            )
            stage_store = FilesystemCheckpointStore(args.checkpoint_dir)
            for stage in PIPELINE_STAGES:
                stage_store.save(
                    fingerprint,
                    StageExecution(StageResult(stage=stage, status="success")),
                )

            restored = _load_completed_companies([company], store, args)

            self.assertEqual(restored, {})
            self.assertEqual(args.batch_completion_resume_stats["retryable_resubmit"], 1)
            self.assertEqual(
                args.batch_completion_resume_decisions[1],
                {
                    "action": "retryable_resubmit",
                    "reason": "retryable_stage_failure",
                    "stage": "career_discovery",
                    "reason_code": "NETWORK_TIMEOUT",
                },
            )
            for stage in PIPELINE_STAGES[:3]:
                self.assertIsNotNone(stage_store.load(fingerprint, stage))
            for stage in PIPELINE_STAGES[3:]:
                self.assertIsNone(stage_store.load(fingerprint, stage))

    def test_company_completions_are_persisted_and_rendered_in_input_order(self):
        companies = {
            1: CompanyInput(company_name="A", company_website_url="https://a.example"),
            2: CompanyInput(company_name="B", company_website_url="https://b.example"),
        }
        results = {
            index: DiscoveryResult(
                company_name=company.company_name,
                company_website_url=company.company_website_url,
                status="success",
                pipeline_status="partial",
            )
            for index, company in companies.items()
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = FilesystemBatchCompletionStore(root / "completed")
            completed = {}
            args = SimpleNamespace(expectations=None)
            for index in (2, 1):
                _record_company_completion(
                    index,
                    2,
                    companies[index],
                    results[index],
                    0.1,
                    completed,
                    store,
                    root / "results.json",
                    root / "trace.json",
                    root / "summary.json",
                    args,
                    0.0,
                )

            ordered_results, ordered_traces = _ordered_records(completed)
            disk_results = json.loads((root / "results.json").read_text(encoding="utf-8"))

        self.assertEqual([item["company_name"] for item in ordered_results], ["A", "B"])
        self.assertEqual([item["company_name"] for item in ordered_traces], ["A", "B"])
        self.assertEqual([item["company_name"] for item in disk_results], ["A", "B"])

    def test_resubmitted_completion_persists_privacy_safe_resume_provenance(self):
        company = CompanyInput(company_name="A", company_website_url="https://a.example")
        result = DiscoveryResult(
            company_name="A",
            company_website_url="https://a.example",
            status="success",
            pipeline_status="success",
        )
        marker = {
            "action": "retryable_resubmit",
            "reason": "retryable_stage_failure",
            "stage": "career_discovery",
            "reason_code": "NETWORK_TIMEOUT",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = FilesystemBatchCompletionStore(root / "completed")
            completed = {}
            args = SimpleNamespace(
                expectations=None,
                batch_completion_resume_decisions={1: marker},
                batch_completion_resume_stats={"retryable_resubmit": 1},
            )

            _record_company_completion(
                1,
                1,
                company,
                result,
                0.1,
                completed,
                store,
                root / "results.json",
                root / "trace.json",
                root / "summary.json",
                args,
                0.0,
            )
            saved = store.scan(
                [
                    {
                        "linkedin_job_url": "",
                        "external_apply_url": None,
                        "company_name": "A",
                        "company_website_url": "https://a.example",
                        "hiring_entity_name": None,
                        "career_root_url": None,
                        "linkedin_html_path": None,
                        "linkedin_company_url": None,
                        "job_title": None,
                        "job_location": None,
                        "source": "input",
                        "source_trace": {},
                    }
                ]
            )
            summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))

        completion = next(iter(saved.values()))
        self.assertEqual(completion.trace["trace"]["batch_completion_resume"], marker)
        self.assertEqual(summary["batch_completion_resume"], {"retryable_resubmit": 1})

    def test_failed_completion_publish_does_not_expose_derived_results(self):
        company = CompanyInput(
            company_name="A",
            company_website_url="https://a.example",
        )
        result = DiscoveryResult(
            company_name="A",
            company_website_url="https://a.example",
            status="success",
            pipeline_status="partial",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = FilesystemBatchCompletionStore(root / "completed")
            completed = {}
            with patch.object(
                store,
                "save",
                side_effect=OSError("injected completion publish failure"),
            ):
                with self.assertRaisesRegex(OSError, "completion publish failure"):
                    _record_company_completion(
                        1,
                        1,
                        company,
                        result,
                        0.1,
                        completed,
                        store,
                        root / "results.json",
                        root / "trace.json",
                        root / "summary.json",
                        SimpleNamespace(expectations=None),
                        0.0,
                    )

            self.assertEqual(completed, {})
            self.assertFalse((root / "results.json").exists())
            self.assertFalse((root / "trace.json").exists())
            self.assertFalse((root / "summary.json").exists())

    def test_build_summary_uses_trace_records_for_checkpoint_activity(self):
        result = {
            "company_name": "A",
            "status": "success",
            "pipeline_status": "partial",
        }
        trace = {
            **result,
            "trace": {
                "checkpoint_events": [
                    {"stage": "opening_match", "action": "restore"},
                ]
            },
        }

        summary = build_summary(
            [result],
            SimpleNamespace(expectations=None),
            elapsed_sec=1.0,
            traces=[trace],
        )

        self.assertEqual(summary["checkpoint_action_counts"], {"restore": 1})
        self.assertEqual(summary["checkpoint_stage_counts"], {"opening_match": 1})

    def test_failure_bundle_configuration_requires_snapshots_and_positive_limit(self):
        with self.assertRaisesRegex(SystemExit, "requires --snapshot-dir"):
            validate_artifact_args(
                SimpleNamespace(
                    failure_bundle_dir="bundle",
                    snapshot_dir=None,
                    failure_bundle_limit=20,
                )
            )
        with self.assertRaisesRegex(SystemExit, "requires --snapshot-dir"):
            validate_artifact_args(
                SimpleNamespace(
                    failure_bundle_dir=None,
                    replay_bundle_dir="bundle",
                    snapshot_dir=None,
                    failure_bundle_limit=20,
                    replay_bundle_limit=50,
                )
            )
        with self.assertRaisesRegex(SystemExit, "greater than zero"):
            validate_artifact_args(
                SimpleNamespace(
                    failure_bundle_dir=None,
                    snapshot_dir=None,
                    failure_bundle_limit=0,
                )
            )

    def test_automatic_failure_bundle_replays_partial_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board_url = "https://jobs.example.test/jobs"
            SnapshotStore(root / "snapshots").write_page(
                Page(
                    url=board_url,
                    final_url=board_url,
                    html='<html><a href="/jobs/123-data-analyst">Data Analyst</a></html>',
                    source="live",
                ),
                request_url=board_url,
            )
            trace_path = root / "trace.json"
            trace_path.write_text(
                json.dumps(
                    [
                        {
                            "company_name": "Example Data",
                            "company_website_url": "https://example.test",
                            "career_root_url": board_url,
                            "linkedin_job_title": "Data Analyst",
                            "pipeline_status": "partial",
                            "run_configuration": self.run_configuration().to_payload(),
                            "run_configuration_digest": self.run_configuration().digest,
                            "stages": [
                                {
                                    "stage": "opening_match",
                                    "status": "partial",
                                    "reason_code": "OPENING_NOT_FOUND",
                                }
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                failure_bundle_dir=str(root / "bundle"),
                failure_bundle_limit=20,
                snapshot_dir=str(root / "snapshots"),
            )

            manifest = build_automatic_failure_bundle(args, trace_path)
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["summary"]["total"], 1)
        self.assertIn("123-data-analyst", replay_results[0]["open_position_url"])

    def test_automatic_replay_bundle_selects_success_and_all_other_statuses(self):
        args = SimpleNamespace(
            replay_bundle_dir="bundle",
            replay_bundle_limit=11,
            snapshot_dir="snapshots",
        )

        with patch("scripts.live_batch_eval.replay_failure_bundle") as replay:
            replay.return_value = {"status": "success", "summary": {"total": 1}}
            manifest = build_automatic_replay_bundle(args, Path("trace.json"))

        self.assertEqual(manifest["status"], "success")
        replay_args = replay.call_args.args[0]
        self.assertIsNone(replay_args.pipeline_status)
        self.assertEqual(replay_args.limit, 11)
        self.assertTrue(replay_args.include_missing_website)
        self.assertEqual(replay.call_args.kwargs, {"allow_empty": True})

    def test_automatic_failure_bundle_records_skipped_when_batch_is_green(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = root / "trace.json"
            trace_path.write_text(
                json.dumps(
                    [
                        {
                            "company_name": "Healthy",
                            "company_website_url": "https://healthy.example",
                            "pipeline_status": "success",
                            "stages": [],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                failure_bundle_dir=str(root / "bundle"),
                failure_bundle_limit=20,
                snapshot_dir=str(root / "snapshots"),
            )

            manifest = build_automatic_failure_bundle(args, trace_path)

        self.assertEqual(manifest["status"], "skipped")
        self.assertEqual(manifest["summary"]["total"], 0)

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
        self.assertIn("replay company_website_url", result.trace["batch_error_detail"])
        self.assertIn("external_apply_url", result.trace["batch_error_detail"])

    def test_external_apply_bypasses_missing_website_in_two_phase_runner(self):
        company = CompanyInput(
            company_name="Missing Marketing Site",
            external_apply_url=(
                "https://company.wd5.myworkdayjobs.com/en-US/acme/job/New-York-NY/"
                "Data-Analyst_R123"
            ),
            job_title="Data Analyst",
            job_location="New York, NY",
            source="linkedin_browser_extension",
        )
        with tempfile.TemporaryDirectory() as directory:
            args = self.pipeline_args(directory)
            args.company_time_budget = 10
            args.website_time_budget = 5
            args.resume_from_stage = None
            args.rerun_stage = None

            result = run_company(company, args)

        self.assertEqual(result.stage_status("website_resolution"), "failed")
        self.assertEqual(result.stage_status("career_discovery"), "not_run")
        self.assertEqual(
            result.job_list_page_url,
            "https://company.wd5.myworkdayjobs.com/en-US/acme",
        )
        self.assertIn("Data-Analyst_R123", result.open_position_url)
        self.assertEqual(result.pipeline_status, "success")
        self.assertIsNone(result.error_code)

    def test_external_apply_allows_resume_fallback_without_website(self):
        company = CompanyInput(
            company_name="Missing Marketing Site",
            external_apply_url="https://company.wd5.myworkdayjobs.com/en-US/acme/job/Role_R1",
            source="replay_input",
        )
        with tempfile.TemporaryDirectory() as directory:
            args = self.pipeline_args(directory)
            args.resume_from_stage = "opening_match"

            start_at, fallback = _downstream_start_stage(company, args)

        self.assertEqual(start_at, "career_discovery")
        self.assertEqual(fallback, "rebuild_downstream")

    def test_resume_from_job_board_restores_s1_to_s4_without_reexecution(self):
        company = CompanyInput(
            company_name="Aurora Data",
            company_website_url="https://aurora-data.example",
            job_title="AI Engineer",
        )
        with tempfile.TemporaryDirectory() as directory:
            args = self.pipeline_args(directory)
            args.company_time_budget = 10
            args.website_time_budget = 5
            args.resume_from_stage = None
            args.rerun_stage = None
            first = run_company(company, args)
            self.assertEqual(first.status, "success")

            args.resume_from_stage = "job_board_discovery"
            resumed = run_company(company, args)

        events = resumed.trace["checkpoint_events"]
        restored = [event["stage"] for event in events if event["action"] == "restore"]
        saved = [event["stage"] for event in events if event["action"] == "save"]
        self.assertEqual(
            restored,
            [
                "linkedin_discovery",
                "website_resolution",
                "hiring_identity_resolution",
                "career_discovery",
            ],
        )
        self.assertNotIn("career_discovery", saved)
        self.assertEqual(saved[0], "job_board_discovery")
        self.assertEqual(
            resumed.trace["source_trace"]["resume"]["effective_start_stage"],
            "job_board_discovery",
        )

    def test_resume_from_opening_match_restores_s1_to_s5_without_reexecution(self):
        company = CompanyInput(
            company_name="Aurora Data",
            company_website_url="https://aurora-data.example",
            job_title="AI Engineer",
        )
        with tempfile.TemporaryDirectory() as directory:
            args = self.pipeline_args(directory)
            args.company_time_budget = 10
            args.website_time_budget = 5
            args.resume_from_stage = None
            args.rerun_stage = None
            run_company(company, args)

            args.resume_from_stage = "opening_match"
            resumed = run_company(company, args)

        events = resumed.trace["checkpoint_events"]
        restored = [event["stage"] for event in events if event["action"] == "restore"]
        saved = [event["stage"] for event in events if event["action"] == "save"]
        self.assertEqual(
            restored,
            [
                "linkedin_discovery",
                "website_resolution",
                "hiring_identity_resolution",
                "career_discovery",
                "job_board_discovery",
            ],
        )
        self.assertNotIn("job_board_discovery", saved)
        self.assertEqual(saved[0], "opening_match")
        self.assertIn("d9d64766", resumed.open_position_url)

    def test_later_resume_without_complete_checkpoints_falls_back_to_career_discovery(
        self,
    ):
        company = CompanyInput(
            company_name="Aurora Data",
            company_website_url="https://aurora-data.example",
            job_title="AI Engineer",
            source="replay_input",
        )
        with tempfile.TemporaryDirectory() as directory:
            args = self.pipeline_args(directory)
            args.resume_from_stage = "opening_match"

            start_at, fallback = _downstream_start_stage(company, args)

        self.assertEqual(start_at, "career_discovery")
        self.assertEqual(fallback, "rebuild_downstream")
        self.assertIn("job_board_discovery", company.source_trace["resume"]["missing_checkpoints"])

    def test_resume_rebuilds_when_s4_checkpoint_has_unsupported_update(self):
        company = CompanyInput(
            company_name="Checkpoint Labs",
            company_website_url="https://checkpoint.example",
        )
        with tempfile.TemporaryDirectory() as directory:
            args = self.pipeline_args(directory)
            store, fingerprint = self.save_checkpoint_chain(company, args)
            path = store._checkpoint_path(fingerprint, "career_discovery")
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["execution"]["updates"]["unsupported_field"] = "blocked"
            path.write_text(json.dumps(payload), encoding="utf-8")
            args.resume_from_stage = "job_board_discovery"

            start_at, fallback = _downstream_start_stage(company, args)

        self.assertEqual(start_at, "career_discovery")
        self.assertEqual(fallback, "rebuild_downstream")
        self.assertIn(
            "career_discovery",
            company.source_trace["resume"]["missing_checkpoints"],
        )

    def test_resume_rebuilds_when_s4_checkpoint_has_malformed_career_url(self):
        company = CompanyInput(
            company_name="Checkpoint Labs",
            company_website_url="https://checkpoint.example",
        )
        with tempfile.TemporaryDirectory() as directory:
            args = self.pipeline_args(directory)
            store, fingerprint = self.save_checkpoint_chain(company, args)
            path = store._checkpoint_path(fingerprint, "career_discovery")
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["execution"]["updates"]["career_page_url"] = ["not", "a", "url"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            args.resume_from_stage = "job_board_discovery"

            start_at, fallback = _downstream_start_stage(company, args)

        self.assertEqual(start_at, "career_discovery")
        self.assertEqual(fallback, "rebuild_downstream")
        self.assertIn(
            "career_discovery",
            company.source_trace["resume"]["missing_checkpoints"],
        )

    def test_two_pipeline_phases_restore_s1_to_s3_checkpoint_updates(self):
        company = CompanyInput(
            company_name="Aurora Data",
            company_website_url="https://aurora-data.example",
            job_title="AI Engineer",
        )
        with tempfile.TemporaryDirectory() as directory:
            args = self.pipeline_args(directory)
            upstream = run_pipeline_phase(
                company,
                args,
                None,
                "hiring_identity_resolution",
                None,
            )
            downstream = run_pipeline_phase(
                company,
                args,
                "career_discovery",
                None,
                None,
            )

        self.assertEqual(upstream.company_website_url, "https://aurora-data.example")
        self.assertIsNone(upstream.career_page_url)
        self.assertEqual(downstream.status, "success")
        self.assertIn("d9d64766", downstream.open_position_url)
        self.assertEqual(
            downstream.stage_status("website_resolution"),
            "success",
        )


if __name__ == "__main__":
    unittest.main()
