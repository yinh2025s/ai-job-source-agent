import unittest

from scripts.collect_linkedin_candidate_pool import validate_collection_count


class CollectLinkedInCandidatePoolTests(unittest.TestCase):
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
