import json
import unittest

from job_source_agent.content_probe import (
    _provider_asset_priority,
    discover_first_party_career_navigation,
    probe_first_party_provider_assets,
)
from job_source_agent.generic_opening_inventory import (
    collect_generic_opening_inventory,
    has_strong_generic_opening_inventory,
)
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


class DynamicInventoryProbeTests(unittest.TestCase):
    def test_extracts_coherent_named_job_destinations_from_public_bundle(self):
        page_url = "https://careers.example.com"
        asset_url = f"{page_url}/assets/index.js"
        greenhouse = "https://job-boards.greenhouse.io/brand"
        bundle = (
            'const brands=[{name:"L\'Occitane Group",url:"https://jobs.example.com"},'
            f'{{name:"Brand",url:"{greenhouse}"}}];'
        )
        page = Page(
            url=page_url,
            final_url=page_url,
            html=f'<script src="{asset_url}"></script>',
        )
        fetcher = BundleFetcher(
            {asset_url: Page(url=asset_url, final_url=asset_url, html=bundle)}
        )

        _enriched, trace = probe_first_party_provider_assets(
            fetcher,
            page,
            lambda url: "greenhouse.io" in url,
        )

        self.assertEqual(
            [(item["label"], item["url"]) for item in trace["job_destinations"]],
            [
                ("L'Occitane Group", "https://jobs.example.com"),
                ("Brand", greenhouse),
            ],
        )

    def probe(self, page_url, asset_url, bundle, endpoint_url, payload):
        page = Page(
            url=page_url,
            final_url=page_url,
            html=f'<script src="{asset_url}"></script>',
            source="fixture",
        )
        fetcher = BundleFetcher(
            {
                asset_url: Page(url=asset_url, final_url=asset_url, html=bundle),
                endpoint_url: Page(
                    url=endpoint_url,
                    final_url=endpoint_url,
                    html=json.dumps(payload),
                    source="fixture_api",
                ),
            }
        )
        enriched, trace = probe_first_party_provider_assets(
            fetcher,
            page,
            lambda _url: False,
            lambda _url: None,
        )
        return enriched, trace, fetcher

    def test_recovers_declared_full_list_get_with_id_detail_route(self):
        page_url = "https://opportunities.example.com"
        asset_url = f"{page_url}/main.abc.bundle.js"
        endpoint_url = f"{page_url}/api/jobs?v=2&f=o"
        bundle = """
            const api = "https://opportunities.example.com/api";
            service.getAll = function(filter) {
              return client.get("/jobs?v=2&f=" + filter);
            };
            service.getAll("o");
            const routes = [{path:"/job/:id"}];
        """
        enriched, trace, fetcher = self.probe(
            page_url,
            asset_url,
            bundle,
            endpoint_url,
            [
                {"id": 4101, "title": "Data Analyst"},
                {"id": 4102, "title": "Financial Analyst"},
            ],
        )

        self.assertEqual(trace["method"], "first_party_declared_inventory")
        self.assertEqual(trace["transport"], "public_same_origin_get")
        self.assertTrue(trace["inventory_complete"])
        self.assertEqual(trace["inventory_count"], 2)
        self.assertEqual(fetcher.requests, [asset_url, endpoint_url])
        self.assertTrue(has_strong_generic_opening_inventory(enriched))
        result = collect_generic_opening_inventory(
            fetcher, enriched, max_pages=2, max_candidates=10
        )
        self.assertEqual(
            [candidate.url for candidate in result.candidates],
            [f"{page_url}/job/4101", f"{page_url}/job/4102"],
        )

    def test_preserves_most_specific_declared_inventory_location(self):
        page_url = "https://opportunities.example.com"
        asset_url = f"{page_url}/main.bundle.js"
        endpoint_url = f"{page_url}/api/jobs?v=2&f=o"
        bundle = f'''
            const api = "{page_url}/api";
            service.getAll = filter => client.get("/jobs?v=2&f=" + filter);
            service.getAll("o");
            const detailBase = "{page_url}/job/";
        '''
        enriched, trace, _fetcher = self.probe(
            page_url,
            asset_url,
            bundle,
            endpoint_url,
            [{
                "id": 458677,
                "title": "Data Analyst",
                "location": "Texas",
                "metro": "Austin, TX",
            }],
        )

        self.assertTrue(trace["inventory_complete"])
        result = collect_generic_opening_inventory(
            BundleFetcher({}), enriched, max_pages=2, max_candidates=10
        )
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].location, "Austin, TX")
        self.assertEqual(result.candidates[0].as_raw_link().location, "Austin, TX")

    def test_recovers_declared_full_list_with_unique_literal_detail_base(self):
        page_url = "https://opportunities.example.com"
        asset_url = f"{page_url}/main.bundle.js"
        endpoint_url = f"{page_url}/api/jobs?v=2&f=o"
        bundle = f'''
            const api = "{page_url}/api";
            service.getAll = filter => client.get("/jobs?v=2&f=" + filter);
            service.getAll("o");
            const detailBase = "{page_url}/job/";
        '''
        enriched, trace, _fetcher = self.probe(
            page_url,
            asset_url,
            bundle,
            endpoint_url,
            [{"id": 458677, "title": "Data Analyst"}],
        )

        self.assertTrue(trace["inventory_complete"])
        result = collect_generic_opening_inventory(
            BundleFetcher({}), enriched, max_pages=2, max_candidates=10
        )
        self.assertEqual(
            [candidate.url for candidate in result.candidates],
            [f"{page_url}/job/458677"],
        )

    def test_declared_detail_template_does_not_apply_navigation_text_penalties(self):
        page_url = "https://opportunities.example.com"
        asset_url = f"{page_url}/main.bundle.js"
        endpoint_url = f"{page_url}/api/jobs?v=2&f=o"
        bundle = f'''
            const api = "{page_url}/api";
            service.getAll = filter => client.get("/jobs?v=2&f=" + filter);
            service.getAll("o");
            const detailBase = "{page_url}/job/";
        '''
        enriched, trace, _fetcher = self.probe(
            page_url,
            asset_url,
            bundle,
            endpoint_url,
            [
                {"id": 458677, "title": "Benefits Manager"},
                {"id": 458678, "title": "Vice President Marketing Fashion"},
            ],
        )

        self.assertTrue(trace["inventory_complete"])
        result = collect_generic_opening_inventory(
            BundleFetcher({}), enriched, max_pages=2, max_candidates=10
        )
        self.assertEqual(
            [candidate.url for candidate in result.candidates],
            [f"{page_url}/job/458677", f"{page_url}/job/458678"],
        )

    def test_rejects_ambiguous_or_cross_origin_literal_detail_bases(self):
        page_url = "https://opportunities.example.com"
        asset_url = f"{page_url}/main.bundle.js"
        endpoint_url = f"{page_url}/api/jobs?v=2&f=o"
        common = f'''
            const api = "{page_url}/api";
            service.getAll = filter => client.get("/jobs?v=2&f=" + filter);
            service.getAll("o");
        '''
        for label, declarations in {
            "ambiguous": (
                f'const a="{page_url}/job/";'
                f'const b="{page_url}/other/job/";'
            ),
            "cross_origin": 'const a="https://other.example/job/";',
        }.items():
            with self.subTest(label=label):
                enriched, trace, _fetcher = self.probe(
                    page_url,
                    asset_url,
                    common + declarations,
                    endpoint_url,
                    [{"id": 458677, "title": "Data Analyst"}],
                )
                self.assertEqual(enriched.html, f'<script src="{asset_url}"></script>')
                self.assertIsNone(trace)

    def test_recovers_complete_jtable_inventory_from_declared_template(self):
        page_url = "https://careers.example.com/search/searchjobs"
        asset_url = "https://careers.example.com/js/JobSearchResultsTable.js"
        endpoint_url = (
            "https://careers.example.com/Search/SearchResults?"
            "jtStartIndex=0&jtPageSize=1000"
        )
        bundle = """
            $("#data").jtable({actions:{listAction: () => load()}});
            ["jtStartIndex", "jtPageSize"].forEach(addParameter);
            return $.ajax({
              url: "/Search/SearchResults?" + params.toString(),
              dataType: "json"
            });
            const detailsUrl = `/search/jobdetails/${title}/${rowId}`;
        """
        enriched, trace, _fetcher = self.probe(
            page_url,
            asset_url,
            bundle,
            endpoint_url,
            {
                "Result": "OK",
                "Records": [
                    {"ID": "a-17", "TrackingObject": {"TitleJson": "Mechanical Engineer I"}},
                    {"ID": "a-18", "TrackingObject": {"TitleJson": "Design Engineer"}},
                ],
                "TotalRecordCount": 2,
            },
        )

        self.assertTrue(trace["inventory_complete"])
        result = collect_generic_opening_inventory(
            BundleFetcher({}), enriched, max_pages=2, max_candidates=10
        )
        self.assertEqual(
            [candidate.url for candidate in result.candidates],
            [
                "https://careers.example.com/search/jobdetails/Mechanical-Engineer-I/a-17",
                "https://careers.example.com/search/jobdetails/Design-Engineer/a-18",
            ],
        )

    def test_jtable_html_title_agrees_with_plain_tracking_title(self):
        page_url = "https://careers.example.com/search/searchjobs"
        asset_url = "https://careers.example.com/js/JobSearchResultsTable.js"
        endpoint_url = (
            "https://careers.example.com/Search/SearchResults?"
            "jtStartIndex=0&jtPageSize=1000"
        )
        bundle = """
            $("#data").jtable({actions:{listAction: () => load()}});
            ["jtStartIndex", "jtPageSize"].forEach(addParameter);
            return $.ajax({
              url: "/Search/SearchResults?" + params.toString(),
              dataType: "json"
            });
            const detailsUrl = `/search/jobdetails/${title}/${rowId}`;
        """
        enriched, trace, _fetcher = self.probe(
            page_url,
            asset_url,
            bundle,
            endpoint_url,
            {
                "Result": "OK",
                "Records": [{
                    "ID": "a-17",
                    "Title": "<span>Mechanical Engineer I</span>",
                    "TrackingObject": {"TitleJson": "Mechanical Engineer I"},
                }],
                "TotalRecordCount": 1,
            },
        )

        self.assertTrue(trace["inventory_complete"])
        self.assertIn("Mechanical-Engineer-I/a-17", enriched.html)

    def test_main_bundle_precedes_generic_libraries_within_asset_budget(self):
        urls = [
            "https://careers.example.com/assets/jquery.js",
            "https://careers.example.com/assets/bootstrap.js",
            "https://careers.example.com/assets/lity.js",
            "https://careers.example.com/vendor.123.bundle.js",
            "https://careers.example.com/main.456.bundle.js",
        ]

        ordered = sorted(urls, key=lambda url: _provider_asset_priority(url, "jobs"))

        self.assertEqual(ordered[0], urls[-1])
        self.assertGreater(
            _provider_asset_priority(urls[0], "jobs")[0],
            _provider_asset_priority(urls[-1], "jobs")[0],
        )

    def test_rejects_truncated_dynamic_inventory(self):
        page_url = "https://careers.example.com/search/searchjobs"
        asset_url = "https://careers.example.com/js/JobSearchResultsTable.js"
        endpoint_url = (
            "https://careers.example.com/Search/SearchResults?"
            "jtStartIndex=0&jtPageSize=1000"
        )
        bundle = """
            $("#data").jtable({actions:{listAction: () => load()}});
            const jtStartIndex = 0, jtPageSize = 12;
            $.ajax({url: "/Search/SearchResults?" + params.toString(), dataType: "json"});
            const detailsUrl = `/search/jobdetails/${title}/${rowId}`;
        """
        enriched, trace, _fetcher = self.probe(
            page_url,
            asset_url,
            bundle,
            endpoint_url,
            {
                "Records": [
                    {"ID": "a-17", "TrackingObject": {"TitleJson": "Mechanical Engineer I"}},
                ],
                "TotalRecordCount": 2,
            },
        )

        self.assertFalse(trace["inventory_complete"])
        self.assertNotIn("data-dynamic-job-inventory", enriched.html)


class FirstPartyBundleNavigationSafetyTests(unittest.TestCase):
    homepage = "https://www.example.com"

    def discover(self, bundle):
        asset = "https://www.example.com/assets/index.js"
        page = Page(
            url=self.homepage,
            final_url=self.homepage,
            html=f'<script type="module" src="{asset}"></script>',
        )
        fetcher = BundleFetcher(
            {asset: Page(url=asset, final_url=asset, html=bundle)}
        )
        return discover_first_party_career_navigation(fetcher, page)

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

    def test_page_chunks_are_not_displaced_by_framework_bundles(self):
        assets = [
            "https://www.example.com/_next/static/chunks/main-framework.js",
            "https://www.example.com/theme/assets/main.js",
            "https://www.example.com/_next/static/chunks/pages/index-route.js",
            "https://www.example.com/_next/static/chunks/951-feature.js",
            "https://www.example.com/_next/static/chunks/pages/_app-shell.js",
        ]
        page = Page(
            url=self.homepage,
            html="".join(f'<script src="{asset}"></script>' for asset in assets),
        )
        pages = {
            asset: Page(url=asset, final_url=asset, html="const noop=true;")
            for asset in assets
        }
        pages[assets[3]] = Page(
            url=assets[3],
            final_url=assets[3],
            html=(
                '<a href="https://opportunities.example.com">'
                "Job Opportunities</a>"
            ),
        )
        fetcher = BundleFetcher(pages)

        candidates, trace = discover_first_party_career_navigation(fetcher, page)

        self.assertIn(assets[3], trace["asset_urls"])
        self.assertEqual(
            [candidate.url for candidate in candidates],
            ["https://opportunities.example.com"],
        )


if __name__ == "__main__":
    unittest.main()
