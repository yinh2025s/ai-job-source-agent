import concurrent.futures
import os
import signal
import tempfile
import time
import unittest

from job_source_agent.process_budget import (
    ProcessBudgetExceeded,
    RemoteProcessError,
    run_with_process_budget,
)


def echo(value):
    return value


def delayed_echo(value, delay):
    time.sleep(delay)
    return value


def exit_without_result(code):
    os._exit(code)


def write_pid_then_wait(path, ignore_sigterm=False):
    if ignore_sigterm:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with open(path, "w", encoding="ascii") as handle:
        handle.write(str(os.getpid()))
        handle.flush()
        os.fsync(handle.fileno())
    while True:
        time.sleep(1)


def _pid_exists(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for_pid_file(path, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(path, encoding="ascii") as handle:
                return int(handle.read())
        except (FileNotFoundError, ValueError):
            time.sleep(0.01)
    raise AssertionError("worker did not publish its pid")


class ProcessBudgetTests(unittest.TestCase):
    def test_worker_returns_picklable_result(self):
        self.assertEqual(run_with_process_budget(echo, ("done",), timeout=5), "done")

    def test_worker_is_stopped_at_hard_deadline(self):
        started = time.monotonic()

        with self.assertRaises(ProcessBudgetExceeded):
            run_with_process_budget(delayed_echo, ("late", 2), timeout=0.2)

        self.assertLess(time.monotonic() - started, 1.5)

    def test_worker_exit_without_result_is_remote_error(self):
        with self.assertRaisesRegex(RemoteProcessError, r"without a result \(exit code 17\)"):
            run_with_process_budget(exit_without_result, (17,), timeout=5)

    def test_timeout_reaps_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = os.path.join(directory, "worker.pid")
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    run_with_process_budget,
                    write_pid_then_wait,
                    (pid_path,),
                    0.5,
                )
                pid = _wait_for_pid_file(pid_path)
                with self.assertRaises(ProcessBudgetExceeded):
                    future.result(timeout=5)

            self.assertFalse(_pid_exists(pid), f"worker {pid} survived its timeout")

    def test_worker_ignoring_sigterm_is_killed_and_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = os.path.join(directory, "worker.pid")
            started = time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    run_with_process_budget,
                    write_pid_then_wait,
                    (pid_path, True),
                    0.5,
                )
                pid = _wait_for_pid_file(pid_path)
                with self.assertRaises(ProcessBudgetExceeded):
                    future.result(timeout=6)

            self.assertFalse(_pid_exists(pid), f"SIGTERM-ignoring worker {pid} survived")
            self.assertLess(time.monotonic() - started, 4.5)

    def test_concurrent_budgets_are_independent(self):
        started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(run_with_process_budget, delayed_echo, (index, 0.6), 3)
                for index in range(4)
            ]

        self.assertEqual([future.result() for future in futures], [0, 1, 2, 3])
        self.assertLess(time.monotonic() - started, 1.8)

    def test_concurrent_timeout_does_not_stop_other_budget(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            timed_out = executor.submit(
                run_with_process_budget,
                delayed_echo,
                ("late", 2),
                0.2,
            )
            completed = executor.submit(
                run_with_process_budget,
                delayed_echo,
                ("done", 0.4),
                3,
            )

            with self.assertRaises(ProcessBudgetExceeded):
                timed_out.result(timeout=3)
            self.assertEqual(completed.result(timeout=3), "done")


if __name__ == "__main__":
    unittest.main()
