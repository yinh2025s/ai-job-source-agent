import unittest

from job_source_agent.runtime import inspect_runtime


class RuntimePolicyTests(unittest.TestCase):
    def test_cpython_312_is_release_compatible(self):
        status = inspect_runtime((3, 12, 6), "CPython")

        self.assertTrue(status.supported)
        self.assertTrue(status.release_compatible)

    def test_cpython_313_is_supported_but_not_release_baseline(self):
        status = inspect_runtime((3, 13, 5), "CPython")

        self.assertTrue(status.supported)
        self.assertFalse(status.release_compatible)

    def test_cpython_314_is_rejected_until_validated(self):
        status = inspect_runtime((3, 14, 2), "CPython")

        self.assertFalse(status.supported)
        self.assertFalse(status.release_compatible)

    def test_non_cpython_runtime_is_rejected(self):
        status = inspect_runtime((3, 12, 6), "PyPy")

        self.assertFalse(status.supported)
        self.assertIn("CPython", status.detail)


if __name__ == "__main__":
    unittest.main()
