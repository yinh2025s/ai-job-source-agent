import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from job_source_agent.linkedin import (
    load_company_inputs,
    parse_linkedin_html,
    parse_visible_external_apply_url,
)


ROOT = Path(__file__).resolve().parents[1]


class LinkedInAdapterTests(unittest.TestCase):
    def test_saved_html_can_seed_company_record(self):
        records = load_company_inputs(ROOT / "samples" / "linkedin_html_input.json")

        self.assertEqual(records[0].company_name, "Example Robotics")
        self.assertEqual(records[0].company_website_url, "https://example-robotics.test")
        self.assertEqual(records[0].linkedin_company_url, "https://www.linkedin.com/company/example-robotics")

    def test_public_payload_supplies_explicit_company_identity_and_website(self):
        data = parse_linkedin_html(ROOT / "tests" / "fixtures" / "linkedin" / "public_company_payload.html")

        self.assertEqual(data["company_name"], "Evidence Robotics")
        self.assertEqual(
            data["linkedin_company_url"],
            "https://www.linkedin.com/company/evidence-robotics",
        )
        self.assertEqual(
            data["company_website_url"],
            "https://evidence-robotics.example/about",
        )

    def test_apply_tracking_and_cdn_links_are_not_treated_as_company_website(self):
        data = parse_linkedin_html(ROOT / "tests" / "fixtures" / "linkedin" / "untrusted_external_links.html")

        self.assertEqual(data["company_name"], "Safety Labs")
        self.assertEqual(data["linkedin_company_url"], "https://www.linkedin.com/company/safety-labs")
        self.assertEqual(
            data["external_apply_url"],
            "https://apply.workable.com/safety-labs/j/ABC123",
        )
        self.assertNotIn("company_website_url", data)

    def test_explicit_website_label_still_rejects_ats_host(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "linkedin.html"
            path.write_text(
                '<a href="https://jobs.lever.co/safety-labs">Company website</a>'
                '<a href="https://www.linkedin.com/company/safety-labs">Safety Labs</a>',
                encoding="utf-8",
            )

            data = parse_linkedin_html(path)

        self.assertNotIn("company_website_url", data)

    def test_ats_vendor_product_domains_can_still_be_company_websites(self):
        for website in (
            "https://www.workable.com",
            "https://www.rippling.com",
            "https://www.smartrecruiters.com",
            "https://www.bamboohr.com",
        ):
            with self.subTest(website=website), TemporaryDirectory() as directory:
                path = Path(directory) / "linkedin.html"
                path.write_text(
                    f'<a href="{website}">Company website</a>'
                    '<a href="https://www.linkedin.com/company/vendor">Vendor</a>',
                    encoding="utf-8",
                )

                data = parse_linkedin_html(path)

            self.assertEqual(data["company_website_url"], website)

    def test_unrelated_nested_website_url_without_company_context_is_ignored(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "linkedin.html"
            path.write_text(
                '<script type="application/json">'
                '{"company":{"companyName":"Safety Labs"},'
                '"advertisement":{"websiteUrl":"https://sponsor.example"}}'
                '</script>',
                encoding="utf-8",
            )

            data = parse_linkedin_html(path)

        self.assertEqual(data["company_name"], "Safety Labs")
        self.assertNotIn("company_website_url", data)

    def test_result_record_can_be_reused_as_input(self):
        result_record = {
            "company_name": "Example Robotics",
            "company_website_url": "https://example-robotics.test",
            "linkedin_job_url": "https://www.linkedin.com/jobs/view/123",
            "linkedin_job_title": "AI Engineer",
            "linkedin_job_location": "New York, NY",
            "career_page_url": "https://example-robotics.test/careers",
            "status": "success",
            "error": None,
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "previous-results.json"
            path.write_text(json.dumps([result_record]), encoding="utf-8")

            records = load_company_inputs(path)

        self.assertEqual(records[0].job_title, "AI Engineer")
        self.assertEqual(records[0].job_location, "New York, NY")
        self.assertEqual(records[0].career_root_url, "https://example-robotics.test/careers")

    def test_frozen_cohort_wrapper_can_be_reused_as_input(self):
        cohort = {
            "schema_version": "1.0",
            "companies_sha256": "frozen-digest",
            "postings": [
                {
                    "company_name": "Example Robotics",
                    "linkedin_job_url": "https://www.linkedin.com/jobs/view/123",
                    "job_title": "AI Engineer",
                }
            ],
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "frozen-cohort.json"
            path.write_text(json.dumps(cohort), encoding="utf-8")

            records = load_company_inputs(path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].company_name, "Example Robotics")
        self.assertEqual(records[0].job_title, "AI Engineer")

    def test_unrecognized_object_input_remains_invalid(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-cohort.json"
            path.write_text(json.dumps({"records": []}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "JSON array"):
                load_company_inputs(path)

    def test_visible_external_apply_parser_accepts_public_https_link(self):
        html = '<a href="https://jobs.example.com/openings/123?source=linkedin">Apply now</a>'

        self.assertEqual(
            parse_visible_external_apply_url(html),
            "https://jobs.example.com/openings/123?source=linkedin",
        )

    def test_visible_external_apply_parser_rejects_unsafe_urls(self):
        unsafe_urls = (
            "https://www.linkedin.com/jobs/view/123",
            "https://media.licdn.com/apply/123",
            "https://lnkd.in/secret",
            "http://jobs.example.com/openings/123",
            "https://127.0.0.1/openings/123",
            "https://jobs.internal/openings/123",
            "https://user:secret@jobs.example.com/openings/123",
            "https://jobs.example.com/openings/123?access_token=secret",
            "https://jobs.example.com/openings/123#private",
        )
        for url in unsafe_urls:
            with self.subTest(url=url):
                self.assertEqual(
                    parse_visible_external_apply_url(f'<a href="{url}">Apply now</a>'),
                    "",
                )

    def test_external_apply_in_script_is_not_visible_evidence(self):
        html = '<script>{"externalApplyUrl":"https://jobs.example.com/openings/123"}</script>'

        self.assertEqual(parse_visible_external_apply_url(html), "")

    def test_hidden_external_apply_anchor_is_not_visible_evidence(self):
        html = '<a hidden href="https://jobs.example.com/openings/123">Apply now</a>'

        self.assertEqual(parse_visible_external_apply_url(html), "")


if __name__ == "__main__":
    unittest.main()
