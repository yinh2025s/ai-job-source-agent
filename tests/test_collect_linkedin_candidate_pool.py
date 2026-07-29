import json
import tempfile
import unittest
from pathlib import Path

from scripts.collect_linkedin_candidate_pool import (
    load_collection_contract,
    validate_collection_count,
)


class CollectLinkedInCandidatePoolTests(unittest.TestCase):
    def test_loads_frozen_query_contract_with_digest(self):
        contract_path = (
            Path(__file__).resolve().parents[1]
            / "samples"
            / "evaluation"
            / "diagnostic_fingerprint_candidate_collection.json"
        )

        contract, digest = load_collection_contract(contract_path)

        self.assertEqual(len(contract["queries"]), 16)
        self.assertEqual(contract["target_job_count"], 480)
        self.assertEqual(contract["minimum_job_count"], 320)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(
            {
                query["lane"] for query in contract["queries"]
            },
            {
                "cws_effective_configuration",
                "repeated_static_cards",
                "safe_fragment_pagination",
                "multi_field_get_title_search",
            },
        )

    def test_rejects_query_contract_with_duplicate_keyword(self):
        payload = {
            "schema_version": "1.0",
            "collection_kind": "linkedin_public_search_cards_s1_only",
            "location": "United States",
            "per_keyword_limit": 30,
            "pages": 2,
            "target_job_count": 30,
            "minimum_job_count": 10,
            "target_required": False,
            "fetch_timeout_seconds": 8,
            "queries": [
                {"lane": "one", "keyword": "Data Analyst"},
                {"lane": "two", "keyword": " data   analyst "},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unique"):
                load_collection_contract(path)

    def test_rejects_query_contract_target_above_collection_ceiling(self):
        payload = {
            "schema_version": "1.0",
            "collection_kind": "linkedin_public_search_cards_s1_only",
            "location": "United States",
            "per_keyword_limit": 10,
            "pages": 1,
            "target_job_count": 11,
            "minimum_job_count": 5,
            "target_required": False,
            "fetch_timeout_seconds": 8,
            "queries": [{"lane": "one", "keyword": "Data Analyst"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "collection ceiling"):
                load_collection_contract(path)

    def test_accepts_minimum_without_exact_target(self):
        validate_collection_count(80, target=120, minimum=50, require_target=False)

    def test_requires_exact_target_when_requested(self):
        validate_collection_count(120, target=120, minimum=50, require_target=True)
        with self.assertRaisesRegex(SystemExit, "exactly 120"):
            validate_collection_count(
                119,
                target=120,
                minimum=50,
                require_target=True,
            )

    def test_rejects_invalid_bounds_and_shortfall(self):
        with self.assertRaisesRegex(SystemExit, "minimum <= target"):
            validate_collection_count(
                0,
                target=40,
                minimum=50,
                require_target=False,
            )
        with self.assertRaisesRegex(SystemExit, "at least 50"):
            validate_collection_count(
                49,
                target=120,
                minimum=50,
                require_target=False,
            )


if __name__ == "__main__":
    unittest.main()
