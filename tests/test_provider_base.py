import unittest

from job_source_agent.providers.base import provider_fetch_reason
from job_source_agent.web import FetchError


class ProviderFetchReasonTests(unittest.TestCase):
    def test_preserves_typed_budget_reasons_independent_of_message(self):
        for reason in (
            "COMPANY_TIME_BUDGET_EXHAUSTED",
            "FETCH_BUDGET_EXHAUSTED",
        ):
            with self.subTest(reason=reason):
                self.assertEqual(
                    provider_fetch_reason(
                        FetchError("human-readable text changed", reason_code=reason)
                    ),
                    reason,
                )

    def test_uses_string_taxonomy_only_without_typed_reason(self):
        self.assertEqual(
            provider_fetch_reason(FetchError("read operation timed out")),
            "NETWORK_TIMEOUT",
        )
        self.assertEqual(
            provider_fetch_reason(FetchError("unclassified provider transport")),
            "PROVIDER_FETCH_FAILED",
        )


if __name__ == "__main__":
    unittest.main()
