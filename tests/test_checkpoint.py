import unittest

from job_source_agent.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    checkpoint_metadata,
    execution_fingerprint,
    input_fingerprint,
)
from job_source_agent.models import RESULT_SCHEMA_VERSION


class CheckpointTests(unittest.TestCase):
    def test_execution_fingerprint_is_stable_and_configuration_sensitive(self):
        record = {"company_name": "Example Corp", "job_title": "Engineer"}
        first_digest = "a" * 64

        self.assertEqual(
            execution_fingerprint(record, first_digest),
            execution_fingerprint({"job_title": "Engineer", "company_name": " Example Corp "}, first_digest),
        )
        self.assertNotEqual(
            execution_fingerprint(record, first_digest),
            execution_fingerprint(record, "b" * 64),
        )

    def test_execution_fingerprint_rejects_invalid_configuration_digest(self):
        for digest in (None, "short", "z" * 64):
            with self.subTest(digest=digest):
                with self.assertRaises(ValueError):
                    execution_fingerprint({"company_name": "Example"}, digest)  # type: ignore[arg-type]

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

    def test_source_posting_semantics_affect_fingerprint(self):
        base = {
            "company_name": "Example Robotics",
            "linkedin_job_url": "https://www.linkedin.com/jobs/view/123",
            "source_trace": {
                "linkedin_posting": {
                    "availability": "active",
                    "apply_mode": "linkedin_native",
                    "evidence_source": "authenticated_detail_dom",
                    "job_url": "https://www.linkedin.com/jobs/view/123",
                    "observed_at": "2026-07-13T00:00:00Z",
                }
            },
        }

        changed = {
            **base,
            "source_trace": {
                "linkedin_posting": {
                    **base["source_trace"]["linkedin_posting"],
                    "apply_mode": "external",
                }
            },
        }
        self.assertNotEqual(input_fingerprint(base), input_fingerprint(changed))

    def test_volatile_or_unrelated_source_trace_does_not_affect_fingerprint(self):
        base = {
            "company_name": "Example Robotics",
            "source_trace": {
                "linkedin_posting": {
                    "availability": "listed",
                    "apply_mode": "unknown",
                    "evidence_source": "public_search_card",
                    "job_url": "https://www.linkedin.com/jobs/view/123",
                    "observed_at": "2026-07-13T00:00:00Z",
                },
                "metrics": {"attempt": 1},
            },
        }
        changed = {
            **base,
            "source_trace": {
                **base["source_trace"],
                "linkedin_posting": {
                    **base["source_trace"]["linkedin_posting"],
                    "observed_at": "2026-07-14T00:00:00Z",
                },
                "metrics": {"attempt": 99},
            },
        }

        self.assertEqual(input_fingerprint(base), input_fingerprint(changed))


if __name__ == "__main__":
    unittest.main()
