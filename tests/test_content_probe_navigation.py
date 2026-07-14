import unittest

from job_source_agent.content_probe import discover_first_party_career_navigation
from job_source_agent.web import FetchError, Page


class BundleFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append(url)
        try:
            return self.pages[url]
        except KeyError as exc:
            raise FetchError(f"fixture miss: {url}") from exc


class FirstPartyBundleNavigationTests(unittest.TestCase):
    homepage = "https://www.example.com"
    asset = "https://www.example.com/assets/index.js"

    def discover(self, bundle):
        page = Page(
            url=self.homepage,
            final_url=self.homepage,
            html=f'<script type="module" src="{self.asset}"></script>',
        )
        fetcher = BundleFetcher(
            {self.asset: Page(url=self.asset, final_url=self.asset, html=bundle)}
        )
        return discover_first_party_career_navigation(fetcher, page)

    def test_extracts_labeled_anchor_to_same_site_subdomain(self):
        candidates, trace = self.discover(
            '<a href="https://opportunities.example.com">'
            "<span>Job Opportunities</span></a>"
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].url, "https://opportunities.example.com")
        self.assertEqual(candidates[0].text, "Job Opportunities")
        self.assertEqual(candidates[0].origin, "first_party_bundle_navigation")
        self.assertEqual(
            trace["candidate_urls"], ["https://opportunities.example.com"]
        )

    def test_preserves_relative_object_route_contract(self):
        candidates, _ = self.discover(
            'const nav=[{href:"/company/careers",children:"Careers"}];'
        )

        self.assertEqual(
            [candidate.url for candidate in candidates],
            ["https://www.example.com/company/careers"],
        )

    def test_ignores_javascript_and_html_commented_anchors(self):
        candidates, _ = self.discover(
            "\n".join(
                (
                    '// <a href="https://jobs.example.com">Careers</a>',
                    '/* <a href="https://work.example.com">Jobs</a> */',
                    '<!-- <a href="https://join.example.com">Careers</a> -->',
                    '<!-- {href:"/careers",children:"Careers"} -->',
                )
            )
        )

        self.assertEqual(candidates, [])

    def test_rejects_unlabeled_anchor_and_bare_url(self):
        candidates, _ = self.discover(
            '<a href="https://opportunities.example.com">Learn more</a>'
            'const careers="https://jobs.example.com";'
        )

        self.assertEqual(candidates, [])

    def test_rejects_cross_site_and_unsafe_anchor_targets(self):
        unsafe_targets = (
            "https://opportunities.example.net",
            "http://opportunities.example.com",
            "https://user@opportunities.example.com",
            "https://opportunities.example.com:8443",
            "https://opportunities.example.com?source=nav",
            "https://opportunities.example.com#jobs",
            "https://www.example.com/assets/careers.pdf",
        )
        bundle = "".join(
            f'<a href="{target}"><span>Careers</span></a>'
            for target in unsafe_targets
        )

        candidates, _ = self.discover(bundle)

        self.assertEqual(candidates, [])

    def test_rejects_inactive_labeled_anchors(self):
        candidates, _ = self.discover(
            '<a href="https://jobs.example.com" aria-disabled="true">Jobs</a>'
            '<a href="https://work.example.com" disabled>Careers</a>'
            '<a href="https://join.example.com" inert>Join us</a>'
        )

        self.assertEqual(candidates, [])

    def test_fetches_at_most_three_assets_and_scans_five_megabytes_each(self):
        assets = [f"https://www.example.com/assets/{index}.js" for index in range(4)]
        page = Page(
            url=self.homepage,
            html="".join(f'<script src="{asset}"></script>' for asset in assets),
        )
        pages = {
            asset: Page(
                url=asset,
                final_url=asset,
                html=(
                    "x" * 5_000_000
                    + '<a href="https://jobs.example.com">Careers</a>'
                ),
            )
            for asset in assets
        }
        fetcher = BundleFetcher(pages)

        candidates, trace = discover_first_party_career_navigation(fetcher, page)

        self.assertEqual(fetcher.requests, assets[:3])
        self.assertEqual(trace["asset_urls"], assets[:3])
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
