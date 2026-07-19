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

    def test_publication_rejects_conflicting_seniority(self):
        issues, _location = validate_opening_selection(
            selection=_selection(title="Principal Software Engineer"),
            provider=_provider(),
            opening=_opening(),
            open_position_url="https://jobs.lever.co/acme/role-1",
            target_title="Software Engineer 1",
            target_location="New York, NY",
        )

        self.assertIn("OPENING_TITLE_MISMATCH", issues)

    def test_publication_rejects_extra_role_specialization(self):
        issues, _location = validate_opening_selection(
            selection=_selection(title="Battery Product Design Engineer"),
            provider=_provider(),
            opening=_opening(),
            open_position_url="https://jobs.lever.co/acme/role-1",
            target_title="Product Design Engineer",
            target_location="Sunnyvale, CA",
        )

        self.assertIn("OPENING_TITLE_MISMATCH", issues)

    def test_explicit_title_city_qualifier_can_refine_broad_inventory_region(self):
        issues, location = validate_opening_selection(
            selection=_selection(
                title="Account Executive, NYC",
                location="Americas",
            ),
            provider=_provider(),
            opening=_opening(),
            open_position_url="https://jobs.lever.co/acme/role-1",
            target_title="Account Executive",
            target_location="New York, NY",
        )

        self.assertNotIn("OPENING_LOCATION_MISMATCH", issues)
        self.assertEqual(location, "title_qualifier")

    def test_title_city_qualifier_does_not_override_a_different_target_city(self):
        issues, location = validate_opening_selection(
            selection=_selection(
                title="Account Executive, D.C.",
                location="Americas",
            ),
            provider=_provider(),
            opening=_opening(),
            open_position_url="https://jobs.lever.co/acme/role-1",
            target_title="Account Executive",
            target_location="New York, NY",
        )

        self.assertIn("OPENING_LOCATION_MISMATCH", issues)
        self.assertEqual(location, "mismatch")

    def test_location_classification_avoids_single_token_york_false_positive(self):
        self.assertEqual(classify_location("New York, NY", "York, UK"), "mismatch")
        self.assertEqual(classify_location("New York, NY", "New York"), "overlap")
        self.assertEqual(classify_location("Houston, Texas", "Houston, TX"), "exact")
        self.assertEqual(classify_location("Lynnwood Clinic", "Lynnwood, WA"), "overlap")
        self.assertEqual(classify_location(None, "New York"), "missing")

    def test_location_classification_accepts_opaque_facility_in_target_state(self):
        self.assertEqual(classify_location("C Forks PA", "Easton, PA"), "region")

    def test_location_classification_keeps_explicit_city_and_state_conflicts(self):
        self.assertEqual(classify_location("Pittsburgh, PA", "Easton, PA"), "mismatch")
        self.assertEqual(classify_location("Houston, CA", "Houston, TX"), "mismatch")

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

    def test_missing_location_rejects_explicit_conflicting_state_in_opening_url(self):
        wrong_url = (
            "https://jobs.acme.test/job/mechanical-design-engineer-"
            "bellevue-washington-342288/role-1"
        )
        issues, location = validate_opening_selection(
            selection=_selection(
                canonical_opening_url=wrong_url,
                title="Mechanical Design Engineer",
                location=None,
            ),
            provider=_provider(),
            opening=_opening(canonical_opening_url=wrong_url),
            open_position_url=wrong_url,
            target_title="Mechanical Design Engineer",
            target_location="York, PA",
        )

        self.assertIn("OPENING_LOCATION_MISMATCH", issues)
        self.assertEqual(location, "mismatch")

    def test_missing_location_keeps_matching_or_unstated_url_location(self):
        for url, expected_location in (
            (
                "https://jobs.acme.test/job/senior-engineer-forney-tx/role-1",
                "url_qualifier",
            ),
            ("https://jobs.acme.test/jobs/role-1", "missing"),
        ):
            with self.subTest(url=url):
                issues, location = validate_opening_selection(
                    selection=_selection(
                        canonical_opening_url=url,
                        title="Senior Engineer",
                        location=None,
                    ),
                    provider=_provider(),
                    opening=_opening(canonical_opening_url=url),
                    open_position_url=url,
                    target_title="Senior Engineer",
                    target_location="Forney, TX",
                )
                self.assertNotIn("OPENING_LOCATION_MISMATCH", issues)
                self.assertEqual(location, expected_location)

    def test_missing_location_ignores_opaque_requisition_id_state_fragments(self):
        opening_url = (
            "https://jobs.example.com/job-search/"
            "bcf896f7352f1001b167c46dc9d00000"
        )
        issues, location = validate_opening_selection(
            selection=_selection(
                canonical_opening_url=opening_url,
                title="National Account Manager - Hotels",
                location=None,
            ),
            provider=_provider(),
            opening=_opening(canonical_opening_url=opening_url),
            open_position_url=opening_url,
            target_title="National Account Manager - Hotels",
            target_location="New York, NY",
        )

        self.assertNotIn("OPENING_LOCATION_MISMATCH", issues)
        self.assertEqual(location, "missing")

    def test_incomplete_unknown_multi_candidate_selection_requires_location_evidence(self):
        issues, location = validate_opening_selection(
            selection=_selection(
                location=None,
                inventory_scope="unknown",
                inventory_complete=False,
                candidate_count=8,
            ),
            provider=_provider(),
            opening=_opening(),
            open_position_url="https://jobs.lever.co/acme/role-1",
            target_title="AI Engineer",
            target_location="New York, NY",
        )

        self.assertIn("OPENING_LOCATION_UNVERIFIED", issues)
        self.assertEqual(location, "missing")

    def test_canonical_opening_slug_can_supply_explicit_city_and_state(self):
        opening_url = (
            "https://jobs.example.com/job/"
            "Portland-Financial-Analyst-OR/1408137933"
        )
        issues, location = validate_opening_selection(
            selection=_selection(
                canonical_opening_url=opening_url,
                title="Financial Analyst",
                location=None,
                inventory_scope="unknown",
                inventory_complete=False,
                candidate_count=2,
            ),
            provider=_provider(canonical_board_url="https://jobs.example.com"),
            opening=_opening(canonical_opening_url=opening_url),
            open_position_url=opening_url,
            target_title="Financial Analyst",
            target_location="Portland, OR",
        )

        self.assertNotIn("OPENING_LOCATION_UNVERIFIED", issues)
        self.assertEqual(location, "url_qualifier")

    def test_opening_slug_city_without_target_state_is_not_location_proof(self):
        opening_url = "https://jobs.example.com/job/Portland-Financial-Analyst/123"
        issues, location = validate_opening_selection(
            selection=_selection(
                canonical_opening_url=opening_url,
                title="Financial Analyst",
                location=None,
                inventory_scope="unknown",
                inventory_complete=False,
                candidate_count=2,
            ),
            provider=_provider(canonical_board_url="https://jobs.example.com"),
            opening=_opening(canonical_opening_url=opening_url),
            open_position_url=opening_url,
            target_title="Financial Analyst",
            target_location="Portland, OR",
        )

        self.assertIn("OPENING_LOCATION_UNVERIFIED", issues)
        self.assertEqual(location, "missing")

    def test_unique_incomplete_selection_can_remain_location_unstated(self):
        issues, location = validate_opening_selection(
            selection=_selection(
                location=None,
                inventory_scope="unknown",
                inventory_complete=False,
                candidate_count=1,
            ),
            provider=_provider(),
            opening=_opening(),
            open_position_url="https://jobs.lever.co/acme/role-1",
            target_title="AI Engineer",
            target_location="New York, NY",
        )

        self.assertNotIn("OPENING_LOCATION_UNVERIFIED", issues)
        self.assertEqual(location, "missing")


if __name__ == "__main__":
    unittest.main()
