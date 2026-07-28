import unittest

from job_source_agent.job_board import DiscoveredJobBoard
from job_source_agent.opening_matcher import JobOpeningMatcher
from job_source_agent.providers.base import JobBoard
from job_source_agent.web import FetchError, Page


class _VisibleDetailFetcher:
    def __init__(
        self,
        *,
        board_url: str,
        detail_url: str,
        title: str,
        detail_html: str,
    ) -> None:
        self.board_url = board_url
        self.detail_url = detail_url
        self.title = title
        self.detail_html = detail_html
        self.calls: list[str] = []

    def fetch(self, url, data=None, headers=None, **kwargs):
        self.calls.append(url)
        if url.rstrip("/") == self.board_url.rstrip("/"):
            return Page(
                url=url,
                final_url=self.board_url,
                source="fixture",
                html=(
                    "<html><body><main><h2>Current openings</h2>"
                    f'<a href="{self.detail_url}">{self.title}</a>'
                    "</main></body></html>"
                ),
            )
        if url.rstrip("/") == self.detail_url.rstrip("/"):
            return Page(
                url=url,
                final_url=self.detail_url,
                source="fixture",
                html=self.detail_html,
            )
        raise FetchError(f"fixture has no response for {url}")


class VisibleFirstPartyDetailIdentityTests(unittest.TestCase):
    def _match(
        self,
        *,
        title: str,
        target_location: str,
        detail_html: str,
        board_url: str = "https://careers.example.test/openings",
        detail_url: str = "https://careers.example.test/jobs/role-123",
    ):
        fetcher = _VisibleDetailFetcher(
            board_url=board_url,
            detail_url=detail_url,
            title=title,
            detail_html=detail_html,
        )
        discovered = DiscoveredJobBoard(
            board=JobBoard(url=board_url, provider="generic"),
            detection_method="verified_first_party_action",
            evidence_url="https://www.example.test/careers",
            relationship_evidence_url="https://www.example.test/careers",
        )
        match, trace = JobOpeningMatcher(fetcher).match(
            board_url,
            title,
            target_location,
            discovered_board=discovered,
        )
        return match, trace, fetcher

    def test_accepts_four_bounded_visible_detail_shapes(self):
        cases = (
            (
                "multi-location line near h1",
                "DevOps Engineer",
                "Detroit, MI",
                """
                    <html><body><main>
                      <h1>DevOps Engineer</h1>
                      <p>Detroit, MI | New York, NY | Remote</p>
                      <section><h2>About the role</h2><p>Build reliable systems.</p></section>
                    </main></body></html>
                """,
                "Detroit, MI",
            ),
            (
                "labelled work location section",
                "DevOps Engineer",
                "San Diego, CA",
                """
                    <html><body><main>
                      <h1>DevOps Engineer</h1>
                      <section><h2>About the role</h2><p>Build medical systems.</p></section>
                      <section>
                        <h2>Work Location and Conditions</h2>
                        <p>This position is based in San Diego, California.</p>
                      </section>
                    </main></body></html>
                """,
                "San Diego, California",
            ),
            (
                "compact employment type and location",
                "Data Scientist",
                "Pittsburgh, PA",
                """
                    <html><body><main>
                      <h1>Data Scientist</h1>
                      <p>Full Time, Pittsburgh, PA</p>
                      <a href="#application">Apply Now</a>
                    </main></body></html>
                """,
                "Pittsburgh, PA",
            ),
            (
                "where and how you can work section",
                "Enterprise Customer Success Manager",
                "Austin, TX",
                """
                    <html><body><main>
                      <h1>Enterprise Customer Success Manager</h1>
                      <section><h2>What you will do</h2><p>Support enterprise teams.</p></section>
                      <section>
                        <h2>Where and how you can work</h2>
                        <p>This role is available in Austin, Texas.</p>
                      </section>
                    </main></body></html>
                """,
                "Austin, Texas",
            ),
        )

        for name, title, target_location, html, observed_location in cases:
            with self.subTest(name=name):
                match, trace, _fetcher = self._match(
                    title=title,
                    target_location=target_location,
                    detail_html=html,
                )

                self.assertIsNotNone(match, trace)
                self.assertEqual(
                    match.url,
                    "https://careers.example.test/jobs/role-123",
                )
                self.assertEqual(match.location, observed_location)
                self.assertIn(
                    "verified same-site visible job detail",
                    match.reasons,
                )

    def test_rejects_location_visible_only_in_page_chrome(self):
        for tag in ("head", "header", "nav", "footer"):
            with self.subTest(tag=tag):
                content = (
                    "<head><title>Platform Engineer - Chicago, IL</title></head>"
                    if tag == "head"
                    else f"<{tag}>Careers in Chicago, IL</{tag}>"
                )
                match, trace, _fetcher = self._match(
                    title="Platform Engineer",
                    target_location="Chicago, IL",
                    detail_html=f"""
                        <html><body>
                          {content}
                          <main>
                            <h1>Platform Engineer</h1>
                            <p>Build the platform used by our customers.</p>
                          </main>
                        </body></html>
                    """,
                )

                self.assertIsNone(match, trace)

    def test_rejects_same_city_with_conflicting_state(self):
        match, trace, _fetcher = self._match(
            title="Platform Engineer",
            target_location="Austin, TX",
            detail_html="""
                <html><body><main>
                  <h1>Platform Engineer</h1>
                  <section><h2>Location</h2><p>Austin, Minnesota</p></section>
                </main></body></html>
            """,
        )

        self.assertIsNone(match, trace)

    def test_rejects_unlabelled_target_city_far_from_heading(self):
        match, trace, _fetcher = self._match(
            title="Platform Engineer",
            target_location="Chicago, IL",
            detail_html=(
                "<html><body><main><h1>Platform Engineer</h1><p>"
                + ("Build reliable distributed systems. " * 30)
                + "Our customers include a team in Chicago."
                + "</p></main></body></html>"
            ),
        )

        self.assertIsNone(match, trace)

    def test_rejects_generic_listing_or_search_url(self):
        for detail_url in (
            "https://careers.example.test/jobs",
            "https://careers.example.test/search?query=platform-engineer",
        ):
            with self.subTest(detail_url=detail_url):
                match, trace, _fetcher = self._match(
                    title="Platform Engineer",
                    target_location="Chicago, IL",
                    detail_url=detail_url,
                    detail_html="""
                        <html><body><main>
                          <h1>Platform Engineer</h1>
                          <p>Chicago, IL</p>
                        </main></body></html>
                    """,
                )

                self.assertIsNone(match, trace)

    def test_rejects_duplicate_matching_h1_titles(self):
        match, trace, _fetcher = self._match(
            title="Platform Engineer",
            target_location="Chicago, IL",
            detail_html="""
                <html><body><main>
                  <h1>Platform Engineer</h1>
                  <p>Chicago, IL</p>
                  <section><h1>Platform Engineer</h1><p>Role details</p></section>
                </main></body></html>
            """,
        )

        self.assertIsNone(match, trace)

    def test_rejects_explicitly_wrong_visible_locations(self):
        cases = (
            ("Project Manager", "Honolulu, HI", "Singapore"),
            ("Project Manager", "Albany, NY", "Eau Claire, WI"),
            ("Project Manager", "Beaumont, TX", "Pittsburgh, PA"),
        )

        for title, target_location, observed_location in cases:
            with self.subTest(
                target_location=target_location,
                observed_location=observed_location,
            ):
                match, trace, _fetcher = self._match(
                    title=title,
                    target_location=target_location,
                    detail_html=f"""
                        <html><body><main>
                          <h1>{title}</h1>
                          <section>
                            <h2>Location</h2>
                            <p>{observed_location}</p>
                          </section>
                        </main></body></html>
                    """,
                )

                self.assertIsNone(match, trace)

    def test_rejects_closed_detail_page(self):
        match, trace, _fetcher = self._match(
            title="Platform Engineer",
            target_location="Chicago, IL",
            detail_html="""
                <html><body><main>
                  <h1>Platform Engineer</h1>
                  <p>Chicago, IL</p>
                  <p>This job is no longer available.</p>
                </main></body></html>
            """,
        )

        self.assertIsNone(match, trace)

    def test_rejects_cross_site_detail_url(self):
        cross_site_url = "https://other.example.net/jobs/platform-engineer-123"
        match, trace, fetcher = self._match(
            title="Platform Engineer",
            target_location="Chicago, IL",
            detail_url=cross_site_url,
            detail_html="""
                <html><body><main>
                  <h1>Platform Engineer</h1>
                  <p>Chicago, IL</p>
                </main></body></html>
            """,
        )

        self.assertIsNone(match, trace)
        self.assertNotIn(cross_site_url, fetcher.calls)


if __name__ == "__main__":
    unittest.main()
