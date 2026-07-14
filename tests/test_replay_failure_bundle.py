import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from job_source_agent.snapshot import SnapshotStore
from job_source_agent.models import PIPELINE_STAGES
from job_source_agent.run_configuration import AgentConfig, DeterministicRunConfig
from job_source_agent.web import Page
from scripts.replay_failure_bundle import (
    FailureReplayError,
    _build_outcome_gate,
    _build_record_integrity,
    _export_replay_records_with_sources,
    _replay_resume_stage,
    main,
    replay_failure_bundle,
)


class FailureReplayBundleTests(unittest.TestCase):
    def _args(self, root: Path, **overrides):
        values = {
            "results": str(root / "results.json"),
            "snapshot_dir": str(root / "snapshots"),
            "output_dir": str(root / "bundle"),
            "pipeline_status": ["partial"],
            "stage": "opening_match",
            "stage_status": ["partial"],
            "reason_code": ["OPENING_NOT_FOUND"],
            "provider": None,
            "limit": None,
            "include_missing_website": False,
            "legacy_run_config": "composition-defaults",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _write_inputs(self, root: Path):
        board_url = "https://jobs.example.test/jobs"
        results = [
            {
                "company_name": "Example Data",
                "company_website_url": "https://example.test",
                "career_root_url": board_url,
                "career_page_url": board_url,
                "job_list_page_url": board_url,
                "linkedin_job_title": "Data Analyst",
                "pipeline_status": "partial",
                "stages": [
                    {
                        "stage": "opening_match",
                        "status": "partial",
                        "reason_code": "OPENING_NOT_FOUND",
                    }
                ],
            }
        ]
        (root / "results.json").write_text(json.dumps(results), encoding="utf-8")
        homepage_url = "https://example.test"
        SnapshotStore(root / "snapshots").write_page(
            Page(
                url=homepage_url,
                final_url=homepage_url,
                html=f'<html><a href="{board_url}">Careers</a></html>',
                source="live",
            ),
            request_url=homepage_url,
        )
        SnapshotStore(root / "snapshots").write_page(
            Page(
                url=board_url,
                final_url=board_url,
                html=(
                    '<html><body><a href="/jobs/123-data-analyst">'
                    "Data Analyst</a></body></html>"
                ),
                source="live",
            ),
            request_url=board_url,
        )
        detail_url = "https://jobs.example.test/jobs/123-data-analyst"
        SnapshotStore(root / "snapshots").write_page(
            Page(
                url=detail_url,
                final_url=detail_url,
                html="<html><h1>Data Analyst</h1><p>Example Data</p></html>",
                source="live",
            ),
            request_url=detail_url,
        )
        for query_url in (
            f"{board_url}?q=Missing+Role",
            f"{board_url}?search=Missing+Role",
        ):
            SnapshotStore(root / "snapshots").write_page(
                Page(
                    url=query_url,
                    final_url=query_url,
                    html=(
                        '<html><body><a href="/jobs/123-data-analyst">'
                        "Data Analyst</a></body></html>"
                    ),
                    source="live",
                ),
                request_url=query_url,
            )

    def test_reproduced_failure_passes_outcome_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            results_path = root / "results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results[0]["linkedin_job_title"] = "Missing Role"
            results_path.write_text(json.dumps(results), encoding="utf-8")

            manifest = replay_failure_bundle(self._args(root))
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest["summary"]["total"], 1)
        self.assertEqual(
            manifest["summary"]["run_configuration_digest"],
            manifest["run_configuration_digest"],
        )
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["summary"]["checkpoint_action_counts"]["save"], 7)
        self.assertIsNone(replay_results[0]["open_position_url"])
        self.assertNotIn(str(root), json.dumps(manifest))
        self.assertEqual(manifest["paths"]["fixtures"], "offline/sites")
        self.assertEqual(manifest["outcome_gate"]["status"], "passed")
        self.assertEqual(
            manifest["outcome_gate"]["classification_counts"],
            {
                "reproduced": 1,
                "expected_transition": 0,
                "budget_recovery": 0,
                "fixture_gap": 0,
                "mismatch": 0,
            },
        )
        comparison = manifest["outcome_gate"]["records"][0]
        self.assertEqual(comparison["classification"], "reproduced")
        self.assertEqual(comparison["original_outcome"], comparison["replay_outcome"])
        self.assertEqual(manifest["run_configuration_provenance"], "legacy_defaulted")

    def test_source_run_configuration_is_replayed_faithfully_and_not_exported_as_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            source_config = DeterministicRunConfig.from_agent_config(
                AgentConfig(
                    max_candidates=19,
                    max_job_pages=7,
                    max_career_candidate_fetches=11,
                    max_career_search_queries=2,
                    max_ats_board_fetches=3,
                    enable_sitemap_discovery=False,
                    enable_career_search=False,
                    career_search_timeout=4.5,
                )
            )
            results_path = root / "results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results[0]["linkedin_job_title"] = "Missing Role"
            results[0]["run_configuration"] = source_config.to_payload()
            results[0]["run_configuration_digest"] = source_config.digest
            results_path.write_text(json.dumps(results), encoding="utf-8")

            manifest = replay_failure_bundle(self._args(root, legacy_run_config=None))
            replay_input = json.loads(
                (root / "bundle" / "replay-input.json").read_text(encoding="utf-8")
            )
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest["bundle_schema_version"], 5)
        self.assertEqual(manifest["run_configuration"], source_config.to_payload())
        self.assertEqual(manifest["run_configuration_digest"], source_config.digest)
        self.assertEqual(manifest["run_configuration_provenance"], "source_record")
        self.assertEqual(replay_results[0]["run_configuration"], source_config.to_payload())
        self.assertNotIn("run_configuration", replay_input[0])
        self.assertNotIn("run_configuration_digest", replay_input[0])

    def test_legacy_versioned_run_configuration_preserves_checkpoint_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            current = DeterministicRunConfig.from_agent_config(
                AgentConfig(enable_sitemap_discovery=False)
            ).to_payload()
            legacy_payload = {
                "schema_version": "1.0",
                "agent": {
                    key: value
                    for key, value in current["agent"].items()
                    if key != "max_career_discovery_transport_calls"
                },
            }
            legacy_config = DeterministicRunConfig.from_payload(legacy_payload)
            results_path = root / "results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results[0]["linkedin_job_title"] = "Missing Role"
            results[0]["run_configuration"] = legacy_payload
            results[0]["run_configuration_digest"] = legacy_config.digest
            results_path.write_text(json.dumps(results), encoding="utf-8")

            manifest = replay_failure_bundle(self._args(root, legacy_run_config=None))
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest["run_configuration"], legacy_payload)
        self.assertEqual(manifest["run_configuration_digest"], legacy_config.digest)
        self.assertEqual(replay_results[0]["run_configuration"], legacy_payload)
        self.assertEqual(
            replay_results[0]["run_configuration_digest"],
            legacy_config.digest,
        )

    def test_legacy_source_requires_explicit_configuration_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)

            with self.assertRaisesRegex(FailureReplayError, "legacy-run-config"):
                replay_failure_bundle(self._args(root, legacy_run_config=None))

    def test_reusing_bundle_output_removes_stale_checkpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            results_path = root / "results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results[0]["linkedin_job_title"] = "Missing Role"
            results_path.write_text(json.dumps(results), encoding="utf-8")
            args = self._args(root)
            replay_failure_bundle(args)
            stale = root / "bundle" / "checkpoints" / "stale.txt"
            stale.write_text("stale", encoding="utf-8")

            replay_failure_bundle(args)

            self.assertFalse(stale.exists())

    def test_replay_restores_successful_upstream_handoffs_before_first_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board_url = "https://jobs.example.test/jobs"
            current = DeterministicRunConfig.from_agent_config(
                AgentConfig(enable_sitemap_discovery=False)
            ).to_payload()
            legacy_payload = {
                "schema_version": "1.0",
                "agent": {
                    key: value
                    for key, value in current["agent"].items()
                    if key != "max_career_discovery_transport_calls"
                },
            }
            legacy_config = DeterministicRunConfig.from_payload(legacy_payload)
            results = [{
                "company_name": "Shared Name",
                "company_website_url": "https://authoritative.example.test",
                "hiring_entity_name": "Authoritative Hiring Entity",
                "career_root_url": "https://authoritative.example.test/careers",
                "career_page_url": "https://authoritative.example.test/careers",
                "job_list_page_url": board_url,
                "linkedin_job_title": "Missing Role",
                "pipeline_status": "partial",
                "run_configuration": legacy_payload,
                "run_configuration_digest": legacy_config.digest,
                "trace": {"stages": {"website_resolution": {"private": "do-not-copy"}}},
                "stages": [
                    {"stage": "linkedin_discovery", "status": "not_applicable"},
                    {"stage": "website_resolution", "status": "success"},
                    {"stage": "hiring_identity_resolution", "status": "success"},
                    {"stage": "career_discovery", "status": "success"},
                    {"stage": "job_board_discovery", "status": "success"},
                    {
                        "stage": "opening_match",
                        "status": "partial",
                        "reason_code": "OPENING_NOT_FOUND",
                    },
                    {"stage": "result_validation", "status": "success"},
                ],
            }]
            (root / "results.json").write_text(json.dumps(results), encoding="utf-8")
            SnapshotStore(root / "snapshots").write_page(
                Page(
                    url=board_url,
                    final_url=board_url,
                    html="<html><body><p>No matching role.</p></body></html>",
                    source="live",
                ),
                request_url=board_url,
            )

            with patch(
                "job_source_agent.stages.upstream.WebsiteResolutionStage.run",
                side_effect=AssertionError("website resolution must not rerun"),
            ), patch(
                "job_source_agent.stages.upstream.HiringIdentityResolutionStage.run",
                side_effect=AssertionError("entity resolution must not rerun"),
            ):
                manifest = replay_failure_bundle(self._args(root))
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )
            replay_trace = json.loads(
                (root / "bundle" / "replay-trace.json").read_text(encoding="utf-8")
            )
            checkpoint_text = "".join(
                path.read_text(encoding="utf-8")
                for path in (root / "bundle" / "checkpoints").rglob("*.json")
            )

        self.assertEqual(
            replay_results[0]["company_website_url"],
            "https://authoritative.example.test",
        )
        self.assertEqual(
            replay_results[0]["hiring_entity_name"],
            "Authoritative Hiring Entity",
        )
        self.assertEqual(
            [
                event["stage"]
                for event in replay_trace[0]["trace"]["checkpoint_events"]
                if event["action"] == "restore"
            ],
            [
                "linkedin_discovery",
                "website_resolution",
                "hiring_identity_resolution",
                "career_discovery",
                "job_board_discovery",
            ],
        )
        self.assertEqual(manifest["summary"]["checkpoint_action_counts"]["save"], 2)
        self.assertEqual(manifest["summary"]["checkpoint_action_counts"]["restore"], 5)
        self.assertEqual(manifest["run_configuration"], legacy_payload)
        self.assertEqual(manifest["run_configuration_digest"], legacy_config.digest)
        self.assertEqual(replay_results[0]["run_configuration"], legacy_payload)
        self.assertEqual(
            replay_results[0]["run_configuration_digest"],
            legacy_config.digest,
        )
        self.assertNotIn("do-not-copy", checkpoint_text)

    def test_results_only_page_aware_provider_reruns_job_board_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board_url = "https://careers.example.com/global/en/search-results"
            search_url = board_url + "?keywords=Missing+Role"

            def phenom_html(*, total_hits: int) -> str:
                config = {
                    "cdnUrl": "https://cdn.phenompeople.com/CareerConnectResources",
                    "pageName": "search-results",
                    "refNum": "ACMEGLOBAL",
                    "baseUrl": "https://careers.example.com/global/en/",
                }
                ddo = {
                    "eagerLoadRefineSearch": {
                        "hits": 0,
                        "totalHits": total_hits,
                        "data": {"jobs": []},
                    }
                }
                return (
                    "<html><body><script>"
                    f"var phApp = {json.dumps(config)};"
                    f"phApp.ddo = {json.dumps(ddo)};"
                    "</script></body></html>"
                )

            results = [{
                "company_name": "Page Aware Example",
                "company_website_url": "https://example.com",
                "career_root_url": board_url,
                "career_page_url": board_url,
                "job_list_page_url": board_url,
                "linkedin_job_title": "Missing Role",
                "pipeline_status": "partial",
                "stages": [
                    {"stage": "linkedin_discovery", "status": "not_applicable"},
                    {"stage": "website_resolution", "status": "success"},
                    {"stage": "hiring_identity_resolution", "status": "success"},
                    {"stage": "career_discovery", "status": "success"},
                    {
                        "stage": "job_board_discovery",
                        "status": "success",
                        "provider": "phenom",
                    },
                    {
                        "stage": "opening_match",
                        "status": "partial",
                        "reason_code": "OPENING_NOT_FOUND",
                    },
                    {"stage": "result_validation", "status": "success"},
                ],
            }]
            (root / "results.json").write_text(json.dumps(results), encoding="utf-8")
            snapshots = SnapshotStore(root / "snapshots")
            snapshots.write_page(
                Page(url=board_url, html=phenom_html(total_hits=0), source="live"),
                request_url=board_url,
            )
            snapshots.write_page(
                Page(url=search_url, html=phenom_html(total_hits=0), source="live"),
                request_url=search_url,
            )

            manifest = replay_failure_bundle(self._args(root))
            replay_input = json.loads(
                (root / "bundle" / "replay-input.json").read_text(encoding="utf-8")
            )
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )
            replay_trace = json.loads(
                (root / "bundle" / "replay-trace.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest["outcome_gate"]["status"], "passed")
        self.assertEqual(
            manifest["outcome_gate"]["records"][0]["classification"],
            "reproduced",
        )
        self.assertEqual(replay_results[0]["pipeline_status"], "partial")
        self.assertEqual(
            next(
                stage["reason_code"]
                for stage in replay_results[0]["stages"]
                if stage["stage"] == "opening_match"
            ),
            "OPENING_NOT_FOUND",
        )
        self.assertNotIn("OFFLINE_FIXTURE_MISSING", json.dumps(replay_results))
        self.assertNotIn("provider_detection", json.dumps(replay_input))
        self.assertEqual(
            replay_trace[0]["trace"]["stages"]["opening_match"]["provider_api"]
            ["provider_detection"],
            {
                "method": "typed_stage_handoff",
                "source_method": "page_evidence",
                "provider": "phenom",
                "url": board_url,
                "evidence_url": board_url,
            },
        )
        checkpoint_events = replay_trace[0]["trace"]["checkpoint_events"]
        self.assertIn(
            {"stage": "job_board_discovery", "action": "save"},
            [
                {"stage": event["stage"], "action": event["action"]}
                for event in checkpoint_events
            ],
        )
        self.assertNotIn(
            {"stage": "job_board_discovery", "action": "restore"},
            [
                {"stage": event["stage"], "action": event["action"]}
                for event in checkpoint_events
            ],
        )

    def test_trace_page_derived_methods_resume_at_job_board_discovery(self):
        for method in ("page_evidence", "page_probe"):
            with self.subTest(method=method):
                source_record = {
                    "trace": {"stages": {"job_board_discovery": {
                        "provider_detection": {"method": method},
                    }}},
                }

                self.assertEqual(
                    _replay_resume_stage(source_record, "opening_match"),
                    "job_board_discovery",
                )

    def test_results_only_url_native_provider_resumes_at_opening_match(self):
        source_record = {
            "job_list_page_url": "https://boards.greenhouse.io/example/jobs/123",
            "stages": [{
                "stage": "job_board_discovery",
                "status": "success",
                "provider": "greenhouse",
            }],
        }

        self.assertEqual(
            _replay_resume_stage(source_record, "opening_match"),
            "opening_match",
        )

    def test_results_fallback_fails_closed_for_invalid_provider_data(self):
        records = (
            {
                "job_list_page_url": "https://careers.example.com/search-results",
                "stages": [{"stage": "job_board_discovery", "provider": "unknown"}],
            },
            {
                "job_list_page_url": "https://careers.example.com/search-results",
                "stages": [{"stage": "job_board_discovery"}],
            },
            {
                "job_list_page_url": {"url": "https://careers.example.com"},
                "stages": [{"stage": "job_board_discovery", "provider": "phenom"}],
            },
            {
                "job_list_page_url": "not-a-url",
                "stages": [{"stage": "job_board_discovery", "provider": "phenom"}],
            },
        )

        for source_record in records:
            with self.subTest(source_record=source_record):
                self.assertEqual(
                    _replay_resume_stage(source_record, "opening_match"),
                    "opening_match",
                )

    def test_explicit_trace_method_does_not_use_results_fallback(self):
        source_record = {
            "job_list_page_url": "https://careers.example.com/search-results",
            "stages": [{"stage": "job_board_discovery", "provider": "phenom"}],
            "trace": {"stages": {"job_board_discovery": {
                "provider_detection": {"method": "linked_url_evidence"},
            }}},
        }

        self.assertEqual(
            _replay_resume_stage(source_record, "opening_match"),
            "opening_match",
        )

    def test_non_opening_failure_keeps_original_resume_stage(self):
        source_record = {
            "job_list_page_url": "https://careers.example.com/search-results",
            "stages": [{"stage": "job_board_discovery", "provider": "phenom"}],
        }

        self.assertEqual(
            _replay_resume_stage(source_record, "job_board_discovery"),
            "job_board_discovery",
        )

    def test_improved_replay_is_mismatch_and_cli_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            args = self._args(root)

            manifest = replay_failure_bundle(args)
            written = json.loads(
                (root / "bundle" / "bundle-manifest.json").read_text(encoding="utf-8")
            )
            cli_args = [
                "--results", args.results,
                "--snapshot-dir", args.snapshot_dir,
                "--output-dir", str(root / "cli-bundle"),
                "--pipeline-status", "partial",
                "--stage", "opening_match",
                "--stage-status", "partial",
                "--reason-code", "OPENING_NOT_FOUND",
                "--legacy-run-config", "composition-defaults",
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "1 outcome mismatch"):
                    main(cli_args)

        self.assertEqual(manifest, written)
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["outcome_gate"]["status"], "failed")
        comparison = manifest["outcome_gate"]["records"][0]
        self.assertEqual(comparison["classification"], "mismatch")
        self.assertEqual(comparison["reason"], "outcome_changed")
        self.assertEqual(comparison["replay_outcome"]["pipeline_status"], "success")

    def test_offline_fixture_failure_is_classified_as_fixture_gap(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "job_title": "Data Analyst",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "linkedin_job_title": "Data Analyst",
            "pipeline_status": "failed",
            "stages": [{
                "stage": "opening_match",
                "status": "failed",
                "reason_code": "OFFLINE_FIXTURE_MISSING",
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "incomplete")
        self.assertEqual(
            gate["classification_counts"],
            {
                "reproduced": 0,
                "expected_transition": 0,
                "budget_recovery": 0,
                "fixture_gap": 1,
                "mismatch": 0,
            },
        )
        self.assertEqual(gate["records"][0]["classification"], "fixture_gap")
        self.assertEqual(gate["records"][0]["reason"], "offline_fixture_missing")

    def test_nested_offline_fixture_reason_in_result_or_trace_is_fixture_gap(self):
        replay_inputs = [{
            "company_name": "Adobe",
            "source_trace": {"replay": {
                "pipeline_status": "failed",
                "first_non_success_stage": {
                    "stage": "career_discovery",
                    "status": "failed",
                    "reason_code": "CAREER_PAGE_NOT_FOUND",
                },
            }},
        }]
        replay_results = [{
            "company_name": "Adobe",
            "pipeline_status": "failed",
            "stages": [{
                "stage": "career_discovery",
                "status": "failed",
                "reason_code": "CAREER_PAGE_NOT_FOUND",
            }],
        }]
        nested_fixture_reason = {
            "trace": {
                "attempts": [[{
                    "reason_code": "OFFLINE_FIXTURE_MISSING",
                }]],
            },
        }

        for location in ("result", "trace"):
            with self.subTest(location=location):
                result = {
                    **replay_results[0],
                    **(nested_fixture_reason if location == "result" else {}),
                }
                traces = [nested_fixture_reason] if location == "trace" else None
                gate = _build_outcome_gate(
                    replay_inputs,
                    [result],
                    trace_records=traces,
                )

                self.assertEqual(gate["status"], "incomplete")
                self.assertEqual(gate["classification_counts"]["fixture_gap"], 1)
                self.assertEqual(gate["classification_counts"]["reproduced"], 0)
                self.assertEqual(gate["records"][0]["classification"], "fixture_gap")

    def test_equal_success_identity_ignores_unused_fixture_probe_gap(self):
        source = {
            "company_name": "Example Streaming",
            "company_website_url": "https://example.test",
            "career_page_url": "https://jobs.example.test",
            "job_list_page_url": "https://jobs.example.test/careers",
            "open_position_url": "https://jobs.example.test/careers/job/123",
            "pipeline_status": "success",
            "stages": [
                {"stage": stage, "status": "success"}
                for stage in PIPELINE_STAGES
            ],
        }
        replay_trace = {
            "trace": {
                "unused_probe": {
                    "reason_code": "OFFLINE_FIXTURE_MISSING",
                },
            },
        }

        gate = _build_outcome_gate(
            [{"company_name": "Example Streaming"}],
            [source],
            trace_records=[replay_trace],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["reproduced"], 1)
        self.assertEqual(gate["classification_counts"]["fixture_gap"], 0)

    def test_success_identity_drift_does_not_hide_fixture_gap(self):
        source = {
            "company_name": "Example Streaming",
            "company_website_url": "https://example.test",
            "career_page_url": "https://jobs.example.test",
            "job_list_page_url": "https://jobs.example.test/careers",
            "open_position_url": "https://jobs.example.test/careers/job/123",
            "pipeline_status": "success",
            "stages": [
                {"stage": stage, "status": "success"}
                for stage in PIPELINE_STAGES
            ],
        }
        replayed = {
            **source,
            "career_page_url": "https://jobs.example.test/search",
        }
        replay_trace = {
            "trace": {
                "probe": {
                    "reason_code": "OFFLINE_FIXTURE_MISSING",
                },
            },
        }

        gate = _build_outcome_gate(
            [{"company_name": "Example Streaming"}],
            [replayed],
            trace_records=[replay_trace],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "incomplete")
        self.assertEqual(gate["classification_counts"]["fixture_gap"], 1)

    def test_provider_declared_board_routes_have_equal_replay_identity(self):
        stages = [
            {
                "stage": stage,
                "status": "success",
                **(
                    {"provider": "google_careers"}
                    if stage in {"job_board_discovery", "opening_match"}
                    else {}
                ),
            }
            for stage in PIPELINE_STAGES
        ]
        source = {
            "company_name": "Example Search",
            "company_website_url": "https://www.google.com",
            "career_page_url": "https://www.google.com/about/careers/applications/",
            "job_list_page_url": "https://www.google.com/about/careers/applications/",
            "open_position_url": (
                "https://www.google.com/about/careers/applications/jobs/results/"
                "123-product-manager"
            ),
            "pipeline_status": "success",
            "stages": stages,
        }
        replayed = {
            **source,
            "career_page_url": (
                "https://www.google.com/about/careers/applications/jobs/results/"
            ),
            "job_list_page_url": (
                "https://www.google.com/about/careers/applications/jobs/results/"
            ),
        }

        gate = _build_outcome_gate(
            [{"company_name": "Example Search"}],
            [replayed],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["reproduced"], 1)

    def test_offline_fixture_text_without_typed_reason_is_not_fixture_gap(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "source_trace": {"replay": {
                "pipeline_status": "failed",
                "first_non_success_stage": {
                    "stage": "career_discovery",
                    "status": "failed",
                    "reason_code": "CAREER_PAGE_NOT_FOUND",
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "pipeline_status": "failed",
            "stages": [{
                "stage": "career_discovery",
                "status": "failed",
                "reason_code": "CAREER_PAGE_NOT_FOUND",
            }],
        }]
        replay_traces = [{
            "trace": {
                "error": "OFFLINE_FIXTURE_MISSING: no fixture found",
                "detail": ["reason_code=OFFLINE_FIXTURE_MISSING"],
            },
        }]

        gate = _build_outcome_gate(
            replay_inputs,
            replay_results,
            trace_records=replay_traces,
        )

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["fixture_gap"], 0)
        self.assertEqual(gate["classification_counts"]["reproduced"], 1)

    def test_cli_exits_nonzero_for_fixture_gap(self):
        manifest = {
            "summary": {"total": 1},
            "outcome_gate": {
                "status": "incomplete",
                "classification_counts": {"mismatch": 0, "fixture_gap": 1},
            },
        }
        cli_args = [
            "--results", "results.json",
            "--snapshot-dir", "snapshots",
            "--output-dir", "bundle",
        ]
        with patch(
            "scripts.replay_failure_bundle.replay_failure_bundle",
            return_value=manifest,
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "1 fixture gap"):
                    main(cli_args)

    def test_explicit_expected_transition_is_the_only_allowed_outcome_change(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                "expected_transition": {
                    "pipeline_status": "success",
                    "failure_stage": {
                        "stage": "opening_match",
                        "status": "success",
                        "reason_code": None,
                    },
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "pipeline_status": "success",
            "stages": [{
                "stage": "opening_match",
                "status": "success",
                "reason_code": None,
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["expected_transition"], 1)
        self.assertEqual(gate["records"][0]["classification"], "expected_transition")

    def test_expected_transition_can_move_to_a_different_failure_stage(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                "expected_transition": {
                    "pipeline_status": "failed",
                    "failure_stage": {
                        "stage": "career_discovery",
                        "status": "failed",
                        "reason_code": "CAREER_PAGE_NOT_FOUND",
                    },
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "pipeline_status": "failed",
            "stages": [{
                "stage": "career_discovery",
                "status": "failed",
                "reason_code": "CAREER_PAGE_NOT_FOUND",
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["expected_transition"], 1)

    def test_expected_transition_can_remove_the_failure_stage(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                "expected_transition": {
                    "pipeline_status": "success",
                    "failure_stage": None,
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "pipeline_status": "success",
            "stages": [{
                "stage": "opening_match",
                "status": "success",
                "reason_code": None,
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["records"][0]["replay_outcome"]["failure_stage"], None)

    def test_fixture_gap_cannot_be_declared_as_an_expected_transition(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                "expected_transition": {
                    "pipeline_status": "partial",
                    "failure_stage": {
                        "stage": "opening_match",
                        "status": "partial",
                        "reason_code": "OFFLINE_FIXTURE_MISSING",
                    },
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "pipeline_status": "partial",
            "stages": [{
                "stage": "opening_match",
                "status": "partial",
                "reason_code": "OFFLINE_FIXTURE_MISSING",
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "incomplete")
        self.assertEqual(gate["records"][0]["classification"], "fixture_gap")

    def test_company_budget_timeout_can_replay_to_later_structured_outcome(self):
        source = {
            "company_name": "Budget Example",
            "company_website_url": "https://budget.example",
            "career_root_url": "https://budget.example/career-root",
            "pipeline_status": "failed",
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {
                    "stage": "career_discovery",
                    "status": "failed",
                    "reason_code": "COMPANY_TIME_BUDGET_EXHAUSTED",
                },
                {"stage": "job_board_discovery", "status": "not_run"},
                {"stage": "opening_match", "status": "not_run"},
                {"stage": "result_validation", "status": "success"},
            ],
        }
        replayed = {
            **source,
            "pipeline_status": "partial",
            "career_page_url": "https://budget.example/careers",
            "job_list_page_url": "https://jobs.example.test/budget",
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {"stage": "career_discovery", "status": "success"},
                {
                    "stage": "job_board_discovery",
                    "status": "success",
                    "provider": "lever",
                },
                {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                {"stage": "result_validation", "status": "success"},
            ],
        }

        gate = _build_outcome_gate(
            [{"company_name": "Budget Example"}],
            [replayed],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["budget_recovery"], 1)
        comparison = gate["records"][0]
        self.assertEqual(comparison["classification"], "budget_recovery")
        self.assertEqual(comparison["reason"], "company_budget_replay_advanced")
        self.assertEqual(
            comparison["source_identity_prefix"],
            {
                "company_website_url": "https://budget.example",
                "hiring_entity_name": None,
                "career_root_url": "https://budget.example/career-root",
            },
        )
        self.assertEqual(
            comparison["replay_outcome"]["failure_stage"]["stage"],
            "opening_match",
        )
        self.assertEqual(
            comparison["replay_outcome"]["result_identity"]["job_list_page_url"],
            "https://jobs.example.test/budget",
        )

    def test_budget_recovery_rejects_established_identity_drift(self):
        source = {
            "company_name": "Budget Example",
            "company_website_url": "https://budget.example",
            "career_root_url": "https://budget.example/career-root",
            "pipeline_status": "failed",
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {
                    "stage": "career_discovery",
                    "status": "failed",
                    "reason_code": "COMPANY_TIME_BUDGET_EXHAUSTED",
                },
            ],
        }
        replayed = {
            **source,
            "career_root_url": "https://wrong.example/careers",
            "pipeline_status": "success",
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {"stage": "career_discovery", "status": "success"},
            ],
        }

        gate = _build_outcome_gate(
            [{"company_name": "Budget Example"}],
            [replayed],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["classification_counts"]["mismatch"], 1)

    def test_expected_transition_rejects_established_provider_identity_drift(self):
        replay_input = {
            "company_name": "Transition Example",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                "expected_transition": {
                    "pipeline_status": "success",
                    "failure_stage": None,
                },
            }},
        }
        source = {
            "company_name": "Transition Example",
            "company_website_url": "https://transition.example",
            "career_page_url": "https://transition.example/careers",
            "job_list_page_url": "https://jobs.example.test/transition",
            "pipeline_status": "partial",
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {"stage": "career_discovery", "status": "success"},
                {
                    "stage": "job_board_discovery",
                    "status": "success",
                    "provider": "greenhouse",
                },
                {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
            ],
        }
        replayed = {
            **source,
            "pipeline_status": "success",
            "stages": [
                *source["stages"][:4],
                {
                    "stage": "job_board_discovery",
                    "status": "success",
                    "provider": "lever",
                },
                {"stage": "opening_match", "status": "success"},
            ],
        }

        gate = _build_outcome_gate(
            [replay_input],
            [replayed],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["classification_counts"]["mismatch"], 1)

    def test_replay_preserves_linkedin_native_only_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            career_url = "https://native.example/careers"
            job_url = "https://www.linkedin.com/jobs/view/808"
            results = [{
                "company_name": "Native Apply",
                "company_website_url": "https://native.example",
                "career_root_url": career_url,
                "linkedin_job_url": job_url,
                "linkedin_job_title": "AI Engineer",
                "pipeline_status": "partial",
                "stages": [{
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                }],
                "trace": {"source_trace": {"linkedin_posting": {
                    "availability": "active",
                    "apply_mode": "linkedin_native",
                    "evidence_source": "authenticated_detail_dom",
                    "job_url": job_url,
                    "observed_at": "2026-07-14T00:00:00Z",
                }}},
            }]
            (root / "results.json").write_text(json.dumps(results), encoding="utf-8")
            SnapshotStore(root / "snapshots").write_page(
                Page(
                    url=career_url,
                    final_url=career_url,
                    html=(
                        "<html><head><title>Careers - Native Apply</title></head>"
                        "<body><h1>Careers</h1>"
                        "<p>Join our team. Explore career opportunities.</p>"
                        "</body></html>"
                    ),
                    source="live",
                ),
                request_url=career_url,
            )

            replay_failure_bundle(self._args(root))
            replay_input = json.loads(
                (root / "bundle" / "replay-input.json").read_text(encoding="utf-8")
            )
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        job_board_stage = next(
            stage for stage in replay_results[0]["stages"]
            if stage["stage"] == "job_board_discovery"
        )
        self.assertEqual(job_board_stage["reason_code"], "LINKEDIN_NATIVE_ONLY")
        self.assertEqual(
            replay_input[0]["source_trace"]["linkedin_posting"]["apply_mode"],
            "linkedin_native",
        )
        self.assertNotIn("observed_at", replay_input[0]["source_trace"]["linkedin_posting"])

    def test_replay_preserves_explicitly_closed_posting_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            results_path = root / "results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results[0]["linkedin_job_title"] = "Missing Role"
            results[0]["trace"] = {
                "source_trace": {
                    "linkedin_posting": {
                        "availability": "closed",
                        "apply_mode": "unknown",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": "https://www.linkedin.com/jobs/view/909",
                    }
                }
            }
            results_path.write_text(json.dumps(results), encoding="utf-8")

            replay_failure_bundle(self._args(root))
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        opening_stage = next(
            stage for stage in replay_results[0]["stages"]
            if stage["stage"] == "opening_match"
        )
        self.assertEqual(opening_stage["reason_code"], "OPENING_CLOSED")
        self.assertEqual(
            opening_stage["evidence"][0]["source_posting_status"],
            "closed",
        )

    def test_successful_replay_with_changed_url_or_provider_is_mismatch(self):
        replay_input = [{"company_name": "Example"}]
        source = {
            "company_name": "Example",
            "pipeline_status": "success",
            "company_website_url": "https://example.test",
            "hiring_entity_name": "Example Holdings",
            "career_page_url": "https://example.test/careers",
            "job_list_page_url": "https://jobs.example.test/openings",
            "open_position_url": "https://jobs.example.test/openings/123",
            "stages": [{"stage": "job_board_discovery", "status": "success", "provider": "greenhouse"}],
        }

        for changed in (
            {**source, "open_position_url": "https://jobs.example.test/openings/456"},
            {
                **source,
                "stages": [{"stage": "job_board_discovery", "status": "success", "provider": "lever"}],
            },
        ):
            with self.subTest(changed=changed):
                gate = _build_outcome_gate(
                    replay_input,
                    [changed],
                    source_records=[source],
                )

                self.assertEqual(gate["status"], "failed")
                self.assertEqual(gate["classification_counts"]["mismatch"], 1)

    def test_canonical_trailing_slash_is_equal_for_successful_replay(self):
        replay_input = [{"company_name": "Example"}]
        source = {
            "company_name": "Example",
            "pipeline_status": "success",
            "company_website_url": "https://example.test/",
            "hiring_entity_name": " Example   Holdings ",
            "career_page_url": "https://example.test/careers/",
            "job_list_page_url": "https://jobs.example.test/openings/",
            "open_position_url": "https://jobs.example.test/openings/123/",
            "stages": [{"stage": "job_board_discovery", "status": "success", "provider": "greenhouse"}],
        }
        replayed = {
            **source,
            "company_website_url": "https://example.test",
            "hiring_entity_name": "example holdings",
            "career_page_url": "https://example.test/careers",
            "job_list_page_url": "https://jobs.example.test/openings",
            "open_position_url": "https://jobs.example.test/openings/123",
        }

        gate = _build_outcome_gate(
            replay_input,
            [replayed],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["reproduced"], 1)

    def test_verified_job_list_partial_pipeline_still_uses_identity_gate(self):
        source = {
            "company_name": "Example",
            "status": "success",
            "pipeline_status": "partial",
            "company_website_url": "https://example.test",
            "career_page_url": "https://example.test/careers",
            "job_list_page_url": "https://jobs.example.test/openings",
            "open_position_url": None,
            "stages": [
                {"stage": "opening_match", "status": "partial", "reason_code": "OPENING_NOT_FOUND"}
            ],
        }
        replayed = {**source, "job_list_page_url": "https://wrong.example/jobs"}

        gate = _build_outcome_gate(
            [{"company_name": "Example"}],
            [replayed],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["classification_counts"]["mismatch"], 1)

    def test_rejects_empty_filter_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)

            with self.assertRaisesRegex(FailureReplayError, "No replayable records"):
                replay_failure_bundle(
                    self._args(root, reason_code=["NETWORK_TIMEOUT"])
                )

    def test_full_outcome_integrity_blocks_one_missing_selected_record(self):
        args = SimpleNamespace(
            pipeline_status=None,
            stage=None,
            stage_status=None,
            reason_code=None,
            provider=None,
            limit=30,
        )
        integrity = _build_record_integrity(
            args,
            {
                "source_result_count": 30,
                "filter_matched_count": 30,
                "selected_count": 29,
                "export_attempted_count": 30,
                "exported_count": 29,
                "replayability_dropped_count": 1,
                "limit_omitted_count": 0,
            },
            result_count=29,
            trace_count=29,
            comparison_count=29,
        )

        self.assertEqual(integrity["status"], "failed")
        self.assertTrue(integrity["full_coverage_required"])
        self.assertEqual(integrity["counts"]["source_result_count"], 30)
        self.assertEqual(integrity["counts"]["comparison_count"], 29)
        self.assertEqual(
            {reason["code"] for reason in integrity["reasons"]},
            {
                "selection_count_mismatch",
                "export_count_mismatch",
                "result_count_mismatch",
                "trace_count_mismatch",
                "comparison_count_mismatch",
                "replayability_records_dropped",
            },
        )

    def test_full_outcome_integrity_passes_with_complete_counts(self):
        args = SimpleNamespace(
            pipeline_status=None,
            stage=None,
            stage_status=None,
            reason_code=None,
            provider=None,
            limit=None,
        )
        integrity = _build_record_integrity(
            args,
            {
                "source_result_count": 30,
                "filter_matched_count": 30,
                "selected_count": 30,
                "export_attempted_count": 30,
                "exported_count": 30,
                "replayability_dropped_count": 0,
                "limit_omitted_count": 0,
            },
            result_count=30,
            trace_count=30,
            comparison_count=30,
        )

        self.assertEqual(integrity["status"], "passed")
        self.assertTrue(integrity["full_coverage_required"])
        self.assertEqual(integrity["reasons"], [])

    def test_explicit_filter_or_small_limit_does_not_require_full_coverage(self):
        base_counts = {
            "source_result_count": 30,
            "filter_matched_count": 10,
            "selected_count": 10,
            "export_attempted_count": 9,
            "exported_count": 9,
            "replayability_dropped_count": 0,
            "limit_omitted_count": 1,
        }
        explicit_filter = _build_record_integrity(
            SimpleNamespace(
                pipeline_status=["failed"],
                stage=None,
                stage_status=None,
                reason_code=None,
                provider=None,
                limit=None,
            ),
            base_counts,
            result_count=9,
            trace_count=9,
            comparison_count=9,
        )
        small_limit = _build_record_integrity(
            SimpleNamespace(
                pipeline_status=None,
                stage=None,
                stage_status=None,
                reason_code=None,
                provider=None,
                limit=9,
            ),
            base_counts,
            result_count=9,
            trace_count=9,
            comparison_count=9,
        )

        self.assertEqual(explicit_filter["status"], "passed")
        self.assertFalse(explicit_filter["full_coverage_required"])
        self.assertEqual(
            explicit_filter["reasons"], [{"code": "explicit_failure_filters"}]
        )
        self.assertEqual(small_limit["status"], "passed")
        self.assertFalse(small_limit["full_coverage_required"])
        self.assertEqual(
            small_limit["reasons"][0]["code"], "limit_below_source_count"
        )

    def test_export_counts_replayability_drop_across_thirty_source_results(self):
        records = [
            {
                "company_name": f"Company {index}",
                "company_website_url": (
                    "" if index == 29 else f"https://company-{index}.example"
                ),
                "pipeline_status": "success",
            }
            for index in range(30)
        ]
        export_args = SimpleNamespace(
            input="results.json",
            pipeline_status=None,
            stage=None,
            stage_status=None,
            reason_code=None,
            provider=None,
            limit=30,
            include_missing_website=False,
        )

        replay_records, source_records, counts = _export_replay_records_with_sources(
            records,
            export_args,
        )

        self.assertEqual(len(replay_records), 29)
        self.assertEqual(len(source_records), 29)
        self.assertEqual(
            counts,
            {
                "source_result_count": 30,
                "filter_matched_count": 30,
                "selected_count": 29,
                "export_attempted_count": 30,
                "exported_count": 29,
                "replayability_dropped_count": 1,
                "limit_omitted_count": 0,
            },
        )

    def test_full_outcome_bundle_fails_closed_before_replaying_thirty_as_twenty_nine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = [
                {
                    "company_name": f"Company {index}",
                    "company_website_url": (
                        "" if index == 29 else f"https://company-{index}.example"
                    ),
                    "pipeline_status": "success",
                }
                for index in range(30)
            ]
            (root / "results.json").write_text(
                json.dumps(records),
                encoding="utf-8",
            )
            args = self._args(
                root,
                pipeline_status=None,
                stage=None,
                stage_status=None,
                reason_code=None,
                limit=30,
            )

            manifest = replay_failure_bundle(args)
            written = json.loads(
                (root / "bundle" / "bundle-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            cli_args = [
                "--results", str(root / "results.json"),
                "--snapshot-dir", str(root / "snapshots"),
                "--output-dir", str(root / "cli-bundle"),
                "--limit", "30",
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "record integrity failed"):
                    main(cli_args)

        self.assertEqual(manifest, written)
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["reason"], "record_integrity_failed")
        self.assertEqual(manifest["outcome_gate"]["status"], "failed")
        integrity = manifest["record_integrity"]
        self.assertEqual(integrity["status"], "failed")
        self.assertEqual(integrity["counts"]["source_result_count"], 30)
        self.assertEqual(integrity["counts"]["selected_count"], 29)
        self.assertEqual(integrity["counts"]["exported_count"], 29)
        self.assertEqual(integrity["counts"]["comparison_count"], 0)
        self.assertIn(
            "replayability_records_dropped",
            {reason["code"] for reason in integrity["reasons"]},
        )
        self.assertFalse((root / "bundle" / "replay-input.json").exists())

    def test_allow_empty_writes_skipped_manifest_without_requiring_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            args = self._args(root, reason_code=["NETWORK_TIMEOUT"])

            manifest = replay_failure_bundle(args, allow_empty=True)
            written = json.loads(
                (root / "bundle" / "bundle-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest, written)
        self.assertEqual(manifest["status"], "skipped")
        self.assertEqual(manifest["reason"], "no_replayable_failure_records")
        self.assertEqual(manifest["summary"], {"total": 0})
        self.assertEqual(manifest["outcome_gate"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
