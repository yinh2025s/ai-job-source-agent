import unittest

from job_source_agent.job_actions import (
    classify_career_action,
    is_explicit_career_action,
    is_internal_career_action,
)


class CareerActionTests(unittest.TestCase):
    def test_semantic_variants_are_high_confidence_actions(self):
        cases = {
            "VIEW ALL AVAILABLE JOBS AND APPLY": "open_job_list_and_apply",
            "View Job Postings": "open_job_list",
            "Explore Opportunities": "browse_jobs",
            "Search Our Open Roles": "search_jobs",
            "See Current Openings": "open_job_list",
            "Browse Vacancies": "browse_jobs",
        }
        for label, expected_kind in cases.items():
            with self.subTest(label=label):
                action = classify_career_action(label)
                self.assertIsNotNone(action)
                self.assertEqual(action.kind, expected_kind)
                self.assertEqual(action.confidence, "high")

    def test_internal_and_non_action_links_are_rejected(self):
        for label in (
            "Internal Applicants Only",
            "Employee Login",
            "Log in to apply",
            "Our Culture",
            "Benefits",
            "Apply",
            "Can’t find a role that fits right now?",
            "Join our talent community",
            "Register your interest",
            "Submit your resume",
        ):
            with self.subTest(label=label):
                self.assertFalse(is_explicit_career_action(label))

    def test_bounded_standalone_navigation_labels_are_actions(self):
        for label in (
            "Employment",
            "Employment Opportunities",
            "Job Board",
            "Job Openings",
            "JOBS IN THE HOUSE",
        ):
            with self.subTest(label=label):
                action = classify_career_action(label)
                self.assertIsNotNone(action)
                self.assertEqual(action.kind, "open_job_list")
                self.assertEqual(action.confidence, "high")

        self.assertFalse(is_explicit_career_action("Employment Law Updates"))

    def test_internal_marker_is_separately_available_for_trace(self):
        self.assertTrue(is_internal_career_action("Internal Applicants Only"))
        self.assertFalse(is_internal_career_action("View all available jobs and apply"))

    def test_short_scoped_opportunity_labels_are_job_lists(self):
        for label in (
            "CORPORATE OPPORTUNITIES",
            "RETAIL OPPORTUNITIES",
            "Store Opportunities",
        ):
            with self.subTest(label=label):
                action = classify_career_action(label)
                self.assertIsNotNone(action)
                self.assertEqual(action.kind, "open_job_list")
                self.assertEqual(action.confidence, "high")

    def test_non_job_opportunity_copy_remains_rejected(self):
        for label in (
            "Talent Opportunities",
            "Community Opportunities",
            "Business Opportunities",
            "Explore Partnership Opportunities",
            "Volunteer Opportunities",
            "Join our talent community",
        ):
            with self.subTest(label=label):
                self.assertIsNone(classify_career_action(label))


if __name__ == "__main__":
    unittest.main()
