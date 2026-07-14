import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from job_source_agent.career_transport_budget import CareerTransportBudgetFetcher
from job_source_agent.composition import build_application as production_build_application
from job_source_agent.models import PIPELINE_STAGES
from job_source_agent.page_cache import PageCacheFetcher
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import Fetcher
from scripts import benchmark_eval
from scripts.benchmark_eval import print_summary


ROOT = Path(__file__).resolve().parents[1]


class BenchmarkEvalTests(unittest.TestCase):
    def test_fixed_benchmark_uses_offline_production_application(self):
        applications = []

        def recording_build_application(*args, **kwargs):
            application = production_build_application(*args, **kwargs)
            applications.append(application)
            return application

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            results_path = output_dir / "results.json"
            trace_path = output_dir / "trace.json"
            summary_path = output_dir / "summary.json"
            argv = [
                "benchmark_eval.py",
                "--output",
                str(results_path),
                "--trace-output",
                str(trace_path),
                "--summary-output",
                str(summary_path),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(
                    benchmark_eval,
                    "build_application",
                    side_effect=recording_build_application,
                ) as build_mock,
                patch.object(
                    JobSourceAgent,
                    "discover",
                    side_effect=AssertionError("legacy discover boundary used"),
                ),
                redirect_stdout(io.StringIO()),
            ):
                benchmark_eval.main()

            results = json.loads(results_path.read_text(encoding="utf-8"))
            traces = json.loads(trace_path.read_text(encoding="utf-8"))
            summary = json.loads(summary_path.read_text(encoding="utf-8"))

        build_mock.assert_called_once()
        self.assertEqual(len(applications), 1)
        application = applications[0]
        fetcher_config = build_mock.call_args.args[0]
        self.assertTrue(fetcher_config.offline)
        self.assertEqual(Path(fetcher_config.fixtures_dir), ROOT / "samples" / "sites")
        self.assertIsInstance(application.fetcher, PageCacheFetcher)
        self.assertIsInstance(application.fetcher.fetcher, CareerTransportBudgetFetcher)
        self.assertIsInstance(application.fetcher.fetcher.fetcher, Fetcher)
        self.assertTrue(application.fetcher.fetcher.fetcher.offline)

        self.assertEqual(len(results), 25)
        self.assertEqual(len(traces), 25)
        self.assertEqual(summary["success"], 25)
        self.assertEqual(summary["expectation_checks"]["passed"], 25)
        self.assertEqual(summary["expectation_checks"]["failed"], 0)

        expected_configuration = application.pipeline.run_configuration.to_payload()
        expected_digest = application.pipeline.run_configuration.digest
        self.assertEqual(summary["run_configuration"], expected_configuration)
        self.assertEqual(summary["run_configuration_digest"], expected_digest)
        self.assertEqual(
            summary["evaluation_manifest"]["run_configuration_digest"],
            expected_digest,
        )
        for result, trace in zip(results, traces, strict=True):
            self.assertEqual(
                [stage["stage"] for stage in result["stages"]],
                list(PIPELINE_STAGES),
            )
            self.assertEqual(result["run_configuration"], expected_configuration)
            self.assertEqual(result["run_configuration_digest"], expected_digest)
            self.assertRegex(result["execution_fingerprint"], r"^[0-9a-f]{64}$")
            self.assertEqual(trace["run_configuration"], expected_configuration)
            self.assertEqual(trace["run_configuration_digest"], expected_digest)
            self.assertEqual(
                trace["execution_fingerprint"],
                result["execution_fingerprint"],
            )
            lineage = trace["trace"]["stage_evidence_lineage"]
            self.assertEqual(
                [stage["stage"] for stage in lineage],
                list(PIPELINE_STAGES),
            )
            self.assertTrue(
                all(
                    stage["execution_fingerprint"] == result["execution_fingerprint"]
                    for stage in lineage
                )
            )

    def test_print_summary_handles_incompatible_baseline(self):
        summary = {
            "total": 1,
            "success": 1,
            "pipeline_status_counts": {"success": 1},
            "with_job_list": 1,
            "with_opening": 1,
            "expectation_checks": {"passed": 1, "total": 1},
            "rates": {"opening": 1.0},
            "provider_counts": {"ashby": 1},
            "regression": {"comparison_status": "no_compatible_baseline"},
        }

        output = io.StringIO()
        with redirect_stdout(output):
            print_summary(summary)

        self.assertIn("baseline_comparison: no_compatible_baseline", output.getvalue())


if __name__ == "__main__":
    unittest.main()
