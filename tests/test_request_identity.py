import unittest

from job_source_agent.request_identity import (
    REDACTED_VALUE,
    build_request_identity,
    is_sensitive_key,
    sanitize_url,
)
from job_source_agent.web import normalize_url


class RequestIdentityTests(unittest.TestCase):
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
                "Authorization": "Bearer private",
                "Cookie": "session=private",
            },
        )

        self.assertEqual(
            identity.semantic_headers,
            {
                "accept": "application/json",
                "x-referer-host": "https://jobs.example.test/?token=%5BREDACTED%5D",
            },
        )
        self.assertNotIn("private", str(identity.as_dict()))


if __name__ == "__main__":
    unittest.main()
