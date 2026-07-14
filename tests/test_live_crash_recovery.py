from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from job_source_agent.checkpoint import execution_fingerprint
from job_source_agent.models import PIPELINE_STAGES
from job_source_agent.run_configuration import AgentConfig, DeterministicRunConfig
from job_source_agent.stage_checkpoint import FilesystemCheckpointStore


ROOT = Path(__file__).resolve().parents[1]
READY_PREFIX = "CHECKPOINT_READY:"


CHILD_PROGRAM = r"""
import json
import signal
import sys
from argparse import Namespace

from job_source_agent.models import CompanyInput
from scripts.live_batch_eval import run_pipeline_phase

input_path, checkpoint_dir, stop_after = sys.argv[1:]
record = json.loads(open(input_path, encoding="utf-8").read())[0]
args = Namespace(
    checkpoint_dir=checkpoint_dir,
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
    max_career_transport_calls=32,
    max_career_search_queries=5,
    max_ats_board_fetches=5,
    skip_sitemap=False,
    career_search_timeout=6,
)
result = run_pipeline_phase(
    CompanyInput(**record),
    args,
    None,
    stop_after,
    None,
)
completed = [
    stage.stage
    for stage in result.stage_results
    if stage.status not in {"not_run", "failed"}
]
print("CHECKPOINT_READY:" + ",".join(completed), flush=True)
signal.pause()
"""


class LiveCrashRecoveryTests(unittest.TestCase):
    maxDiff = None

    def test_sigterm_after_s4_resumes_from_s5(self):
        self._assert_signal_recovery(
            stop_after="career_discovery",
            resume_from="job_board_discovery",
            interruption_signal=signal.SIGTERM,
            expected_restored=PIPELINE_STAGES[:4],
            expected_first_saved="job_board_discovery",
        )

    def test_sigkill_after_s5_resumes_from_s6(self):
        self._assert_signal_recovery(
            stop_after="job_board_discovery",
            resume_from="opening_match",
            interruption_signal=signal.SIGKILL,
            expected_restored=PIPELINE_STAGES[:5],
            expected_first_saved="opening_match",
        )

    def _assert_signal_recovery(
        self,
        *,
        stop_after: str,
        resume_from: str,
        interruption_signal: signal.Signals,
        expected_restored: tuple[str, ...],
        expected_first_saved: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint_dir = root / "stage-checkpoints"
            input_path = root / "input.json"
            output_path = root / "results.json"
            trace_path = root / "trace.json"
            summary_path = root / "summary.json"
            record = {
                "company_name": "Aurora Data",
                "company_website_url": "https://aurora-data.example",
                "job_title": "AI Engineer",
                "source": "crash_recovery_test",
            }
            input_path.write_text(json.dumps([record]), encoding="utf-8")

            first = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    CHILD_PROGRAM,
                    str(input_path),
                    str(checkpoint_dir),
                    stop_after,
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                ready_line = self._read_ready_line(first, timeout=10)
                self.assertTrue(ready_line.startswith(READY_PREFIX), ready_line)
                completed = ready_line.removeprefix(READY_PREFIX).split(",")
                self.assertEqual(completed, list(expected_restored))

                os.kill(first.pid, interruption_signal)
                return_code = first.wait(timeout=5)
                self.assertEqual(return_code, -interruption_signal)
            finally:
                if first.poll() is None:
                    first.kill()
                    first.wait(timeout=5)
                if first.stdout is not None:
                    first.stdout.close()
                if first.stderr is not None:
                    first.stderr.close()

            second = subprocess.run(
                [
                    sys.executable,
                    "scripts/live_batch_eval.py",
                    "--input",
                    str(input_path),
                    "--limit",
                    "1",
                    "--fixtures-dir",
                    "samples/sites",
                    "--offline",
                    "--fetch-timeout",
                    "0.1",
                    "--max-career-candidates",
                    "12",
                    "--max-career-fetches",
                    "12",
                    "--max-job-pages",
                    "8",
                    "--company-time-budget",
                    "10",
                    "--website-time-budget",
                    "5",
                    "--checkpoint-dir",
                    str(checkpoint_dir),
                    "--batch-checkpoint-dir",
                    str(root / "batch-checkpoints"),
                    "--resume-from-stage",
                    resume_from,
                    "--output",
                    str(output_path),
                    "--trace-output",
                    str(trace_path),
                    "--summary-output",
                    str(summary_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

            results = json.loads(output_path.read_text(encoding="utf-8"))
            traces = json.loads(trace_path.read_text(encoding="utf-8"))
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(len(results), 1)
            self.assertEqual(len(traces), 1)
            self.assertEqual(summary["total"], 1)
            self.assertEqual(results[0]["pipeline_status"], "success")
            self.assertIn("d9d64766", results[0]["open_position_url"])
            self.assertEqual(
                [stage["stage"] for stage in results[0]["stages"]],
                list(PIPELINE_STAGES),
            )
            self.assertTrue(all(
                stage["status"] in {"success", "not_applicable"}
                for stage in results[0]["stages"]
            ))

            events = traces[0]["trace"]["checkpoint_events"]
            restored = [event["stage"] for event in events if event["action"] == "restore"]
            saved = [event["stage"] for event in events if event["action"] == "save"]
            self.assertEqual(restored, list(expected_restored))
            self.assertEqual(saved[0], expected_first_saved)
            self.assertTrue(set(restored).isdisjoint(saved))
            self.assertEqual(
                traces[0]["trace"]["source_trace"]["resume"]["effective_start_stage"],
                resume_from,
            )

            run_configuration = DeterministicRunConfig.from_agent_config(
                AgentConfig(
                    max_candidates=12,
                    max_job_pages=8,
                    max_career_candidate_fetches=12,
                    max_career_discovery_transport_calls=32,
                    max_career_search_queries=5,
                    max_ats_board_fetches=5,
                    career_search_timeout=6,
                )
            )
            fingerprint = execution_fingerprint(record, run_configuration.digest)
            store = FilesystemCheckpointStore(checkpoint_dir)
            for stage in PIPELINE_STAGES:
                execution = store.load(fingerprint, stage)
                self.assertIsNotNone(execution, stage)
                self.assertEqual(execution.result.stage, stage)
            self.assertFalse(list(checkpoint_dir.rglob("*.tmp")))
            for checkpoint_path in checkpoint_dir.rglob("*.json"):
                json.loads(checkpoint_path.read_text(encoding="utf-8"))

    def _read_ready_line(
        self,
        process: subprocess.Popen[str],
        *,
        timeout: float,
    ) -> str:
        assert process.stdout is not None
        assert process.stderr is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            readable, _, _ = select.select([process.stdout], [], [], 0.1)
            if readable:
                line = process.stdout.readline().strip()
                if line.startswith(READY_PREFIX):
                    return line
            if process.poll() is not None:
                break
        stderr = process.stderr.read()
        self.fail(
            f"child did not report a durable checkpoint boundary; "
            f"returncode={process.poll()} stderr={stderr}"
        )


if __name__ == "__main__":
    unittest.main()
