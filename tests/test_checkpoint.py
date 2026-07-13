import unittest

from job_source_agent.checkpoint import CHECKPOINT_SCHEMA_VERSION, checkpoint_metadata, input_fingerprint
from job_source_agent.models import RESULT_SCHEMA_VERSION


class CheckpointTests(unittest.TestCase):
    def test_input_fingerprint_is_stable_for_equivalent_spacing(self):
        left = {
            "company_name": " Example Robotics ",
            "company_website_url": "https://example.com",
            "job_title": "AI   Engineer",
        }
        right = {
            "company_name": "Example Robotics",
            "company_website_url": "https://example.com",
            "job_title": "AI Engineer",
        }

        self.assertEqual(input_fingerprint(left), input_fingerprint(right))

    def test_input_fingerprint_changes_when_replay_input_changes(self):
        base = {
            "company_name": "Example Robotics",
            "company_website_url": "https://example.com",
            "job_title": "AI Engineer",
        }
        changed = dict(base, job_title="Product Manager")

        self.assertNotEqual(input_fingerprint(base), input_fingerprint(changed))

    def test_input_fingerprint_includes_external_apply_url(self):
        base = {"company_name": "Example Robotics", "job_title": "AI Engineer"}

        self.assertNotEqual(
            input_fingerprint(base),
            input_fingerprint({**base, "external_apply_url": "https://jobs.lever.co/example/123"}),
        )

    def test_checkpoint_metadata_records_versions_and_fingerprint(self):
        metadata = checkpoint_metadata({"company_name": "Example", "company_website_url": "https://example.com"})

        self.assertEqual(metadata["checkpoint_schema_version"], CHECKPOINT_SCHEMA_VERSION)
        self.assertEqual(metadata["result_schema_version"], RESULT_SCHEMA_VERSION)
        self.assertRegex(metadata["input_fingerprint"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
