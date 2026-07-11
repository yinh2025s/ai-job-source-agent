import time
import unittest

from job_source_agent.process_budget import ProcessBudgetExceeded, run_with_process_budget


def echo(value):
    return value


def delayed_echo(value, delay):
    time.sleep(delay)
    return value


class ProcessBudgetTests(unittest.TestCase):
    def test_worker_returns_picklable_result(self):
        self.assertEqual(run_with_process_budget(echo, ("done",), timeout=5), "done")

    def test_worker_is_stopped_at_hard_deadline(self):
        started = time.monotonic()

        with self.assertRaises(ProcessBudgetExceeded):
            run_with_process_budget(delayed_echo, ("late", 2), timeout=0.2)

        self.assertLess(time.monotonic() - started, 1.5)


if __name__ == "__main__":
    unittest.main()
