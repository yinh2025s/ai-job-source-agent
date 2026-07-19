import unittest

from job_source_agent.result_identity import (
    canonicalize_identity_url,
    identity_urls_equivalent,
    public_result_identity,
)


class ResultIdentityTests(unittest.TestCase):
    def test_canonicalizes_public_url_without_erasing_query_identity(self):
        self.assertEqual(
            canonicalize_identity_url(
                "HTTPS://B\N{LATIN SMALL LETTER U WITH DIAERESIS}CHER.example:443/jobs/?req=R-1&utm_source=test#details"
            ),
            "https://xn--bcher-kva.example/jobs?req=R-1#details",
        )
        self.assertNotEqual(
            canonicalize_identity_url("https://jobs.example/opening?req=R-1"),
            canonicalize_identity_url("https://jobs.example/opening?req=R-2"),
        )

    def test_rejects_non_public_or_malformed_urls(self):
        invalid = [
            "ftp://example.com/jobs",
            "https://user:secret@example.com/jobs",
            "https://example.com:bad/jobs",
            "https://example.com/jobs\nsecond",
            "https://example.com/jobs%zz",
            "/relative/jobs",
            "https://localhost/jobs",
            "https://service.local/jobs",
            "https://service.internal/jobs",
            "https://127.0.0.1/jobs",
            "https://[::1]/jobs",
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonicalize_identity_url(value)

    def test_preserves_safe_fragment_as_opening_identity(self):
        first = canonicalize_identity_url(
            "https://jobs.example/candidate#detail/job/4242"
        )
        second = canonicalize_identity_url(
            "https://jobs.example/candidate#detail/job/4343"
        )

        self.assertEqual(first, "https://jobs.example/candidate#detail/job/4242")
        self.assertNotEqual(first, second)

    def test_normalizes_trailing_dot_and_rejects_raw_or_decoded_controls(self):
        self.assertEqual(
            canonicalize_identity_url("https://Jobs.Example./opening"),
            "https://jobs.example/opening",
        )
        invalid = [
            "https://jobs.example/path\nnext",
            "https://jobs.example/path?req=one\rnext",
            "https://jobs.example/path#detail\tjob",
            "https://jobs.example/path%0Anext",
            "https://jobs.example/path?req=one%0Dnext",
            "https://jobs.example/path#detail%09job",
            "https://jobs.example/path?req=%C2%85",
            "https://jobs.example/path#detail%zzjob",
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonicalize_identity_url(value)

    def test_www_equivalence_is_explicit(self):
        plain = "https://example.com/careers"
        www = "https://www.example.com/careers"
        self.assertTrue(identity_urls_equivalent(plain, www, allow_www=True))
        self.assertFalse(identity_urls_equivalent(plain, www))

    def test_equivalence_ignores_tracking_parameters(self):
        self.assertTrue(
            identity_urls_equivalent(
                "https://jobs.example/role/123",
                "https://jobs.example/role/123?utm_medium=jobshare",
            )
        )

    def test_public_identity_uses_url_tenant_and_no_runtime_identifiers(self):
        identity = public_result_identity(
            {
                "company_website_url": "https://example.com/",
                "career_page_url": "https://example.com/careers/",
                "job_list_page_url": "https://jobs.example.com/acme/",
                "open_position_url": "https://jobs.example.com/acme/123/",
                "trace": {"tenant_id": "secret-runtime-value"},
            },
            "example_ats",
        )

        self.assertEqual(
            identity["job_board"]["tenant"],
            "url:https://jobs.example.com/acme",
        )
        self.assertNotIn("secret-runtime-value", repr(identity))


if __name__ == "__main__":
    unittest.main()
