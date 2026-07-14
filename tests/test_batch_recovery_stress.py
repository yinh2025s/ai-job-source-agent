from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from job_source_agent.batch_checkpoint import FilesystemBatchCompletionStore
from job_source_agent.completion_resume import classify_completion_resume
from job_source_agent.run_configuration import (
    AgentConfig,
    BatchExecutionConfig,
    DeterministicRunConfig,
    combined_configuration_digest,
)


ROOT = Path(__file__).resolve().parents[1]
COMPANY_COUNT = 32
WORKERS = 6
FAILED_INDEX = 3


CHILD_PROGRAM = r"""
import os
import sys
import time

from job_source_agent.checkpoint import input_fingerprint
from job_source_agent.contracts import StageExecution
from job_source_agent.models import DiscoveryResult, StageResult, dataclass_to_dict
from job_source_agent.stage_checkpoint import FilesystemCheckpointStore
import scripts.live_batch_eval as live_batch_eval


phase = os.environ["STRESS_PHASE"]
execution_log = os.environ["STRESS_EXECUTION_LOG"]


def record_execution(event, company_name):
    line = f"{phase}\t{event}\t{company_name}\n".encode("utf-8")
    descriptor = os.open(execution_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, line)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def deterministic_worker(index, company, args):
    record_execution("start", company.company_name)
    fingerprint = input_fingerprint(dataclass_to_dict(company))
    FilesystemCheckpointStore(args.checkpoint_dir).save(
        fingerprint,
        StageExecution(
            result=StageResult(stage="linkedin_discovery", status="success"),
            updates={"company_name": company.company_name},
            trace={"stress_index": index, "phase": phase},
        ),
    )

    if index == 3:
        record_execution("finish", company.company_name)
        raise RuntimeError("injected deterministic worker failure")

    if phase == "first" and index > 12:
        delay = 5.0
    elif phase == "first":
        delay = 0.04 * (7 - (index % 6))
    else:
        delay = 0.01 * (1 + (index % 5))
    time.sleep(delay)
    record_execution("finish", company.company_name)
    result = DiscoveryResult(
        company_name=company.company_name,
        company_website_url=company.company_website_url,
        status="success",
        pipeline_status="success",
        trace={"stress_index": index, "phase": phase},
    )
    return index, result, round(delay, 2)


live_batch_eval.run_company_timed = deterministic_worker
live_batch_eval.main()
"""


class BatchRecoveryStressTests(unittest.TestCase):
    maxDiff = None

    def test_parallel_batch_survives_real_main_process_crash_and_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "companies.json"
            output_path = root / "results.json"
            trace_path = root / "trace.json"
            summary_path = root / "summary.json"
            checkpoint_dir = root / "stage-checkpoints"
            completion_dir = root / "batch-completions"
            execution_log = root / "executions.log"
            records = [
                {
                    "company_name": f"Stress Company {index:02d}",
                    "company_website_url": f"https://stress-{index:02d}.example",
                    "job_title": f"Engineer {index:02d}",
                    "source": "batch_recovery_stress",
                }
                for index in range(1, COMPANY_COUNT + 1)
            ]
            input_path.write_text(json.dumps(records), encoding="utf-8")

            first = self._start_batch(
                phase="first",
                input_path=input_path,
                output_path=output_path,
                trace_path=trace_path,
                summary_path=summary_path,
                checkpoint_dir=checkpoint_dir,
                completion_dir=completion_dir,
                execution_log=execution_log,
            )
            try:
                self._wait_for_completion_count(completion_dir, minimum=6, timeout=10)
                os.kill(first.pid, signal.SIGKILL)
                self.assertEqual(first.wait(timeout=5), -signal.SIGKILL)
            finally:
                if first.poll() is None:
                    first.kill()
                    first.wait(timeout=5)
                if first.stdout is not None:
                    first.stdout.close()
                if first.stderr is not None:
                    first.stderr.close()

            run_configuration = DeterministicRunConfig.from_agent_config(
                AgentConfig(
                    max_candidates=6,
                    max_job_pages=3,
                    max_career_candidate_fetches=5,
                    max_career_discovery_transport_calls=32,
                    max_career_search_queries=5,
                    max_ats_board_fetches=5,
                    career_search_timeout=6,
                )
            )
            batch_execution = BatchExecutionConfig.from_payload(
                {
                    "schema_version": "1.0",
                    "batch": {
                        "company_time_budget": 45,
                        "website_time_budget": 20,
                        "fetch_timeout": 3,
                        "fetch_retries": 0,
                        "retry_base_delay": 0.25,
                        "render_mode": "none",
                        "render_budget": 2,
                        "verify_limit": 3,
                        "offline": True,
                    },
                }
            )
            store = FilesystemBatchCompletionStore(
                completion_dir,
                run_configuration,
                combined_configuration_digest(
                    run_configuration.digest,
                    batch_execution.digest,
                ),
            )
            completed_after_crash = store.scan(records)
            self.assertGreaterEqual(len(completed_after_crash), 6)
            self.assertLess(len(completed_after_crash), COMPANY_COUNT)
            missing_names = {
                record["company_name"]
                for record in records
                if store.load(record) is None
            }
            retryable_names = {
                completion.result["company_name"]
                for completion in completed_after_crash.values()
                if classify_completion_resume(
                    completion.result,
                    completion.trace,
                ).action
                == "retryable_resubmit"
            }
            self.assertEqual(
                retryable_names,
                {f"Stress Company {FAILED_INDEX:02d}"},
            )

            second = self._run_batch(
                phase="second",
                input_path=input_path,
                output_path=output_path,
                trace_path=trace_path,
                summary_path=summary_path,
                checkpoint_dir=checkpoint_dir,
                completion_dir=completion_dir,
                execution_log=execution_log,
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

            executions = self._read_execution_log(execution_log)
            first_names = [
                name for phase, event, name in executions
                if phase == "first" and event == "start"
            ]
            first_finished = [
                name for phase, event, name in executions
                if phase == "first" and event == "finish"
            ]
            second_names = [
                name for phase, event, name in executions
                if phase == "second" and event == "start"
            ]
            self.assertNotEqual(first_finished, sorted(first_finished))
            expected_second_names = missing_names | retryable_names
            self.assertEqual(set(second_names), expected_second_names)
            self.assertEqual(len(second_names), len(expected_second_names))
            self.assertTrue(set(first_names) - missing_names)

            results = json.loads(output_path.read_text(encoding="utf-8"))
            traces = json.loads(trace_path.read_text(encoding="utf-8"))
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            expected_names = [record["company_name"] for record in records]
            self.assertEqual([item["company_name"] for item in results], expected_names)
            self.assertEqual([item["company_name"] for item in traces], expected_names)
            self.assertEqual(len(set(expected_names)), COMPANY_COUNT)
            self.assertEqual(summary["total"], COMPANY_COUNT)
            self.assertEqual(
                summary["batch_completion_resume"]["retryable_resubmit"],
                1,
            )

            failures = [item for item in results if item["status"] == "failed"]
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["company_name"], f"Stress Company {FAILED_INDEX:02d}")
            self.assertEqual(failures[0]["error"], "batch_worker_failed")
            self.assertEqual(
                sum(item["status"] == "success" for item in results),
                COMPANY_COUNT - 1,
            )

            final_completions = store.scan(records)
            self.assertEqual(len(final_completions), COMPANY_COUNT)
            self._assert_json_artifacts_are_intact(root)

    def _start_batch(self, **paths: Path | str) -> subprocess.Popen[str]:
        command, environment = self._batch_command(**paths)
        return subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

    def _run_batch(self, **paths: Path | str) -> subprocess.CompletedProcess[str]:
        command, environment = self._batch_command(**paths)
        return subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )

    @staticmethod
    def _batch_command(**paths: Path | str) -> tuple[list[str], dict[str, str]]:
        environment = os.environ.copy()
        environment["STRESS_PHASE"] = str(paths["phase"])
        environment["STRESS_EXECUTION_LOG"] = str(paths["execution_log"])
        command = [
            sys.executable,
            "-c",
            CHILD_PROGRAM,
            "--input",
            str(paths["input_path"]),
            "--limit",
            str(COMPANY_COUNT),
            "--offline",
            "--workers",
            str(WORKERS),
            "--checkpoint-dir",
            str(paths["checkpoint_dir"]),
            "--batch-checkpoint-dir",
            str(paths["completion_dir"]),
            "--output",
            str(paths["output_path"]),
            "--trace-output",
            str(paths["trace_path"]),
            "--summary-output",
            str(paths["summary_path"]),
        ]
        return command, environment

    @staticmethod
    def _wait_for_completion_count(directory: Path, *, minimum: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            count = len(list(directory.glob("*/*.json"))) if directory.exists() else 0
            if count >= minimum:
                return
            time.sleep(0.02)
        raise AssertionError(f"only {count} completion records became durable")

    @staticmethod
    def _read_execution_log(path: Path) -> list[tuple[str, str, str]]:
        return [
            tuple(line.split("\t", 2))
            for line in path.read_text(encoding="utf-8").splitlines()
        ]

    def _assert_json_artifacts_are_intact(self, root: Path) -> None:
        json_paths = list(root.rglob("*.json"))
        self.assertGreaterEqual(len(json_paths), COMPANY_COUNT * 2 + 3)
        for path in json_paths:
            with self.subTest(path=path.relative_to(root)):
                json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(list(root.rglob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
