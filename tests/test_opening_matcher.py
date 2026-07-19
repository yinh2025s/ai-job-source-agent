import unittest
import json
from pathlib import Path

from job_source_agent.browser_interaction import JobSearchInteraction
from job_source_agent.opening_matcher import (
    JobOpeningMatcher,
    build_provider_api_urls,
    build_provider_search_urls,
    build_search_form_urls,
    detect_provider,
    _opening_candidates_from_links,
    _is_explicit_location_mismatch,
    score_title_match,
    title_identity_matches,
    structured_job_links,
)
from job_source_agent.listing_extraction import (
    explicit_empty_inventory_evidence,
    extract_listing_candidates,
    validate_output_url,
)
from job_source_agent.opening_availability import diagnose_opening_availability
from job_source_agent.web import FetchError, Fetcher, Page, RawLink
from job_source_agent.job_board import DiscoveredJobBoard
from job_source_agent.providers.base import AdapterResult, JobBoard, JobCandidate
from job_source_agent.providers.registry import ProviderRegistry
from job_source_agent.rendered_fetcher import FORCE_RENDER_HEADER


ROOT = Path(__file__).resolve().parents[1]


class OpeningMatcherTests(unittest.TestCase):
    def test_explicit_empty_inventory_accepts_right_now_question(self):
        phrase = explicit_empty_inventory_evidence(
            "<main><h2>No open positions right now?</h2></main>"
        )

        self.assertEqual(phrase, "No open positions right now")

    def test_declared_inventory_rejects_explicit_foreign_location_before_s7(self):
        links = [
            RawLink(
                "https://careers.example.com/job/project_manager/scotland/42/",
                "Project Manager",
                "https://careers.example.com/jobs",
                origin="verified_declared_inventory",
                location="Scotland",
            ),
            RawLink(
                "https://careers.example.com/job/project_manager/ohio/43/",
                "Project Manager",
                "https://careers.example.com/jobs",
                origin="verified_declared_inventory",
                location="Toledo, OH",
            ),
        ]

        candidates = _opening_candidates_from_links(
            links,
            page_url="https://careers.example.com/jobs",
            target_title="Project Manager",
            target_location="Toledo, OH",
            provider="generic",
        )

        self.assertEqual([item.url for item in candidates], [links[1].url])

    def test_declared_inventory_uses_explicit_title_city_qualifier_not_broad_region(self):
        links = [
            RawLink(
                "https://careers.example.com/apply/offer/NYC123",
                "Account Executive, NYC",
                "https://careers.example.com/jobs",
                origin="verified_declared_inventory",
                location="Americas",
            ),
            RawLink(
                "https://careers.example.com/apply/offer/DC456",
                "Account Executive, D.C.",
                "https://careers.example.com/jobs",
                origin="verified_declared_inventory",
                location="Americas",
            ),
        ]

        candidates = _opening_candidates_from_links(
            links,
            page_url="https://careers.example.com/jobs",
            target_title="Account Executive",
            target_location="New York, NY",
            provider="generic",
        )

        self.assertEqual([item.url for item in candidates], [links[0].url])

    def test_verified_generic_handoff_forces_job_board_render(self):
        board_url = "https://opaque-hiring.example/jobs"

        class HeaderFetcher:
            def __init__(self):
                self.calls = []

            def fetch(self, url, data=None, headers=None, **kwargs):
                self.calls.append((url, headers))
                if url == board_url:
                    return Page(url, "<html><main>Search jobs</main></html>")
                raise FetchError(f"unexpected URL: {url}")

        fetcher = HeaderFetcher()
        discovered = DiscoveredJobBoard(
            JobBoard(board_url, "generic"),
            "verified_first_party_action",
            board_url,
            relationship_evidence_url="https://acme.example/careers",
        )

        JobOpeningMatcher(fetcher).match(
            board_url,
            "Registered Nurse",
            discovered_board=discovered,
        )

        self.assertEqual(
            fetcher.calls[0],
            (board_url, {FORCE_RENDER_HEADER: "force"}),
        )

    def test_same_site_search_lead_requires_verified_jobposting_page(self):
        board_url = "https://jobs.acme.example/"
        detail_url = "https://jobs.acme.example/job-3/123/platform-engineer/"
        posting = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "title": "Platform Engineer",
                "url": detail_url,
                "jobLocation": {
                    "@type": "Place",
                    "address": {
                        "addressLocality": "Denver",
                        "addressRegion": "CO",
                    },
                },
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": "Acme",
                    "url": "https://jobs.acme.example/",
                },
            }
        )

        class SearchFetcher:
            def __init__(self):
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url == board_url:
                    return Page(url, "<html><main>Careers</main></html>")
                if "bing.com/search" in url:
                    return Page(
                        url,
                        f"<rss><channel><item><link>{detail_url}</link></item></channel></rss>",
                    )
                if url == detail_url:
                    return Page(
                        url,
                        f'<script type="application/ld+json">{posting}</script>',
                    )
                raise FetchError(f"unexpected URL: {url}")

        fetcher = SearchFetcher()
        match, trace = JobOpeningMatcher(fetcher).match(
            board_url,
            "Platform Engineer",
            "Denver, CO",
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, detail_url)
        self.assertEqual(match.location, "Denver, CO")
        self.assertEqual(len(trace["verified_site_search"]["verified_pages"]), 1)
        self.assertEqual(fetcher.calls[-1], detail_url)

    def test_same_site_search_verifies_jobposting_on_sibling_subdomain(self):
        board_url = "https://careers.acme.example/search"
        detail_url = "https://www.acme.example/talent/job-offers/platform-engineer/"
        posting = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "title": "Platform Engineer",
                "url": detail_url,
                "jobLocation": {
                    "@type": "Place",
                    "address": {
                        "addressLocality": "Denver",
                        "addressRegion": "CO",
                    },
                },
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": "Acme",
                    "url": "https://www.acme.example/",
                },
            }
        )

        class SearchFetcher:
            def fetch(self, url, data=None, headers=None):
                if "bing.com/search" in url:
                    return Page(
                        url,
                        f"<rss><channel><item><link>{detail_url}</link></item></channel></rss>",
                    )
                if url == board_url or "?q=" in url or "?search=" in url:
                    return Page(url, "<main>Careers</main>")
                if url == detail_url:
                    return Page(
                        url,
                        f'<script type="application/ld+json">{posting}</script>',
                    )
                raise FetchError(f"unexpected URL: {url}")

        match, trace = JobOpeningMatcher(SearchFetcher()).match(
            board_url,
            "Platform Engineer",
            "Denver, CO",
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, detail_url)
        self.assertEqual(len(trace["verified_site_search"]["verified_pages"]), 1)

    def test_same_site_search_rejects_wrong_employer_and_location_then_ranks_valid_page(self):
        board_url = "https://jobs.acme.example/"
        wrong_employer = "https://jobs.acme.example/jobs/1/platform-engineer"
        wrong_location = "https://jobs.acme.example/jobs/2/platform-engineer"
        valid = "https://jobs.acme.example/jobs/3/platform-engineer"

        def posting(url, location, organization_url):
            return json.dumps(
                {
                    "@context": "https://schema.org",
                    "@type": "JobPosting",
                    "title": "Platform Engineer",
                    "url": url,
                    "jobLocation": {
                        "@type": "Place",
                        "address": {
                            "addressLocality": location.split(",")[0],
                            "addressRegion": location.split(",")[1].strip(),
                        },
                    },
                    "hiringOrganization": {
                        "@type": "Organization",
                        "name": "Acme" if "acme" in organization_url else "Other",
                        "url": organization_url,
                    },
                }
            )

        pages = {
            wrong_employer: posting(wrong_employer, "Denver, CO", "https://other.example/"),
            wrong_location: posting(wrong_location, "Boulder, CO", board_url),
            valid: posting(valid, "Denver, CO", board_url),
        }

        class SearchFetcher:
            def fetch(self, url, data=None, headers=None):
                if url == board_url:
                    return Page(url, "<main>Careers</main>")
                if "bing.com/search" in url:
                    links = "".join(f"<item><link>{item}</link></item>" for item in pages)
                    return Page(url, f"<rss><channel>{links}</channel></rss>")
                if url in pages:
                    return Page(url, f'<script type="application/ld+json">{pages[url]}</script>')
                raise FetchError(f"unexpected URL: {url}")

        match, trace = JobOpeningMatcher(SearchFetcher()).match(
            board_url,
            "Platform Engineer",
            "Denver, CO",
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.url, valid)
        self.assertEqual(match.hiring_organization_name, "Acme")
        reasons = {item["reason"] for item in trace["verified_site_search"]["rejected_pages"]}
        self.assertIn("hiring_organization_not_first_party", reasons)
        self.assertIn("location_identity_mismatch", reasons)

    def test_same_site_search_snippet_without_jobposting_is_not_success(self):
        board_url = "https://jobs.acme.example/"
        detail_url = "https://jobs.acme.example/jobs/123/platform-engineer"

        class SearchFetcher:
            def fetch(self, url, data=None, headers=None):
                if "bing.com/search" in url:
                    return Page(
                        url,
                        f"<rss><channel><item><link>{detail_url}</link></item></channel></rss>",
                    )
                if url == detail_url:
                    return Page(url, "<h1>Platform Engineer</h1>")
                return Page(url, "<html></html>")

        match, trace = JobOpeningMatcher(SearchFetcher()).match(
            board_url,
            "Platform Engineer",
        )

        self.assertIsNone(match)
        self.assertEqual(
            trace["verified_site_search"]["rejected_pages"][0]["reason"],
            "jobposting_identity_not_verified",
        )

    def test_declared_search_route_is_followed_as_inventory_not_success_evidence(self):
        board_url = "https://jobs.example.com/jobs/"
        helper_url = (
            "https://jobs.example.com/api/search/get-search-results"
            "?query=Data+Analyst&text=Data+Analyst"
        )
        route_url = "https://jobs.example.com/jobs/q-data-analyst/"
        detail_url = "https://jobs.example.com/jobs/123/data-analyst"

        class SearchFetcher:
            def fetch(self, url, data=None, headers=None):
                if url == board_url:
                    return Page(
                        url,
                        """<script>
                        api.get(`/api/search/get-search-results?query=${title}&text=${title}`);
                        </script>""",
                    )
                if url == helper_url:
                    self.assertEqual(headers, {"Accept": "application/json"})
                    return Page(url, json.dumps({"searchUrl": "/jobs/q-data-analyst/"}))
                if url == route_url:
                    return Page(
                        url,
                        f'<a href="{detail_url}">Data Analyst</a>',
                    )
                return Page(url, "<main>No matching inventory</main>")

            def assertEqual(self, left, right):
                if left != right:
                    raise AssertionError((left, right))

        match, trace = JobOpeningMatcher(SearchFetcher()).match(
            board_url,
            "Data Analyst",
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, detail_url)
        self.assertEqual(trace["declared_search_route"]["status"], "resolved")
        self.assertIn(
            {"url": route_url, "source": "declared_search_route",
             "query": "Data Analyst", "query_source": "full_title"},
            trace["search_plan"],
        )

    def test_verified_declared_inventory_accepts_title_bound_jobdetails_uuid(self):
        url = (
            "https://careers.example.com/search/jobdetails/"
            "mechanical-engineer-i/68b78236-9379-46ed-a166-deb3c5213645"
        )
        links = [
            RawLink(
                url,
                "Mechanical Engineer I",
                "https://careers.example.com/search/searchjobs",
                origin="verified_declared_inventory",
                location="Kennesaw, Georgia",
            )
        ]

        candidates = _opening_candidates_from_links(
            links,
            page_url="https://careers.example.com/search/searchjobs",
            target_title="Mechanical Engineer I",
            target_location="Kennesaw, GA",
            provider="generic",
        )

        self.assertEqual([item.url for item in candidates], [url])

    def test_uses_declared_anonymous_js_post_inventory_after_html_inventory(self):
        job_list_url = "https://careers.example.com/search"
        asset_url = "https://careers.example.com/assets/job-search.js"
        endpoint_url = "https://careers.example.com/bin/public/jobs"
        job_url = "https://careers.example.com/jobs/42/ai-engineer"

        class RecordingFetcher:
            def __init__(self):
                self.requests = []

            def fetch(self, url, data=None, headers=None):
                self.requests.append((url, data, headers))
                if url == job_list_url:
                    return Page(url, f'<script src="{asset_url}"></script>')
                if url == asset_url:
                    return Page(
                        url,
                        '''
                        const pageLimit = 25;
                        $.ajax({
                            url: "/bin/public/jobs",
                            type: "POST",
                            data: {
                                searchMode: "search",
                                searchTerm: requestedTitle,
                                paginationStart: 0,
                                paginationLimit: pageLimit
                            },
                            success: response => render(response.jobPostings)
                        });
                        ''',
                    )
                if url == endpoint_url:
                    self.assert_request(data, headers)
                    return Page(
                        url,
                        json.dumps({"jobPostings": [{
                            "title": "AI Engineer",
                            "location": "Austin, TX",
                            "url": job_url,
                        }]}),
                    )
                raise FetchError(f"unexpected URL: {url}")

            @staticmethod
            def assert_request(data, headers):
                assert b"searchTerm=AI+Engineer" in data
                assert "Content-Type" in headers

        match, trace = JobOpeningMatcher(RecordingFetcher()).match(
            job_list_url,
            "AI Engineer",
            "Austin, TX",
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, job_url)
        self.assertEqual(match.location, "Austin, TX")
        self.assertEqual(trace["js_declared_inventory"][0]["status"], "verified")

    def test_rebuilds_declared_get_inventory_without_runtime_board_handoff(self):
        job_list_url = "https://opportunities.example.com"
        asset_url = f"{job_list_url}/main.bundle.js"
        endpoint_url = f"{job_list_url}/api/jobs?v=2&f=o"
        target_url = f"{job_list_url}/job/458677"

        class RecordingFetcher:
            def __init__(self):
                self.requests = []

            def fetch(self, url, data=None, headers=None):
                self.requests.append(url)
                if url == job_list_url:
                    return Page(url, f'<script src="{asset_url}"></script>')
                if url == asset_url:
                    return Page(
                        url,
                        f'''
                        const api = "{job_list_url}/api";
                        service.getAll = filter => client.get("/jobs?v=2&f=" + filter);
                        service.getAll("o");
                        const detailBase = "{job_list_url}/job/";
                        ''',
                    )
                if url == endpoint_url:
                    return Page(
                        url,
                        json.dumps([
                            {"id": 458677, "title": "Data Analyst"},
                            {"id": 458678, "title": "Benefits Manager"},
                        ]),
                    )
                raise FetchError(f"unexpected URL: {url}")

        match, trace = JobOpeningMatcher(RecordingFetcher()).match(
            job_list_url,
            "Data Analyst",
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, target_url)
        self.assertEqual(
            trace["provider_api"]["provider_detection"]["method"],
            "verified_declared_inventory",
        )
        self.assertEqual(
            trace["provider_api"]["provider_detection"]["inventory_count"],
            2,
        )

    def test_empty_complete_declared_title_inventory_is_a_verified_no_match(self):
        job_list_url = "https://careers.example.com/jobs"
        asset_url = "https://careers.example.com/assets/job-search.js"
        endpoint_url = "https://careers.example.com/bin/public/jobs"

        class RecordingFetcher:
            def fetch(self, url, data=None, headers=None):
                if url == job_list_url:
                    return Page(url, f'<script src="{asset_url}"></script>')
                if url == asset_url:
                    return Page(
                        url,
                        '''
                        const pageLimit = 25;
                        $.ajax({
                            url: "/bin/public/jobs", type: "POST",
                            data: {searchMode: "search", searchTerm: requestedTitle,
                                   paginationStart: 0, paginationLimit: pageLimit},
                            success: response => render(response.jobPostings)
                        });
                        ''',
                    )
                if url == endpoint_url:
                    return Page(url, json.dumps({"jobPostings": []}))
                raise FetchError(f"unexpected URL: {url}")

        match, trace = JobOpeningMatcher(RecordingFetcher()).match(
            job_list_url,
            "Missing Engineer",
        )

        self.assertIsNone(match)
        self.assertEqual(
            trace["provider_api"]["inventory"]["status"],
            "verified_filtered_empty",
        )
        diagnostic = diagnose_opening_availability(trace)
        self.assertEqual(diagnostic.reason_code, "OPENING_NOT_FOUND")

    def test_filtered_svelte_inventory_rejects_same_title_wrong_location(self):
        job_list_url = (
            "https://block.example/careers/jobs?businessUnits%5B%5D=square"
        )
        target_title = "SMB Account Executive"
        payload = (
            '<script type="application/json">{type:"data",data:{jobs:'
            '{currentPage:['
            '{id:5282973008,title:"SMB Account Executive",bu:"square",'
            'location:"Bay Area, CA, US"},'
            '{id:5287754008,title:"SMB Account Executive - Canada",bu:"square",'
            'location:"Toronto, Ontario, Canada"}'
            '],total:2},initialJobsListRequest:{page:1,pageLimit:50,'
            'query:"SMB Account Executive",businessUnits:["square"]}}}</script>'
        )

        class RecordingFetcher:
            def __init__(self):
                self.requested = []

            def fetch(self, url, data=None, headers=None):
                self.requested.append(url)
                if "query=SMB+Account+Executive" in url:
                    return Page(url=url, html=payload)
                return Page(url=url, html="<main>Search jobs</main>")

        fetcher = RecordingFetcher()
        match, trace = JobOpeningMatcher(fetcher).match(
            job_list_url,
            target_title,
            "New York, NY",
        )

        self.assertIsNone(match)
        self.assertTrue(any("query=SMB+Account+Executive" in url for url in fetcher.requested))
        self.assertEqual(trace["provider_api"]["inventory"]["scope"], "filtered")
        self.assertEqual(trace["provider_api"]["inventory"]["status"], "verified")
        self.assertTrue(trace["provider_api"]["inventory"]["complete"])
        self.assertEqual(trace["provider_api"]["inventory"]["candidate_count"], 2)
        diagnostic = diagnose_opening_availability(trace)
        self.assertEqual(diagnostic.reason_code, "OPENING_NOT_FOUND")

    def test_generic_inventory_does_not_fetch_next_page_when_initial_page_matches(self):
        job_list_url = "https://careers.example.com/jobs"
        second_url = job_list_url + "?page=2"
        job_url = "https://careers.example.com/jobs/42/ai-engineer"

        class RecordingFetcher:
            def __init__(self):
                self.requested = []

            def fetch(self, url, data=None, headers=None):
                self.requested.append(url)
                if url != job_list_url:
                    raise FetchError(f"unexpected URL: {url}")
                return Page(
                    url=url,
                    html=(
                        '<article class="job-card"><h3>AI Engineer</h3>'
                        f'<a href="{job_url}">View job</a></article>'
                        f'<a href="{second_url}">Next page</a>'
                    ),
                )

        fetcher = RecordingFetcher()
        match, trace = JobOpeningMatcher(fetcher).match(job_list_url, "AI Engineer")

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, job_url)
        self.assertEqual(fetcher.requested, [job_list_url])
        self.assertNotIn("generic_inventory", trace)

    def test_structured_same_title_candidates_are_ranked_by_location(self):
        job_list_url = "https://careers.example.com/jobs"
        oxnard_url = "https://recruiting.paylocity.com/Recruiting/Jobs/Details/4324729"
        anaheim_url = "https://recruiting.paylocity.com/Recruiting/Jobs/Details/4336893"
        payload = json.dumps(
            {
                "first_party_declared_inventory": {
                    "endpoint_url": "https://inventory.example.net/api/jobs",
                    "jobs": [
                        {
                            "title": "Registered Nurse (RN) - Full-Time",
                            "location": "Anaheim Hills, CA",
                            "url": anaheim_url,
                        },
                        {
                            "title": "Registered Nurse (RN) - Full-Time",
                            "location": "Oxnard, CA",
                            "url": oxnard_url,
                        },
                    ],
                }
            }
        )

        class StaticFetcher:
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    html=f'<script type="application/json">{payload}</script>',
                    source="fixture|first_party_declared_inventory",
                )

        match, trace = JobOpeningMatcher(StaticFetcher()).match(
            job_list_url,
            "Registered Nurse (RN) - Full-Time",
            "Oxnard, CA",
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, oxnard_url)
        self.assertEqual(match.location, "Oxnard, CA")
        self.assertEqual(trace["selected"]["location"], "Oxnard, CA")

    def test_generic_exact_link_enriches_location_from_same_site_jobposting_detail(self):
        job_list_url = "https://careers.example.com/jobs"
        detail_url = "https://careers.example.com/job?id=R0045464"
        posting = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "title": "Product Design Engineer",
                "url": detail_url,
                "jobLocation": {
                    "@type": "Place",
                    "address": {
                        "addressLocality": "Los Angeles",
                        "addressRegion": "CA",
                    },
                },
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": "Example",
                    "url": "https://careers.example.com/",
                },
            }
        )

        class DetailFetcher:
            def __init__(self):
                self.requested = []

            def fetch(self, url, data=None, headers=None):
                self.requested.append(url)
                if url == job_list_url:
                    return Page(
                        url,
                        f'<a href="{detail_url}">Product Design Engineer</a>',
                    )
                if url == detail_url:
                    return Page(
                        url,
                        f'<script type="application/ld+json">{posting}</script>',
                    )
                raise FetchError(f"unexpected URL: {url}")

        fetcher = DetailFetcher()
        match, trace = JobOpeningMatcher(fetcher).match(
            job_list_url,
            "Product Design Engineer",
            "Los Angeles, CA",
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, detail_url)
        self.assertEqual(match.location, "Los Angeles, CA")
        self.assertIn("verified same-site JobPosting detail", match.reasons)
        self.assertEqual(trace["detail_enrichment"]["verified_count"], 1)
        self.assertEqual(fetcher.requested[:2], [job_list_url, detail_url])

    def test_generic_listing_continuity_accepts_jobposting_without_organization_url(self):
        job_list_url = "https://careers.example.com/jobs"
        detail_url = "https://careers.example.com/jobs/42/platform-engineer"
        posting = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "title": "Platform Engineer",
                "url": detail_url,
                "jobLocation": {
                    "@type": "Place",
                    "address": {"addressLocality": "New York"},
                },
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": "Confidential Client",
                },
            }
        )

        class DetailFetcher:
            def fetch(self, url, data=None, headers=None):
                if url == job_list_url:
                    return Page(url, f'<a href="{detail_url}">Platform Engineer</a>')
                if url == detail_url:
                    return Page(url, f'<script type="application/ld+json">{posting}</script>')
                raise FetchError(f"unexpected URL: {url}")

        match, trace = JobOpeningMatcher(DetailFetcher()).match(
            job_list_url,
            "Platform Engineer",
            "New York, NY",
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, detail_url)
        self.assertEqual(match.location, "New York")
        self.assertEqual(trace["detail_enrichment"]["verified_count"], 1)

    def test_generic_listing_continuity_rejects_foreign_organization_url(self):
        job_list_url = "https://careers.example.com/jobs"
        detail_url = "https://careers.example.com/jobs/42/platform-engineer"
        posting = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "title": "Platform Engineer",
                "url": detail_url,
                "jobLocation": {
                    "@type": "Place",
                    "address": {
                        "addressLocality": "New York",
                        "addressRegion": "NY",
                    },
                },
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": "Foreign Employer",
                    "url": "https://jobs.foreign.example/",
                },
            }
        )

        class DetailFetcher:
            def fetch(self, url, data=None, headers=None):
                if url == job_list_url:
                    return Page(url, f'<a href="{detail_url}">Platform Engineer</a>')
                if url == detail_url:
                    return Page(url, f'<script type="application/ld+json">{posting}</script>')
                return Page(url, "<main>No matching jobs</main>")

        match, trace = JobOpeningMatcher(DetailFetcher()).match(
            job_list_url,
            "Platform Engineer",
            "New York, NY",
        )

        self.assertIsNone(match)
        self.assertEqual(trace["detail_enrichment"]["verified_count"], 0)

    def test_generic_opaque_detail_uses_page_bound_hydration_location(self):
        job_list_url = "https://careers.example.com/job-search"
        detail_url = "https://careers.example.com/job-search/abc123def456"
        record = {
            "props": {
                "job": {
                    "wdId": "abc123def456",
                    "title": "National Account Manager - Hotels",
                    "locations": [
                        {"city": "Chicago, IL"},
                        {"city": "New York, NY"},
                    ],
                }
            }
        }
        frame = "9:" + json.dumps(record, separators=(",", ":")) + "\n"
        detail_html = (
            "<script>self.__next_f.push("
            + json.dumps([1, frame], separators=(",", ":"))
            + ")</script>"
        )

        class DetailFetcher:
            def fetch(self, url, data=None, headers=None):
                if url == job_list_url:
                    return Page(
                        url,
                        f'<a href="{detail_url}">National Account Manager - Hotels</a>',
                    )
                if url == detail_url:
                    return Page(url, detail_html)
                return Page(url, "<main>No matches</main>")

        match, trace = JobOpeningMatcher(DetailFetcher()).match(
            job_list_url,
            "National Account Manager - Hotels",
            "New York, NY",
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, detail_url)
        self.assertEqual(match.location, "Chicago, IL; New York, NY")
        self.assertEqual(trace["detail_enrichment"]["verified_count"], 1)

    def test_generic_remote_detail_requires_exact_country_evidence(self):
        job_list_url = "https://careers.example.com/jobs"
        detail_url = "https://careers.example.com/jobs/42/remote-nurse"

        def posting(description):
            return json.dumps(
                {
                    "@context": "https://schema.org",
                    "@type": "JobPosting",
                    "title": "Remote Nurse",
                    "url": detail_url,
                    "jobLocationType": "TELECOMMUTE",
                    "description": description,
                    "hiringOrganization": {"name": "Confidential Client"},
                }
            )

        class DetailFetcher:
            def __init__(self, description):
                self.description = description

            def fetch(self, url, data=None, headers=None):
                if url == job_list_url:
                    return Page(url, f'<a href="{detail_url}">Remote Nurse</a>')
                return Page(
                    url,
                    '<script type="application/ld+json">'
                    + posting(self.description)
                    + "</script>",
                )

        positive, _ = JobOpeningMatcher(
            DetailFetcher("<p>Applicants must be based in the United States.</p>")
        ).match(job_list_url, "Remote Nurse", "United States")
        wrong_country, _ = JobOpeningMatcher(
            DetailFetcher("<p>Applicants must be based in Canada.</p>")
        ).match(job_list_url, "Remote Nurse", "United States")
        broad_for_city, _ = JobOpeningMatcher(
            DetailFetcher("<p>Applicants must be based in the United States.</p>")
        ).match(job_list_url, "Remote Nurse", "New York, NY")

        self.assertIsNotNone(positive)
        self.assertIsNone(wrong_country)
        self.assertIsNone(broad_for_city)

    def test_generic_detail_enrichment_rejects_wrong_location(self):
        job_list_url = "https://careers.example.com/jobs"
        detail_url = "https://careers.example.com/jobs/42/data-analyst"
        posting = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "title": "Data Analyst",
                "url": detail_url,
                "jobLocation": {
                    "@type": "Place",
                    "address": {
                        "addressLocality": "Lake Forest",
                        "addressRegion": "IL",
                    },
                },
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": "Example",
                    "url": "https://careers.example.com/",
                },
            }
        )

        class DetailFetcher:
            def fetch(self, url, data=None, headers=None):
                if url == job_list_url:
                    return Page(url, f'<a href="{detail_url}">Data Analyst</a>')
                if url == detail_url:
                    return Page(
                        url,
                        f'<script type="application/ld+json">{posting}</script>',
                    )
                raise FetchError(f"unexpected URL: {url}")

        match, trace = JobOpeningMatcher(DetailFetcher()).match(
            job_list_url,
            "Data Analyst",
            "Malvern, PA",
        )

        self.assertIsNone(match)
        self.assertEqual(trace["detail_enrichment"]["verified_count"], 0)
        self.assertEqual(
            trace["detail_enrichment"]["attempts"][0]["status"],
            "jobposting_identity_not_verified",
        )
        self.assertEqual(
            trace["location_unverified_candidate_rejected"]["url"],
            detail_url,
        )

    def test_generic_broad_location_does_not_satisfy_specific_target(self):
        job_list_url = "https://careers.example.com/jobs"
        detail_url = "https://careers.example.com/jobs/42/data-analyst"

        class DetailFetcher:
            def fetch(self, url, data=None, headers=None):
                if url == job_list_url:
                    return Page(
                        url,
                        (
                            f'<a class="job-card" href="{detail_url}">'
                            '<h3>Data Analyst</h3>'
                            '<span class="job-location">United States</span>'
                            '</a>'
                        ),
                    )
                return Page(url, "<main>Job detail without structured location</main>")

        match, trace = JobOpeningMatcher(DetailFetcher()).match(
            job_list_url,
            "Data Analyst",
            "Greater Tampa Bay Area",
        )

        self.assertIsNone(match)
        self.assertEqual(
            trace["location_unverified_candidate_rejected"],
            {
                "url": detail_url,
                "candidate_location": "United States",
                "target_location": "Greater Tampa Bay Area",
                "reason": "generic candidate location was broader than the target",
            },
        )

    def test_generic_detail_enrichment_reads_page_bound_controller_cache(self):
        job_list_url = "https://careers.example.com/jobs"
        taipei_url = "https://careers.example.com/job?id=R0045464"
        los_angeles_url = "https://careers.example.com/job?id=R0046024"

        def detail_html(job_id, location):
            payload = {
                f"job-{job_id}": {
                    "data": {
                        "body": {
                            "id": job_id,
                            "title": "Product Design Engineer",
                            "jobPostingSite": "Example Inc.",
                            "offices": [{"location": location}],
                        }
                    }
                }
            }
            return (
                "<script>window.ASYNC_DATA_CONTROLLER_CACHE = "
                + json.dumps(payload)
                + ";</script>"
            )

        class DetailFetcher:
            def fetch(self, url, data=None, headers=None):
                if url == job_list_url:
                    return Page(
                        url,
                        (
                            f'<a href="{taipei_url}">Product Design Engineer</a>'
                            f'<a href="{los_angeles_url}">Product Design Engineer</a>'
                        ),
                    )
                if url == taipei_url:
                    return Page(url, detail_html("R0045464", "Taipei City, Taiwan"))
                if url == los_angeles_url:
                    return Page(url, detail_html("R0046024", "Los Angeles, California"))
                raise FetchError(f"unexpected URL: {url}")

        match, trace = JobOpeningMatcher(DetailFetcher()).match(
            job_list_url,
            "Product Design Engineer",
            "Los Angeles, CA",
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, los_angeles_url)
        self.assertEqual(match.location, "Los Angeles, California")
        self.assertEqual(trace["detail_enrichment"]["verified_count"], 1)
        self.assertEqual(
            [item["status"] for item in trace["detail_enrichment"]["attempts"]],
            ["jobposting_identity_not_verified", "verified"],
        )

    def test_generic_inventory_follows_bounded_next_page_to_exact_opening(self):
        job_list_url = "https://careers.example.com/jobs"
        second_url = job_list_url + "?page=2"
        job_url = "https://careers.example.com/jobs/42/ai-engineer"

        class MappingFetcher:
            def __init__(self):
                self.requested = []

            def fetch(self, url, data=None, headers=None):
                self.requested.append(url)
                if url == job_list_url:
                    return Page(
                        url=url,
                        html=f'<a href="{second_url}">Next page</a>',
                    )
                if url == second_url:
                    return Page(
                        url=url,
                        html=(
                            '<article class="job-card"><h3>AI Engineer</h3>'
                            f'<a href="{job_url}">View job</a></article>'
                        ),
                    )
                raise FetchError(f"unexpected URL: {url}")

        fetcher = MappingFetcher()
        match, trace = JobOpeningMatcher(fetcher).match(
            job_list_url,
            "AI Engineer",
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, job_url)
        self.assertEqual(fetcher.requested, [job_list_url, second_url])
        self.assertTrue(trace["provider_api"]["inventory"]["complete"])
        self.assertEqual(trace["generic_inventory"][0]["pages_fetched"], 2)

    def test_generic_inventory_uses_configured_page_budget_beyond_three_pages(self):
        job_list_url = "https://careers.example.com/jobs"
        job_url = "https://careers.example.com/jobs/42/ai-engineer"

        class MappingFetcher:
            def __init__(self):
                self.requested = []

            def fetch(self, url, data=None, headers=None):
                self.requested.append(url)
                page = 1 if "page=" not in url else int(url.rsplit("=", 1)[1])
                if page < 4:
                    next_url = f"{job_list_url}?page={page + 1}"
                    return Page(url=url, html=f'<a href="{next_url}">Next page</a>')
                if page == 4:
                    return Page(
                        url=url,
                        html=(
                            '<article class="job-card"><h3>AI Engineer</h3>'
                            f'<a href="{job_url}">View job</a></article>'
                        ),
                    )
                raise FetchError(f"unexpected URL: {url}")

        fetcher = MappingFetcher()
        match, trace = JobOpeningMatcher(
            fetcher,
            max_generic_job_pages=8,
        ).match(job_list_url, "AI Engineer")

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, job_url)
        self.assertEqual(
            fetcher.requested,
            [
                job_list_url,
                f"{job_list_url}?page=2",
                f"{job_list_url}?page=3",
                f"{job_list_url}?page=4",
            ],
        )
        self.assertEqual(trace["generic_inventory"][0]["pages_fetched"], 4)

    def test_single_generic_page_does_not_claim_complete_inventory(self):
        job_list_url = "https://careers.example.com/jobs"

        class StaticFetcher:
            def fetch(self, url, data=None, headers=None):
                return Page(url=url, html="<main>Open roles</main>")

        _match, trace = JobOpeningMatcher(StaticFetcher()).match(
            job_list_url,
            "AI Engineer",
        )

        self.assertNotIn("inventory", trace["provider_api"])
        self.assertEqual(
            trace["generic_inventory"][0]["stop_reason"],
            "single_page_unbounded",
        )

    def test_first_party_all_jobs_numeric_route_matches_exact_opening(self):
        job_list_url = "https://careers.example.com/en/all-jobs/"
        job_url = job_list_url + "8036603/product-manager/?gh_jid=8036603"

        class StaticFetcher:
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=job_list_url,
                    html=f'<a href="{job_url}">Product Manager</a>',
                    source="fixture",
                )

        match, _trace = JobOpeningMatcher(StaticFetcher()).match(
            job_list_url,
            "Product Manager",
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.url, job_url)

    def test_generic_same_page_job_query_matches_exact_opening(self):
        job_list_url = "https://zello.com/careers/"
        job_url = (
            job_list_url
            + "job/?jid=f8f40e9f-4c49-4a3d-9d89-750fc2409835"
        )

        class StaticFetcher:
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=job_list_url,
                    html=f'<a href="{job_url}">Machine Learning Engineer</a>',
                    source="fixture",
                )

        matcher = JobOpeningMatcher(StaticFetcher())

        match, _trace = matcher.match(job_list_url, "Machine Learning Engineer")
        missing, missing_trace = matcher.match(job_list_url, "Quantum Archaeologist")

        self.assertIsNotNone(match)
        self.assertEqual(match.url, job_url)
        self.assertIsNone(missing)
        self.assertNotIn("inventory", missing_trace["provider_api"])

    def test_generic_sibling_job_query_matches_exact_opening(self):
        job_list_url = "https://careers.example.com/jobs"
        job_url = "https://careers.example.com/job?id=R0046024"

        class StaticFetcher:
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=job_list_url,
                    html=f'<a href="{job_url}">Product Design Engineer</a>',
                    source="fixture",
                )

        match, _trace = JobOpeningMatcher(StaticFetcher()).match(
            job_list_url,
            "Product Design Engineer",
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.url, job_url)

    def test_generic_opaque_job_child_matches_title_with_card_metadata(self):
        job_list_url = "https://jobs.example.com/job-search"
        job_url = job_list_url + "/bcf896f7352f1001b167c46dc9d00000"

        class StaticFetcher:
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=job_list_url,
                    html=(
                        f'<a href="{job_url}">National Account Manager - Hotels '
                        "Multiple locations Commercial Full time</a>"
                    ),
                    source="fixture",
                )

        match, _trace = JobOpeningMatcher(StaticFetcher()).match(
            job_list_url,
            "National Account Manager - Hotels",
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.url, job_url)

    def test_nested_anchor_title_matches_workable_detail(self):
        job_list_url = "https://awesomemotive.com/careers/"
        job_url = "https://apply.workable.com/awesomemotive/j/ABC123/"

        class StaticFetcher:
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=job_list_url,
                    html=(
                        f'<a href="{job_url}">'
                        "<h4>AI Developer</h4><span>Remote</span>"
                        "</a>"
                    ),
                    source="fixture",
                )

        match, _trace = JobOpeningMatcher(StaticFetcher()).match(
            job_list_url,
            "AI Developer",
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.url, job_url)
        self.assertEqual(match.title, "AI Developer")

    def test_title_match_scores_relevant_title_higher(self):
        good_score, _ = score_title_match("Product Manager, Ads", "Product Manager, Ads")
        weak_score, _ = score_title_match("Software Engineer", "Product Manager, Ads")

        self.assertGreater(good_score, weak_score)

    def test_title_match_scores_shared_generic_role_below_strict_tenant_gate(self):
        score, _reasons = score_title_match(
            "Senior Software Engineer, Backend",
            "Software Engineer, Fullstack",
        )

        self.assertLess(score, 65)

    def test_title_identity_rejects_unordered_overlap_from_another_role(self):
        candidate = "Full Stack Engineer (Automation & AI Agents) | Delivery Automation Team"

        self.assertGreaterEqual(score_title_match(candidate, "AI Engineer")[0], 65)
        self.assertFalse(title_identity_matches(candidate, "AI Engineer"))

    def test_title_identity_accepts_ordered_specialization_and_level_alias(self):
        self.assertTrue(title_identity_matches("Generative AI Engineer", "AI Engineer"))
        self.assertTrue(title_identity_matches("AI Algorithm Engineer Intern", "AI Engineer"))
        self.assertTrue(title_identity_matches("Senior Data Scientist", "Sr Data Scientist"))
        self.assertTrue(title_identity_matches("Engineer, AI", "AI Engineer"))
        self.assertTrue(title_identity_matches("Software Engineer I", "Software Engineer 1"))
        self.assertTrue(title_identity_matches("Engineer II", "Engineer"))
        self.assertFalse(title_identity_matches("Platform Engineer", "Engineer"))

    def test_native_adapter_does_not_promote_unordered_title_overlap(self):
        board_url = "https://jobs.example.com/example"

        class Adapter:
            name = "strict_title"
            supports_listing = True

            def recognizes(self, url):
                return url == board_url

            def identify_board(self, url):
                return JobBoard(url=url, provider=self.name, identifier="example")

            def list_jobs(self, fetcher, board, query):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    candidates=[
                        JobCandidate(
                            title=(
                                "Full Stack Engineer (Automation & AI Agents) "
                                "| Delivery Automation Team"
                            ),
                            url=board_url + "/1",
                            provider=self.name,
                        )
                    ],
                )

        matcher = JobOpeningMatcher(
            Fetcher(offline=True),
            ProviderRegistry([Adapter()]),
        )

        match, trace = matcher.match(board_url, "AI Engineer")

        self.assertIsNone(match)
        self.assertEqual(trace["provider_api"]["inventory"]["status"], "verified")
        self.assertEqual(trace["search_skipped"], "verified_native_inventory_no_match")

    def test_google_search_results_match_linkedin_title(self):
        matcher = JobOpeningMatcher(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        match, trace = matcher.match(
            "https://www.google.com/about/careers/applications/",
            "Product Manager, Ads",
        )

        self.assertIsNotNone(match)
        self.assertIn("123-product-manager-ads", match.url)
        self.assertEqual(trace["provider"], "google_careers")

    def test_provider_detection_covers_enterprise_ats(self):
        cases = {
            "https://company.wd5.myworkdayjobs.com/en-US/acme": "workday",
            "https://careers-acme.icims.com/jobs/search": "icims",
            "https://jobs.smartrecruiters.com/AcmeCorp": "smartrecruiters",
            "https://acme.successfactors.com/career": "successfactors",
            "https://acme.bamboohr.com/careers": "bamboohr",
            "https://ats.rippling.com/embed/acme/jobs": "rippling",
        }

        for url, provider in cases.items():
            with self.subTest(url=url):
                self.assertEqual(detect_provider(url), provider)

    def test_enterprise_ats_opening_matchers(self):
        matcher = JobOpeningMatcher(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )
        cases = {
            "https://company.wd5.myworkdayjobs.com/en-US/acme": "Data-Analyst_R123",
            "https://careers-acme.icims.com/jobs/search": "/jobs/1234/data-analyst/job",
            "https://jobs.smartrecruiters.com/AcmeCorp": "743999999999999-data-analyst",
            "https://acme.successfactors.com/career": "career_job_req_id=987",
            "https://acme.bamboohr.com/careers": "/careers/270",
            "https://ats.rippling.com/embed/acme-rippling/jobs": "b4f5c9d3",
        }

        for url, expected_url_part in cases.items():
            with self.subTest(url=url):
                match, trace = matcher.match(url, "Data Analyst")
                self.assertIsNotNone(match)
                self.assertIn(expected_url_part, match.url)
                self.assertEqual(trace["provider"], detect_provider(url))

    def test_provider_search_urls_are_provider_specific(self):
        cases = {
            "https://company.wd5.myworkdayjobs.com/en-US/acme": "q=Data+Analyst",
            "https://careers-acme.icims.com/jobs/search": "searchKeyword=Data+Analyst",
            "https://jobs.smartrecruiters.com/AcmeCorp": "search=Data+Analyst",
            "https://acme.successfactors.com/career": "keyword=Data+Analyst",
        }

        for url, expected_query in cases.items():
            with self.subTest(url=url):
                urls = build_provider_search_urls(url, "Data Analyst")
                self.assertTrue(any(expected_query in search_url for search_url in urls))

    def test_generic_search_urls_preserve_existing_scope_and_try_query_contract(self):
        board = "https://careers.example.com/jobs?businessUnits%5B%5D=square"

        urls = build_provider_search_urls(board, "SMB Account Executive")

        self.assertIn(board, urls)
        query_url = next(url for url in urls if "query=SMB+Account+Executive" in url)
        self.assertIn("businessUnits%5B%5D=square", query_url)

    def test_rippling_board_matches_static_job_link(self):
        matcher = JobOpeningMatcher(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        match, trace = matcher.match(
            "https://ats.rippling.com/embed/acme-rippling/jobs",
            "Data Analyst",
        )

        self.assertIsNotNone(match)
        self.assertIn("b4f5c9d3", match.url)
        self.assertEqual(trace["provider"], "rippling")

    def test_page_evidence_routes_customer_owned_jibe_board_to_icims_adapter(self):
        board_url = "https://jobs.example.org/region/jobs"
        board_html = (
            '<html data-jibe-search-version="4.11">'
            '<script src="https://app.jibecdn.com/prod/search/4/main.js"></script>'
            '<script>window.searchConfig = '
            '{"externalSearch":true,"searchOverride":{"brand":"Example Health"}};'
            '</script></html>'
        )
        api_payload = json.dumps({
            "count": 1,
            "totalCount": 1,
            "jobs": [{"data": {
                "slug": "135333",
                "title": "Registered Nurse / RN IMC",
                "ats_code": "icims",
                "meta_data": {
                    "canonical_url": "https://jobs.example.org/jobs/135333?lang=en-us"
                },
            }}],
        })

        class JibeFetcher:
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url=url,
                    html=board_html if url == board_url else api_payload,
                    source="jibe-fixture",
                )

        match, trace = JobOpeningMatcher(JibeFetcher()).match(
            board_url,
            "Registered Nurse / RN IMC",
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.url, "https://jobs.example.org/jobs/135333?lang=en-us")
        self.assertEqual(trace["provider"], "icims")
        self.assertEqual(trace["provider_api"]["adapter"], "icims")
        self.assertEqual(
            trace["provider_api"]["provider_detection"]["method"],
            "page_evidence",
        )

    def test_reuses_landing_page_and_submits_declared_get_form_before_guesses(self):
        board_url = "https://staff.example.com/jobs/search/"
        search_url = "https://staff.example.com/jobs/search/?q=AI+Engineer+II"
        landing_html = (
            '<form action="/jobs/search/" method="GET">'
            '<input type="search" name="q">'
            "</form>"
        )
        result_html = '<a href="/jobs/17810432-ai-engineer-ii">AI Engineer II</a>'

        class RecordingFetcher:
            def __init__(self):
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url == board_url:
                    return Page(url=url, final_url=url, html=landing_html, source="fixture")
                if url == search_url:
                    return Page(url=url, final_url=url, html=result_html, source="fixture")
                raise AssertionError(f"unexpected speculative fetch: {url}")

        fetcher = RecordingFetcher()
        match, trace = JobOpeningMatcher(fetcher).match(board_url, "AI Engineer II")

        self.assertIsNotNone(match)
        self.assertEqual(match.url, "https://staff.example.com/jobs/17810432-ai-engineer-ii")
        self.assertEqual(fetcher.calls, [board_url, search_url])
        self.assertEqual(trace["search_plan"][1]["source"], "declared_get_form")

    def test_declared_get_search_uses_title_query_portfolio_with_trace_provenance(self):
        board_url = "https://staff.example.com/jobs/search/"
        title = "Registered Nurse (RN) - Apollo Platform"
        queries = (
            "Registered+Nurse+%28RN%29+-+Apollo+Platform",
            "Registered+Nurse",
            "Apollo+Platform",
        )
        search_urls = [f"{board_url}?q={query}" for query in queries]
        detail_url = "https://staff.example.com/jobs/registered-nurse-rn-apollo-platform"
        landing_html = ('<form action="/jobs/search/" method="GET">'
                        '<input type="search" name="q"></form>')

        class RecordingFetcher:
            def __init__(self):
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append(url)
                if url == board_url:
                    return Page(url=url, final_url=url, html=landing_html, source="fixture")
                if url == search_urls[2]:
                    return Page(url=url, final_url=url,
                                html=f'<a href="{detail_url}">{title}</a>', source="fixture")
                if url in search_urls[:2]:
                    return Page(url=url, final_url=url, html="<main>No matches</main>")
                raise AssertionError(f"unexpected speculative fetch: {url}")

        fetcher = RecordingFetcher()
        match, trace = JobOpeningMatcher(fetcher).match(board_url, title)

        self.assertIsNotNone(match)
        self.assertEqual(fetcher.calls, [board_url, *search_urls])
        self.assertEqual(
            [item["query_source"] for item in trace["search_plan"][1:4]],
            ["full_title", "core_title", "product_or_team"],
        )
        self.assertEqual(
            [item["query"] for item in trace["search_plan"][1:4]],
            [title, "Registered Nurse", "Apollo Platform"],
        )

    def test_declared_post_search_uses_verified_submission_transport(self):
        board_url = "https://careers.example.com/jobs"
        search_url = "https://careers.example.com/jobs/search"
        detail_url = "https://careers.example.com/jobs/42/data-analyst"
        landing_html = (
            '<form action="/jobs/search" method="POST">'
            '<input type="search" name="keyword">'
            '<input type="hidden" name="department" value="all">'
            "</form>"
        )

        class PostFetcher:
            def __init__(self):
                self.calls = []

            def fetch(self, url, data=None, headers=None):
                self.calls.append((url, data, headers))
                if url == board_url:
                    return Page(url, landing_html, final_url=board_url)
                if url == search_url:
                    return Page(
                        url,
                        f'<a class="job-card" href="{detail_url}"><h3>Data Analyst</h3></a>',
                        final_url=board_url,
                    )
                return Page(url, "<main>No matches</main>")

        fetcher = PostFetcher()
        match, trace = JobOpeningMatcher(fetcher).match(board_url, "Data Analyst")

        self.assertIsNotNone(match)
        self.assertEqual(
            fetcher.calls[1],
            (
                search_url,
                b"department=all&keyword=Data+Analyst",
                {
                    "Accept": "application/json, text/html",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            ),
        )
        self.assertEqual(trace["job_search_submissions"][0]["status"], "submitted")
        self.assertEqual(
            trace["job_search_submissions"][0]["change_kind"],
            "listing_fingerprint",
        )

    def test_declared_post_unchanged_transport_is_typed_in_matcher_trace(self):
        board_url = "https://careers.example.com/jobs"
        landing_html = (
            '<form action="/jobs/search" method="POST">'
            '<input type="search" name="keyword"></form>'
        )

        class UnchangedFetcher:
            def fetch(self, url, data=None, headers=None):
                if url == board_url and data is None:
                    return Page(url, landing_html, final_url=board_url)
                if data is not None:
                    return Page(url, landing_html, final_url=board_url)
                return Page(url, "<main>No matches</main>")

        match, trace = JobOpeningMatcher(UnchangedFetcher()).match(
            board_url,
            "Data Analyst",
        )

        self.assertIsNone(match)
        self.assertEqual(
            trace["job_search_submissions"][0]["status"],
            "transport_unchanged",
        )

    def test_js_only_search_fills_exact_title_and_matches_rendered_candidate(self):
        board_url = "https://www.randstadusa.com/jobs/"
        job_url = "https://www.randstadusa.com/jobs/123/data-analyst/"
        landing_html = (
            '<form id="job-search-form">'
            '<input id="job-title" name="jobTitle" type="text" '
            'placeholder="Search by job title or keyword">'
            '<button type="button"><span>Search</span></button>'
            "</form>"
        )
        result_html = (
            f'<article class="job-card"><h3>Data Analyst</h3>'
            f'<a href="{job_url}">View job</a></article>'
        )

        class InteractiveFetcher:
            def __init__(self):
                self.calls = []

            def fetch(self, url, data=None, headers=None, *, interaction=None):
                self.calls.append((url, interaction))
                if interaction is None:
                    return Page(url, landing_html, final_url=board_url, source="fixture")
                self.assert_interaction(interaction)
                return Page(url, result_html, final_url=board_url, source="browser")

            @staticmethod
            def assert_interaction(interaction):
                assert interaction == JobSearchInteraction(
                    form_ordinal=0,
                    query_name="jobTitle",
                    query_id="job-title",
                    target_title="Data Analyst",
                    submit_text="Search",
                )

        fetcher = InteractiveFetcher()
        match, trace = JobOpeningMatcher(fetcher).match(board_url, "Data Analyst")

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, job_url)
        self.assertEqual(len(fetcher.calls), 2)
        self.assertIsNone(fetcher.calls[0][1])
        self.assertEqual(fetcher.calls[1][1].target_title, "Data Analyst")
        self.assertEqual(
            [item["source"] for item in trace["search_plan"][:2]],
            ["reused_landing_page", "interactive_job_search"],
        )
        self.assertEqual(trace["interactive_search"]["disposition"], "matched")

    def test_interactive_capability_failure_stays_typed_and_uses_fallback_afterward(self):
        board_url = "https://jobs.example.com/jobs/"
        landing_html = (
            '<form><input id="job-title" name="jobTitle" '
            'placeholder="Search by job title">'
            '<button type="button">Search</button></form>'
        )

        class CapabilityFetcher:
            def __init__(self):
                self.calls = []

            def fetch(self, url, data=None, headers=None, *, interaction=None):
                self.calls.append((url, interaction))
                if interaction is not None:
                    raise FetchError(
                        "browser interaction unavailable",
                        reason_code="OPENING_DISCOVERY_INCOMPLETE",
                        retryable=False,
                    )
                if url == board_url:
                    return Page(url, landing_html, final_url=board_url, source="fixture")
                return Page(url, "", final_url=url, source="fixture")

        fetcher = CapabilityFetcher()
        match, trace = JobOpeningMatcher(fetcher).match(board_url, "Data Analyst")

        self.assertIsNone(match)
        self.assertEqual(
            trace["interactive_search"]["reason_code"],
            "OPENING_DISCOVERY_INCOMPLETE",
        )
        self.assertEqual(trace["interactive_search"]["disposition"], "fetch_failed")
        self.assertEqual(trace["errors"][0]["phase"], "interactive_job_search")
        interaction_index = next(
            index for index, (_url, interaction) in enumerate(fetcher.calls)
            if interaction is not None
        )
        fallback_index = next(
            index for index, (url, _interaction) in enumerate(fetcher.calls)
            if "?q=" in url
        )
        self.assertLess(interaction_index, fallback_index)

    def test_interactive_unchanged_transport_uses_shared_verifier_status(self):
        board_url = "https://jobs.example.com/jobs/"
        landing_html = (
            '<form><input id="job-title" name="jobTitle" '
            'placeholder="Search by job title">'
            '<button type="button">Search</button></form>'
        )

        class UnchangedInteractiveFetcher:
            def fetch(self, url, data=None, headers=None, *, interaction=None):
                if interaction is not None:
                    return Page(
                        url,
                        landing_html + "<script>window.requestId='new'</script>",
                        final_url=board_url,
                    )
                if url == board_url:
                    return Page(url, landing_html, final_url=board_url)
                return Page(url, "<main>No matches</main>")

        match, trace = JobOpeningMatcher(UnchangedInteractiveFetcher()).match(
            board_url,
            "Data Analyst",
        )

        self.assertIsNone(match)
        self.assertEqual(
            trace["interactive_search"]["disposition"],
            "transport_unchanged",
        )

    def test_interactive_search_page_cannot_be_its_own_opening(self):
        board_url = "https://jobs.example.com/jobs/"
        landing_html = (
            '<form><input name="jobTitle" placeholder="Search by job title">'
            '<button type="button">Search</button></form>'
        )

        class SelfLinkFetcher:
            def fetch(self, url, data=None, headers=None, *, interaction=None):
                if interaction is not None:
                    return Page(
                        url,
                        f'<a href="{board_url}">Data Analyst</a>',
                        final_url=board_url,
                        source="browser",
                    )
                if url == board_url:
                    return Page(url, landing_html, final_url=board_url, source="fixture")
                return Page(url, "", final_url=url, source="fixture")

        match, trace = JobOpeningMatcher(SelfLinkFetcher()).match(
            board_url,
            "Data Analyst",
        )

        self.assertIsNone(match)
        self.assertNotIn(board_url, [item["url"] for item in trace["candidates"]])
        self.assertEqual(
            trace["interactive_search"]["disposition"],
            "transport_unchanged",
        )

    def test_url_location_tokens_only_break_exact_title_ties(self):
        board_url = "https://jobs.example.com/search"
        wrong = "https://jobs.example.com/job/mechanical-design-engineer-bellevue-washington/1"
        right = "https://jobs.example.com/job/mechanical-design-engineer-york-pa/2"

        class FixtureFetcher:
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=board_url,
                    final_url=board_url,
                    html=(
                        f'<a href="{wrong}">Mechanical Design Engineer</a>'
                        f'<a href="{right}">Mechanical Design Engineer</a>'
                    ),
                    source="fixture",
                )

        match, trace = JobOpeningMatcher(FixtureFetcher()).match(
            board_url,
            "Mechanical Design Engineer",
            "York, PA",
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, right)
        self.assertIn("opening URL location token overlap", match.reasons)

    def test_native_inventory_skips_wrong_location_and_continues(self):
        board_url = "https://jobs.example.com/board"

        class LocationAdapter:
            name = "location_test"
            supports_listing = True

            def recognizes(self, url):
                return url == board_url

            def identify_board(self, url):
                return JobBoard(url=url, provider=self.name, identifier="example")

            def list_jobs(self, fetcher, board, query):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    candidates=[
                        JobCandidate(
                            title="Data Analyst",
                            location="San Francisco, CA",
                            url="https://jobs.example.com/job/1",
                            provider=self.name,
                        ),
                        JobCandidate(
                            title="Data Analyst",
                            location="New York, NY",
                            url="https://jobs.example.com/job/2",
                            provider=self.name,
                        ),
                    ],
                    inventory_scope="full",
                    inventory_complete=True,
                )

        match, trace = JobOpeningMatcher(
            Fetcher(offline=True),
            ProviderRegistry([LocationAdapter()]),
        ).match(board_url, "Data Analyst", "New York, NY")

        self.assertIsNotNone(match)
        self.assertEqual(match.url, "https://jobs.example.com/job/2")
        self.assertEqual(
            trace["provider_api"]["rejected_candidates"][0]["reason"],
            "location_identity_mismatch",
        )

    def test_location_identity_accepts_city_in_full_street_address(self):
        self.assertFalse(
            _is_explicit_location_mismatch(
                "2224 Bay Area Boulevard, Houston, TX, USA",
                "Houston, TX",
            )
        )

    def test_location_identity_accepts_opaque_facility_label_in_target_state(self):
        self.assertFalse(
            _is_explicit_location_mismatch("C Forks PA", "Easton, PA")
        )

    def test_location_identity_rejects_explicit_conflicting_city(self):
        self.assertTrue(
            _is_explicit_location_mismatch("Pittsburgh, PA", "Easton, PA")
        )

    def test_location_identity_rejects_explicit_conflicting_state(self):
        self.assertTrue(
            _is_explicit_location_mismatch("Houston, CA", "Houston, TX")
        )

    def test_location_filter_keeps_remote_and_multiple_location_candidates(self):
        board_url = "https://jobs.example.com/board"

        class FlexibleLocationAdapter:
            name = "flexible_location_test"
            supports_listing = True

            def recognizes(self, url):
                return url == board_url

            def identify_board(self, url):
                return JobBoard(url=url, provider=self.name, identifier="example")

            def list_jobs(self, fetcher, board, query):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    candidates=[
                        JobCandidate(
                            title="Data Analyst",
                            location="Remote - Multiple locations",
                            url="https://jobs.example.com/job/remote",
                            provider=self.name,
                        )
                    ],
                    inventory_scope="full",
                    inventory_complete=True,
                )

        match, _trace = JobOpeningMatcher(
            Fetcher(offline=True),
            ProviderRegistry([FlexibleLocationAdapter()]),
        ).match(board_url, "Data Analyst", "New York, NY")

        self.assertIsNotNone(match)

    def test_generic_landing_fetch_failure_is_preserved_for_availability_diagnostics(self):
        board_url = "https://staff.example.com/"

        class ForbiddenFetcher:
            def fetch(self, url, data=None, headers=None):
                raise FetchError("HTTP Error 403: Forbidden")

        match, trace = JobOpeningMatcher(ForbiddenFetcher()).match(
            board_url,
            "AI Engineer II",
        )

        self.assertIsNone(match)
        self.assertEqual(
            trace["provider_api"]["errors"][0],
            {
                "url": board_url,
                "error": "HTTP Error 403: Forbidden",
                "phase": "page_evidence",
            },
        )

    def test_declared_search_forms_reject_post_cross_site_and_sensitive_actions(self):
        page = Page(
            url="https://jobs.example.com/careers",
            final_url="https://jobs.example.com/careers",
            source="fixture",
            html="""
                <form action="https://evil.example/search" method="get"><input name="q"></form>
                <form action="/private?token=secret" method="get"><input name="query"></form>
                <form action="/post-search" method="post"><input name="keywords"></form>
                <form action="/jobs/search/?sort=relevancy"><input type="search" name="q"></form>
            """,
        )

        self.assertEqual(
            build_search_form_urls(page, "Data Analyst"),
            ["https://jobs.example.com/jobs/search/?sort=relevancy&q=Data+Analyst"],
        )

    def test_provider_variant_unsupported_remains_typed_in_trace(self):
        class UnsupportedAdapter:
            name = "unsupported_test"
            supports_listing = True

            def recognizes(self, url):
                return True

            def identify_board(self, url):
                return JobBoard(url=url, provider=self.name, identifier="example")

            def list_jobs(self, fetcher, board, query):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    reason_code="PROVIDER_VARIANT_UNSUPPORTED",
                    trace={"adapter": self.name, "error": "unsupported public variant"},
                )

        class EmptyFetcher:
            def fetch(self, url, data=None, headers=None):
                return Page(url=url, final_url=url, html="", source="fixture")

        match, trace = JobOpeningMatcher(
            EmptyFetcher(),
            ProviderRegistry([UnsupportedAdapter()]),
        ).match("https://jobs.example.com/careers", "Data Analyst")

        self.assertIsNone(match)
        self.assertEqual(
            trace["provider_api"]["inventory"]["reason_code"],
            "PROVIDER_VARIANT_UNSUPPORTED",
        )
        self.assertEqual(
            trace["provider_api"]["adapter_trace"]["error"],
            "unsupported public variant",
        )

    def test_structured_provider_apis_are_used_before_html(self):
        matcher = JobOpeningMatcher(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )
        cases = {
            "https://boards.greenhouse.io/acme": "https://boards.greenhouse.io/acme/jobs/12345",
            "https://jobs.lever.co/apiacme": "https://jobs.lever.co/apiacme/abc123",
            "https://jobs.smartrecruiters.com/AcmeApi": "https://jobs.smartrecruiters.com/AcmeApi/743999111111111-data-analyst",
            "https://company.wd5.myworkdayjobs.com/en-US/acme": "https://company.wd5.myworkdayjobs.com/en-US/acme/job/New-York-NY/Data-Analyst_R123",
            "https://jobs.ashbyhq.com/acme": "https://jobs.ashbyhq.com/acme/ashby-data-analyst",
        }

        for url, expected in cases.items():
            with self.subTest(url=url):
                match, trace = matcher.match(url, "Data Analyst")
                self.assertIsNotNone(match)
                self.assertEqual(match.url, expected)
                self.assertTrue(trace["provider_api"]["candidates"])

    def test_native_adapter_records_verified_inventory_when_title_does_not_match(self):
        matcher = JobOpeningMatcher(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        match, trace = matcher.match(
            "https://jobs.lever.co/apiacme",
            "Quantum Archaeologist",
        )

        self.assertIsNone(match)
        self.assertEqual(trace["provider_api"]["inventory"]["status"], "verified")
        self.assertGreater(trace["provider_api"]["inventory"]["candidate_count"], 0)
        self.assertLess(
            trace["provider_api"]["inventory"]["strongest_title_score"],
            45,
        )
        self.assertEqual(trace["search_plan"], [])
        self.assertEqual(
            trace["search_skipped"],
            "verified_native_inventory_no_match",
        )

    def test_partial_provider_inventory_allows_positive_match_without_verified_no_match(self):
        class PartialAdapter:
            name = "partial_test"
            supports_listing = True

            def __init__(self):
                self.query = None

            def recognizes(self, url):
                return True

            def identify_board(self, url):
                return JobBoard(url=url, provider=self.name, identifier="example")

            def list_jobs(self, fetcher, board, query):
                self.query = query
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    candidates=[
                        JobCandidate(
                            title="Product Manager",
                            location="Remote, US",
                            url="https://jobs.example.com/1",
                            provider=self.name,
                        )
                    ],
                    inventory_scope="visible_page",
                    inventory_complete=False,
                )

        adapter = PartialAdapter()
        matcher = JobOpeningMatcher(Fetcher(offline=True), ProviderRegistry([adapter]))

        match, matched_trace = matcher.match(
            "https://jobs.example.com",
            "Product Manager",
            "Remote, US",
        )
        missing, missing_trace = matcher.match(
            "https://jobs.example.com",
            "Quantum Archaeologist",
            "Remote, US",
        )

        self.assertIsNotNone(match)
        self.assertIsNone(missing)
        self.assertEqual(adapter.query.location, "Remote, US")
        self.assertEqual(
            matched_trace["provider_api"]["inventory"]["status"],
            "incomplete",
        )
        self.assertEqual(
            missing_trace["provider_api"]["inventory"]["status"],
            "incomplete",
        )
        self.assertFalse(missing_trace["provider_api"]["inventory"]["complete"])
        self.assertNotEqual(
            missing_trace.get("search_skipped"),
            "verified_native_inventory_no_match",
        )

    def test_incomplete_native_inventory_uses_strict_same_site_detail_search(self):
        board_url = "https://jobs.acme.example/job-search-results/"
        detail_url = "https://jobs.acme.example/job-3/42/platform-engineer/"
        posting = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "title": "Platform Engineer",
                "jobLocation": {
                    "@type": "Place",
                    "address": {
                        "addressLocality": "Denver",
                        "addressRegion": "CO",
                    },
                },
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": "Acme",
                    "sameAs": "https://acme.example/",
                },
            }
        )

        class PartialAdapter:
            name = "cws"
            supports_listing = True

            def recognizes(self, url):
                return url.startswith("https://jobs.acme.example/")

            def identify_board(self, url):
                return JobBoard(url=board_url, provider=self.name, identifier="acme")

            def list_jobs(self, fetcher, board, query):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    reason_code="NETWORK_TIMEOUT",
                    retryable=True,
                    inventory_scope="title_filtered",
                    inventory_complete=False,
                )

        class SearchFetcher:
            def fetch(self, url, data=None, headers=None):
                if "bing.com/search" in url:
                    return Page(
                        url,
                        f"<rss><channel><item><link>{detail_url}</link></item></channel></rss>",
                    )
                if url == detail_url:
                    return Page(
                        url,
                        f'<script type="application/ld+json">{posting}</script>',
                    )
                raise FetchError(f"unexpected URL: {url}")

        match, trace = JobOpeningMatcher(
            SearchFetcher(),
            ProviderRegistry([PartialAdapter()]),
        ).match(board_url, "Platform Engineer", "Denver, CO")

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, detail_url)
        self.assertEqual(match.provider, "cws")
        self.assertEqual(
            trace["provider_api"]["inventory"]["status"],
            "incomplete",
        )
        self.assertEqual(len(trace["verified_site_search"]["verified_pages"]), 1)

    def test_incomplete_native_inventory_rejects_closed_indexed_detail(self):
        board_url = "https://jobs.acme.example/job-search-results/"
        detail_url = "https://jobs.acme.example/job-3/42/platform-engineer/"
        posting = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "title": "Platform Engineer",
                "url": detail_url,
                "jobLocation": {
                    "@type": "Place",
                    "address": {
                        "addressLocality": "Denver",
                        "addressRegion": "CO",
                    },
                },
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": "Acme",
                    "url": "https://jobs.acme.example/",
                },
            }
        )

        class PartialAdapter:
            name = "cws"
            supports_listing = True

            def recognizes(self, url):
                return url.startswith("https://jobs.acme.example/")

            def identify_board(self, url):
                return JobBoard(url=board_url, provider=self.name, identifier="acme")

            def list_jobs(self, fetcher, board, query):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    reason_code="NETWORK_TIMEOUT",
                    retryable=True,
                    inventory_scope="title_filtered",
                    inventory_complete=False,
                )

        class SearchFetcher:
            def fetch(self, url, data=None, headers=None):
                if "bing.com/search" in url:
                    return Page(
                        url,
                        f"<rss><channel><item><link>{detail_url}</link></item></channel></rss>",
                    )
                if url == detail_url:
                    return Page(
                        url,
                        "The job you are trying to apply for does not exist!"
                        f'<script type="application/ld+json">{posting}</script>',
                    )
                raise FetchError(f"unexpected URL: {url}")

        match, trace = JobOpeningMatcher(
            SearchFetcher(),
            ProviderRegistry([PartialAdapter()]),
        ).match(board_url, "Platform Engineer", "Denver, CO")

        self.assertIsNone(match)
        self.assertEqual(
            trace["verified_site_search"]["rejected_pages"][0]["reason"],
            "opening_closed_or_unavailable",
        )

    def test_acquired_brand_handoff_requires_exact_normalized_title(self):
        class ParentInventoryAdapter:
            name = "parent_inventory"
            supports_listing = True

            def recognizes(self, url):
                return False

            def identify_board(self, url):
                return None

            def list_jobs(self, fetcher, board, query):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    candidates=[
                        JobCandidate(
                            title="Principal / Senior Data Scientist - LLM Agents",
                            url="https://jobs.parent.example/job/1",
                            provider=self.name,
                        )
                    ],
                    inventory_scope="title_filtered",
                    inventory_complete=True,
                )

        board = JobBoard(
            url="https://jobs.parent.example/search-jobs",
            provider="parent_inventory",
        )
        discovered = DiscoveredJobBoard(
            board=board,
            detection_method="acquired_brand_handoff",
            evidence_url="https://jobs.parent.example/",
        )
        matcher = JobOpeningMatcher(
            Fetcher(offline=True),
            ProviderRegistry((ParentInventoryAdapter(),)),
        )

        match, trace = matcher.match(
            board.url,
            "Data Scientist",
            discovered_board=discovered,
        )

        self.assertIsNone(match)
        self.assertEqual(
            trace["provider_api"]["title_policy"],
            "exact_for_acquired_brand_handoff",
        )

    def test_native_budget_exhaustion_skips_generic_fallback(self):
        class BudgetAdapter:
            name = "budget_test"
            supports_listing = True

            def recognizes(self, url):
                return True

            def identify_board(self, url):
                return JobBoard(url=url, provider=self.name, identifier="example")

            def list_jobs(self, fetcher, board, query):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    candidates=[
                        JobCandidate(
                            title="Data Engineer",
                            url="https://jobs.example.com/1",
                            provider=self.name,
                        )
                    ],
                    reason_code="FETCH_BUDGET_EXHAUSTED",
                    retryable=True,
                    inventory_scope="title_filtered",
                    inventory_complete=False,
                    trace={"stop_reason": "soft_deadline_reserve"},
                )

        class NoFallbackFetcher:
            def fetch(self, url, data=None, headers=None):
                raise AssertionError(f"unexpected generic fallback fetch: {url}")

        match, trace = JobOpeningMatcher(
            NoFallbackFetcher(),
            ProviderRegistry([BudgetAdapter()]),
        ).match("https://jobs.example.com", "Quantum Archaeologist")

        self.assertIsNone(match)
        self.assertEqual(trace["search_plan"], [])
        self.assertEqual(
            trace["search_skipped"],
            "native_inventory_budget_exhausted",
        )
        self.assertEqual(
            trace["provider_api"]["inventory"]["reason_code"],
            "FETCH_BUDGET_EXHAUSTED",
        )

    def test_native_adapter_rejects_generic_role_token_as_exact_opening(self):
        class BroadSearchAdapter:
            name = "broad_search_test"
            supports_listing = True

            def recognizes(self, url):
                return True

            def identify_board(self, url):
                return JobBoard(url=url, provider=self.name, identifier="example")

            def list_jobs(self, fetcher, board, query):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    candidates=[
                        JobCandidate(
                            title="Engineer",
                            url="https://jobs.example.com/1",
                            provider=self.name,
                        )
                    ],
                    inventory_scope="title_filtered",
                    inventory_complete=True,
                )

        matcher = JobOpeningMatcher(
            Fetcher(offline=True),
            ProviderRegistry([BroadSearchAdapter()]),
        )

        match, trace = matcher.match(
            "https://jobs.example.com",
            "Artificial Intelligence Engineer",
        )

        self.assertIsNone(match)
        self.assertEqual(trace["provider_api"]["inventory"]["status"], "verified")
        self.assertEqual(
            trace["provider_api"]["inventory"]["strongest_title_score"],
            53,
        )

    def test_native_adapter_uses_location_to_break_exact_title_tie(self):
        class LocationAdapter:
            name = "location_test"
            supports_listing = True

            def recognizes(self, url):
                return True

            def identify_board(self, url):
                return JobBoard(url=url, provider=self.name, identifier="example")

            def list_jobs(self, fetcher, board, query):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    candidates=[
                        JobCandidate(
                            title="Product Manager",
                            location="London, United Kingdom",
                            url="https://jobs.example.com/uk",
                            provider=self.name,
                        ),
                        JobCandidate(
                            title="Product Manager",
                            location="Remote, US",
                            url="https://jobs.example.com/us",
                            provider=self.name,
                        ),
                    ],
                )

        matcher = JobOpeningMatcher(
            Fetcher(offline=True),
            ProviderRegistry([LocationAdapter()]),
        )

        match, trace = matcher.match(
            "https://jobs.example.com",
            "Product Manager",
            "Remote, US",
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.url, "https://jobs.example.com/us")
        self.assertIn("exact location match", trace["selected"]["reasons"])

    def test_adapter_errors_override_legacy_complete_default(self):
        class PartialFailureAdapter:
            name = "partial_failure"
            supports_listing = True

            def recognizes(self, url):
                return True

            def identify_board(self, url):
                return JobBoard(url=url, provider=self.name, identifier="example")

            def list_jobs(self, fetcher, board, query):
                return AdapterResult(
                    provider=self.name,
                    board=board,
                    candidates=[
                        JobCandidate(
                            title="Software Engineer",
                            url="https://jobs.example.com/1",
                            provider=self.name,
                        )
                    ],
                    trace={"page_errors": [{"error": "page 2 timed out"}]},
                )

        matcher = JobOpeningMatcher(
            Fetcher(offline=True),
            ProviderRegistry([PartialFailureAdapter()]),
        )

        match, trace = matcher.match(
            "https://jobs.example.com",
            "Quantum Archaeologist",
        )

        self.assertIsNone(match)
        self.assertEqual(trace["provider_api"]["inventory"]["status"], "incomplete")
        self.assertFalse(trace["provider_api"]["inventory"]["complete"])

    def test_provider_api_urls_are_built_from_job_board_urls(self):
        cases = {
            "https://boards.greenhouse.io/acme": "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
            "https://jobs.lever.co/apiacme": "https://api.lever.co/v0/postings/apiacme?mode=json",
            "https://jobs.smartrecruiters.com/AcmeApi": "https://api.smartrecruiters.com/v1/companies/AcmeApi/postings?limit=100",
            "https://company.wd5.myworkdayjobs.com/en-US/acme": "https://company.wd5.myworkdayjobs.com/wday/cxs/company/acme/jobs",
            "https://jobs.ashbyhq.com/acme": "https://api.ashbyhq.com/posting-api/job-board/acme",
            "https://acme.bamboohr.com/careers": "https://acme.bamboohr.com/careers/list",
        }

        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertIn(expected, build_provider_api_urls(url))

    def test_structured_json_ld_job_links_are_extracted(self):
        html = """
        <script type="application/ld+json">
          {"@type":"JobPosting","title":"Data Analyst","url":"/jobs/2345/data-analyst/job"}
        </script>
        """

        links = structured_job_links(html, "https://careers-acme.icims.com/jobs/search")

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].text, "Data Analyst")
        self.assertEqual(links[0].url, "https://careers-acme.icims.com/jobs/2345/data-analyst/job")

    def test_icims_json_ld_page_can_match_opening(self):
        matcher = JobOpeningMatcher(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )

        match, trace = matcher.match(
            "https://careers-acme.icims.com/jobs/search-jsonld",
            "Data Analyst",
        )

        self.assertIsNotNone(match)
        self.assertIn("/jobs/2345/data-analyst/job", match.url)
        self.assertEqual(trace["provider"], "icims")

    def test_embedded_json_job_links_are_extracted(self):
        html = """
        <script type="application/json">
          {"jobs":[{"title":"Data Analyst","shortcode":"ABC123","location":"New York"}]}
        </script>
        """

        links = structured_job_links(html, "https://apply.workable.com/acme")

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].text, "Data Analyst")
        self.assertEqual(links[0].url, "https://apply.workable.com/acme/j/ABC123/")

    def test_embedded_json_pages_can_match_provider_openings(self):
        matcher = JobOpeningMatcher(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True)
        )
        cases = {
            "https://apply.workable.com/acme": "https://apply.workable.com/acme/j/ABC123/",
            "https://acme.successfactors.com/career-json": "career_job_req_id=987",
            "https://careers-acme.icims.com/jobs/search-embedded": "/jobs/3456/data-analyst/job",
        }

        for url, expected_url_part in cases.items():
            with self.subTest(url=url):
                match, trace = matcher.match(url, "Data Analyst")
                self.assertIsNotNone(match)
                self.assertIn(expected_url_part, match.url)
                self.assertEqual(trace["provider"], detect_provider(url))

    def test_parent_card_associates_action_link_with_title_and_ranks_exact_match(self):
        matcher = JobOpeningMatcher(Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True))

        match, trace = matcher.match(
            "https://exploratory.example/careers",
            "Product Manager, Ads",
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.title, "Product Manager, Ads")
        self.assertEqual(match.url, "https://exploratory.example/careers/product-manager-ads-4815")
        self.assertEqual(trace["selected"]["score"], match.score)

    def test_generic_cards_cover_same_origin_and_external_ats_details(self):
        matcher = JobOpeningMatcher(Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True))
        cases = {
            "Senior Software Engineer, Video": "/en/careers/8142331/senior-software-engineer-video/",
            "Applied Scientist, Recommendations": "/jobs/2981774/applied-scientist-recommendations",
            "Data Platform Engineer": "https://jobs.ashbyhq.com/snowflake/8c54a6d7",
            "Machine Learning Engineer": "/jobs/991/machine-learning-engineer",
        }

        for title, expected in cases.items():
            with self.subTest(title=title):
                match, _ = matcher.match("https://exploratory.example/careers", title)
                self.assertIsNotNone(match)
                self.assertIn(expected, match.url)

    def test_extractor_dedupes_html_and_assignment_state(self):
        html = (ROOT / "samples" / "sites" / "exploratory.example" / "careers" / "index.html").read_text()

        candidates = extract_listing_candidates(html, "https://exploratory.example/careers")
        product = [item for item in candidates if item.title == "Product Manager, Ads"]

        self.assertEqual(len(product), 1)
        self.assertTrue(any(item.title == "Machine Learning Engineer" for item in candidates))

    def test_output_url_validation_rejects_unsafe_external_and_false_positive_urls(self):
        source = "https://exploratory.example/careers"
        rejected = (
            "javascript:alert(1)",
            "https://evil.example/jobs/security-engineer",
            "https://user:secret@jobs.ashbyhq.com/snowflake/8c54a6d7",
            "https://jobs.ashbyhq.com:8443/snowflake/8c54a6d7",
            "/careers/benefits",
            "/careers",
            "https://www.linkedin.com/jobs/view/123",
        )

        for url in rejected:
            with self.subTest(url=url):
                self.assertIsNone(validate_output_url(url, source))
        self.assertEqual(
            validate_output_url("https://jobs.ashbyhq.com/snowflake/8c54a6d7", source),
            "https://jobs.ashbyhq.com/snowflake/8c54a6d7",
        )

    def test_output_url_validation_accepts_title_bound_same_origin_career_detail(self):
        source = "https://goguava.ai/careers"
        opening = "https://goguava.ai/careers/mid-market-account-executive"

        self.assertEqual(
            validate_output_url(
                opening,
                source,
                title="Mid-Market Account Executive",
            ),
            opening,
        )
        self.assertIsNone(
            validate_output_url(opening, source, title="Privacy Policy")
        )
        self.assertIsNone(
            validate_output_url(
                "https://evil.example/careers/mid-market-account-executive",
                source,
                title="Mid-Market Account Executive",
            )
        )

    def test_output_url_validation_accepts_title_bound_sibling_job_host(self):
        source = "https://careers.example.com/jobs"
        opening = "https://jobs.example.com/job/Portland-Financial-Analyst/123"

        self.assertEqual(
            validate_output_url(opening, source, title="Financial Analyst"),
            opening,
        )
        self.assertIsNone(
            validate_output_url(
                "https://jobs.other-example.com/job/Portland-Financial-Analyst/123",
                source,
                title="Financial Analyst",
            )
        )

    def test_output_url_validation_accepts_only_attested_applicant_manager_position(self):
        source = "https://theapplicantmanager.com/careers?co=n5"
        opening = "https://theapplicantmanager.com/jobs?pos=n513775"

        self.assertEqual(
            validate_output_url(
                opening,
                source,
                title="Registered Nurse RN - $40.55 per hour",
                origin="applicant_manager_table",
            ),
            opening,
        )
        for rejected, origin in (
            (opening, "anchor"),
            ("https://evil.example/jobs?pos=n513775", "applicant_manager_table"),
            ("https://theapplicantmanager.com/jobs?pos=../../secret", "applicant_manager_table"),
            ("https://theapplicantmanager.com/jobs?pos=n513775&next=evil", "applicant_manager_table"),
        ):
            with self.subTest(url=rejected, origin=origin):
                self.assertIsNone(
                    validate_output_url(
                        rejected,
                        source,
                        title="Registered Nurse RN - $40.55 per hour",
                        origin=origin,
                    )
                )

    def test_declared_get_search_form_accepts_term_field(self):
        page = Page(
            url="https://careers.example.com/jobs/results",
            html=(
                '<form method="get" action="/jobs/search-action">'
                '<input name="term" type="text">'
                '<input name="location" value="">'
                '</form>'
            ),
        )

        self.assertEqual(
            build_search_form_urls(page, "Senior Manufacturing Engineer"),
            [
                "https://careers.example.com/jobs/search-action"
                "?term=Senior+Manufacturing+Engineer"
            ],
        )

    def test_nested_cards_do_not_broadcast_child_title_to_parent_links(self):
        html = """
            <section class="job-card">
              <h2>Engineering roles</h2>
              <a href="/careers">All roles</a>
              <article class="job-card">
                <h3>Staff Platform Engineer</h3>
                <a href="/jobs/123/staff-platform-engineer">See role</a>
              </article>
            </section>
        """

        candidates = extract_listing_candidates(html, "https://exploratory.example/careers")

        self.assertEqual(
            [(candidate.title, candidate.url) for candidate in candidates],
            [("Staff Platform Engineer", "https://exploratory.example/jobs/123/staff-platform-engineer")],
        )

    def test_structured_state_requires_job_container_or_explicit_job_schema(self):
        html = """
            <script>
              window.__STATE__ = {"navigation":{"name":"Security","url":"/jobs/123/security"}};
            </script>
        """

        self.assertEqual(
            extract_listing_candidates(html, "https://exploratory.example/careers"),
            [],
        )

    def test_paragraph_title_and_dotted_assignment_state_are_extracted(self):
        html = """
            <ul>
              <li class="opening-item">
                <p>New York Office</p>
                <p>Software Engineer, Full Stack</p>
                <a href="/careers/openings/software-engineer-full-stack">See role</a>
              </li>
            </ul>
            <script>
              var phApp = {"page":"search-results"};
              phApp.ddo = {"jobs":[{"title":"Software Engineer - Backend","applyUrl":"https://jobs.ashbyhq.com/acme/abc-123"}]};
              phApp.session = {"page":"search-results"};
            </script>
        """

        candidates = extract_listing_candidates(html, "https://careers.example.com/search-results")

        self.assertIn(
            ("Software Engineer, Full Stack", "https://careers.example.com/careers/openings/software-engineer-full-stack"),
            [(candidate.title, candidate.url) for candidate in candidates],
        )
        self.assertIn(
            ("Software Engineer - Backend", "https://jobs.ashbyhq.com/acme/abc-123"),
            [(candidate.title, candidate.url) for candidate in candidates],
        )


if __name__ == "__main__":
    unittest.main()
