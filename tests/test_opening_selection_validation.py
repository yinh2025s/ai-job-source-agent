import unittest

from job_source_agent.identity_continuity import (
    OpeningIdentity,
    OpeningSelectionEvidence,
    ProviderIdentity,
)
from job_source_agent.opening_selection_validation import (
    classify_location,
    validate_opening_selection,
)


def _provider(**changes):
    values = {
        "hiring_entity_name": "Acme",
        "provider": "lever",
        "tenant": "acme",
        "canonical_board_url": "https://jobs.lever.co/acme",
        "evidence_url": "https://jobs.lever.co/acme/role-1",
        "verification_method": "linkedin_external_apply",
        "relationship_verified": True,
    }
    values.update(changes)
    return ProviderIdentity(**values)


def _opening(**changes):
    values = {
        "hiring_entity_name": "Acme",
        "provider": "lever",
        "tenant": "acme",
        "canonical_board_url": "https://jobs.lever.co/acme",
        "canonical_opening_url": "https://jobs.lever.co/acme/role-1",
    }
    values.update(changes)
    return OpeningIdentity(**values)


def _selection(**changes):
    values = {
        "provider": "lever",
        "tenant": "acme",
        "canonical_board_url": "https://jobs.lever.co/acme",
        "canonical_opening_url": "https://jobs.lever.co/acme/role-1",
        "title": "AI Engineer",
        "location": "New York, NY",
        "inventory_scope": "full",
        "inventory_complete": True,
        "candidate_count": 3,
    }
    values.update(changes)
    return OpeningSelectionEvidence(**values)


class OpeningSelectionValidationTests(unittest.TestCase):
    def test_verified_selection_requires_same_provider_tenant_board_and_url(self):
        issues, _location = validate_opening_selection(
            selection=_selection(tenant="not-acme"),
            provider=_provider(),
            opening=_opening(),
            open_position_url="https://jobs.lever.co/acme/role-1",
            target_title="AI Engineer",
            target_location="New York, NY",
        )

        self.assertIn("OPENING_SELECTION_TENANT_MISMATCH", issues)

    def test_title_and_explicit_location_mismatch_reject_new_candidate_path(self):
        issues, location = validate_opening_selection(
            selection=_selection(title="Platform Engineer", location="York, UK"),
            provider=_provider(),
            opening=_opening(),
            open_position_url="https://jobs.lever.co/acme/role-1",
            target_title="AI Engineer",
            target_location="New York, NY",
        )

        self.assertIn("OPENING_TITLE_MISMATCH", issues)
        self.assertIn("OPENING_LOCATION_MISMATCH", issues)
        self.assertEqual(location, "mismatch")

    def test_location_classification_avoids_single_token_york_false_positive(self):
        self.assertEqual(classify_location("New York, NY", "York, UK"), "mismatch")
        self.assertEqual(classify_location("New York, NY", "New York"), "overlap")
        self.assertEqual(classify_location(None, "New York"), "missing")

    def test_strict_new_path_requires_typed_selection(self):
        issues, location = validate_opening_selection(
            selection=None,
            provider=_provider(),
            opening=_opening(),
            open_position_url="https://jobs.lever.co/acme/role-1",
            target_title="AI Engineer",
            target_location=None,
        )

        self.assertEqual(issues, ["OPENING_SELECTION_MISSING"])
        self.assertEqual(location, "missing")


if __name__ == "__main__":
    unittest.main()
