import unittest

from job_source_agent.browser_interaction import JobSearchInteraction
from job_source_agent.request_identity import (
    REDACTED_VALUE,
    build_request_identity,
    is_sensitive_key,
    sanitize_url,
)
from job_source_agent.web import normalize_url


class RequestIdentityTests(unittest.TestCase):
    def test_browser_interaction_has_distinct_redacted_request_identity(self):
        first = JobSearchInteraction(
            form_ordinal=0,
            query_name="query",
            query_id="job-keywords",
            submit_text="Search",
            target_title="Data Analyst",
        )
        second = JobSearchInteraction(
            form_ordinal=0,
            query_name="query",
            query_id="job-keywords",
            submit_text="Search",
            target_title="Senior Data Analyst",
        )

        plain = build_request_identity("https://example.test/jobs")
        searched = build_request_identity(
            "https://example.test/jobs", interaction=first
        )
        other_search = build_request_identity(
            "https://example.test/jobs", interaction=second
        )

        self.assertNotEqual(plain.fingerprint(), searched.fingerprint())
        self.assertNotEqual(searched.fingerprint(), other_search.fingerprint())
        self.assertEqual(
            searched.semantic_headers["x-job-source-agent-interaction"],
            first.fingerprint(),
        )
        self.assertNotIn("Data Analyst", str(searched.as_dict()))

    def test_browser_interaction_rejects_arbitrary_selectors_and_control_text(self):
        with self.assertRaises(ValueError):
            JobSearchInteraction(
                form_ordinal=0,
                query_name="input[name=q]",
                submit_text="Search",
                target_title="Data Analyst",
            )
        with self.assertRaises(ValueError):
            JobSearchInteraction(
                form_ordinal=0,
                query_name="query",
                submit_text="Search",
                target_title="Data\nAnalyst",
            )
        with self.assertRaises(ValueError):
            JobSearchInteraction(
                form_ordinal=0,
                query_name=None,
                submit_text="Search",
                target_title="Data Analyst",
            )

    def test_placeholder_only_job_search_interaction_is_supported(self):
        interaction = JobSearchInteraction(
            form_ordinal=0,
            query_name=None,
            query_placeholder="Job Title",
            submit_text="Find Jobs",
            submit_tag="span",
            target_title="Registered Nurse",
        )

        self.assertEqual(interaction.query_placeholder, "Job Title")
        self.assertEqual(interaction.submit_tag, "span")

    def test_navigation_normalization_preserves_response_affecting_empty_query_values(self):
        normalized = normalize_url(
            "https://example.test/api?themeid=&utm_source=test&job_id="
        )

        self.assertEqual(normalized, "https://example.test/api?themeid=&job_id=")

    def test_sensitive_key_spelling_variants_share_url_identity(self):
        urls = [
            "https://example.test/jobs?apikey=one&tenant=acme",
            "https://example.test/jobs?api_key=two&tenant=acme",
            "https://example.test/jobs?api-key=three&tenant=acme",
        ]

        sanitized = [sanitize_url(url) for url in urls]

        self.assertTrue(all(REDACTED_VALUE not in value for value in urls))
        self.assertTrue(all("%5BREDACTED%5D" in value for value in sanitized))
        self.assertTrue(all(is_sensitive_key(key) for key in ("apikey", "api_key", "api-key")))

    def test_json_body_fingerprint_is_stable_after_sensitive_redaction(self):
        first = build_request_identity(
            "https://example.test/api",
            data=b'{"range": 10, "api_key": "first"}',
            headers={"Content-Type": "application/json"},
        )
        second = build_request_identity(
            "https://example.test/api",
            data=b'{"api_key": "second", "range": 10}',
            headers={"content-type": "application/json"},
        )
        other_page = build_request_identity(
            "https://example.test/api",
            data=b'{"range": 20, "api_key": "first"}',
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(first.body_fingerprint, second.body_fingerprint)
        self.assertNotEqual(first.body_fingerprint, other_page.body_fingerprint)

    def test_form_embedded_json_recursively_redacts_sensitive_values(self):
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        first = build_request_identity(
            "https://jobs.example.test/search",
            data=(
                "payload=%7B%22page%22%3A0%2C%22token%22%3A%22first-secret%22%7D"
            ).encode(),
            headers=headers,
        )
        second = build_request_identity(
            "https://jobs.example.test/search",
            data=(
                "payload=%7B%22token%22%3A%22second-secret%22%2C%22page%22%3A0%7D"
            ).encode(),
            headers=headers,
        )
        other_page = build_request_identity(
            "https://jobs.example.test/search",
            data=(
                "payload=%7B%22page%22%3A1%2C%22token%22%3A%22first-secret%22%7D"
            ).encode(),
            headers=headers,
        )

        self.assertEqual(first.identity_version, "2")
        self.assertEqual(first.body_fingerprint, second.body_fingerprint)
        self.assertNotEqual(first.body_fingerprint, other_page.body_fingerprint)
        self.assertEqual(first.fingerprint(), second.fingerprint())
        self.assertTrue(first.replayable)

    def test_form_body_redacts_credentials_without_losing_pagination(self):
        first = build_request_identity(
            "https://example.test/api",
            data=b"offset=10&token=alpha",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        second = build_request_identity(
            "https://example.test/api",
            data=b"token=beta&offset=10",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        self.assertEqual(first.body_fingerprint, second.body_fingerprint)
        self.assertTrue(first.requires_fixture_suffix)

    def test_meta_lsd_form_token_is_sensitive_and_does_not_change_identity(self):
        first = build_request_identity(
            "https://www.metacareers.com/graphql",
            data=b"doc_id=123&lsd=first-secret&q=Engineer",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        second = build_request_identity(
            "https://www.metacareers.com/graphql",
            data=b"doc_id=123&lsd=second-secret&q=Engineer",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        self.assertTrue(is_sensitive_key("lsd"))
        self.assertTrue(is_sensitive_key("X-FB-LSD"))
        self.assertEqual(first.body_fingerprint, second.body_fingerprint)
        self.assertNotIn("first-secret", repr(first.as_dict()))

    def test_ceipal_credential_path_is_redacted_without_losing_endpoint_shape(self):
        first = sanitize_url(
            "https://careerapi.ceipal.com/private-one/CareerPortalJobPostings/?page=2"
        )
        second = sanitize_url(
            "https://careerapi.ceipal.com/private-two/CareerPortalJobPostings/?page=2"
        )

        self.assertEqual(first, second)
        self.assertNotIn("private-one", first)
        self.assertIn("/%5BREDACTED%5D/CareerPortalJobPostings/", first)
        self.assertEqual(sanitize_url(first), first)

    def test_multipart_body_redacts_api_key_and_preserves_tenant_page_and_search(self):
        boundary = "----Ceipal-Test-Boundary"

        def body(api_key, page="2", portal="tenant-one", search="AI Engineer"):
            fields = {
                "page": page,
                "api_key": api_key,
                "method": "CareerPortalJobPostings",
                "cp_id": portal,
                "from_career_portal": "1",
                "searchkey": search,
            }
            chunks = []
            for name, value in fields.items():
                chunks.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                    f"\r\n\r\n{value}\r\n"
                )
            chunks.append(f"--{boundary}--\r\n")
            return "".join(chunks).encode()

        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Origin": "https://jobsapi.ceipal.com",
            "Referer": "https://jobsapi.ceipal.com/APISource/v1/index.html",
        }
        first = build_request_identity(
            "https://careerapi.ceipal.com/first/CareerPortalJobPostings/?page=2",
            data=body("first"),
            headers=headers,
        )
        same = build_request_identity(
            "https://careerapi.ceipal.com/second/CareerPortalJobPostings/?page=2",
            data=body("second"),
            headers=headers,
        )
        other_page = build_request_identity(
            "https://careerapi.ceipal.com/first/CareerPortalJobPostings/?page=3",
            data=body("first", page="3"),
            headers=headers,
        )
        other_tenant = build_request_identity(
            "https://careerapi.ceipal.com/first/CareerPortalJobPostings/?page=2",
            data=body("first", portal="tenant-two"),
            headers=headers,
        )

        self.assertTrue(first.replayable)
        self.assertEqual(first.body_fingerprint, same.body_fingerprint)
        self.assertEqual(first.fingerprint(), same.fingerprint())
        self.assertNotEqual(first.fingerprint(), other_page.fingerprint())
        self.assertNotEqual(first.body_fingerprint, other_tenant.body_fingerprint)
        self.assertNotIn("first", str(first.as_dict()))
        self.assertEqual(
            set(first.semantic_headers),
            {"content-type", "origin", "referer"},
        )

    def test_multipart_file_or_malformed_body_is_not_replayable(self):
        boundary = "----unsafe"
        data = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="resume"; '
            'filename="resume.pdf"\r\n\r\nsecret\r\n'
            f"--{boundary}--\r\n"
        ).encode()
        identity = build_request_identity(
            "https://example.test/apply",
            data=data,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

        self.assertFalse(identity.replayable)
        self.assertEqual(identity.non_replayable_reason, "invalid_multipart_form_body")

    def test_opaque_body_is_classified_without_a_raw_digest(self):
        identity = build_request_identity(
            "https://example.test/api",
            data=b"opaque payload without structure",
        )

        self.assertFalse(identity.replayable)
        self.assertIsNone(identity.body_fingerprint)
        self.assertEqual(identity.non_replayable_reason, "opaque_body")

    def test_only_safe_semantic_headers_are_retained(self):
        identity = build_request_identity(
            "https://example.test/api",
            headers={
                "Accept": "application/json",
                "X-Referer-Host": "https://jobs.example.test/?token=secret",
                "Origin": "https://jobsapi.ceipal.com",
                "Referer": "https://jobsapi.ceipal.com/APISource/v1/index.html",
                "Authorization": "Bearer private",
                "Cookie": "session=private",
            },
        )

        self.assertEqual(
            identity.semantic_headers,
            {
                "accept": "application/json",
                "origin": "https://jobsapi.ceipal.com",
                "referer": "https://jobsapi.ceipal.com/APISource/v1/index.html",
                "x-referer-host": "https://jobs.example.test/?token=%5BREDACTED%5D",
            },
        )
        self.assertNotIn("private", str(identity.as_dict()))


if __name__ == "__main__":
    unittest.main()
