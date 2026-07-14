import unittest
from pathlib import Path

from job_source_agent.web import Page, extract_links, fixture_path_candidates


class WebExtractionTests(unittest.TestCase):
    def test_fixture_path_treats_trailing_dot_url_segment_as_directory(self):
        path = fixture_path_candidates(
            "/tmp/fixtures",
            "https://www.linkedin.com/company/awesome-motive-inc.",
        )[0]

        self.assertEqual(
            path,
            Path(
                "/tmp/fixtures/www.linkedin.com/company/"
                "awesome-motive-inc./index.html"
            ),
        )

    def test_extract_links_skips_invalid_raw_urls(self):
        page = Page(
            url="https://example.com",
            html="""
            <html>
              <body>
                <a href="/careers">Careers</a>
                <script>
                  const broken = "https://[not-a-valid-ipv6-url";
                </script>
              </body>
            </html>
            """,
        )

        links = extract_links(page)

        self.assertEqual([link.url for link in links], ["https://example.com/careers"])

    def test_extract_links_skips_invalid_anchor_href(self):
        page = Page(
            url="https://example.com",
            html='<a href="https://[not-a-valid-ipv6-url">Broken</a><a href="/jobs">Jobs</a>',
        )

        links = extract_links(page)

        self.assertEqual([link.url for link in links], ["https://example.com/jobs"])


if __name__ == "__main__":
    unittest.main()
