import unittest
import json
from pathlib import Path

from job_source_agent.opening_matcher import (
    JobOpeningMatcher,
    build_provider_api_urls,
    build_provider_search_urls,
    build_search_form_urls,
    detect_provider,
    score_title_match,
    structured_job_links,
)
from job_source_agent.listing_extraction import (
    extract_listing_candidates,
    validate_output_url,
)
from job_source_agent.web import FetchError, Fetcher, Page
from job_source_agent.providers.base import AdapterResult, JobBoard, JobCandidate
from job_source_agent.providers.registry import ProviderRegistry


ROOT = Path(__file__).resolve().parents[1]


class OpeningMatcherTests(unittest.TestCase):
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
        self.assertEqual(match.title, "AI Developer Remote")

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
            "Registered Nurse",
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
        self.assertNotIn("search_skipped", missing_trace)

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
