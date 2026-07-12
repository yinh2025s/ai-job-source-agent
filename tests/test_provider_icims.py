import unittest
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from job_source_agent.providers.base import JobBoard, JobQuery
from job_source_agent.providers.icims import ICIMSAdapter
from job_source_agent.web import FetchError, Page


ROOT = Path(__file__).resolve().parents[1]


class StubFetcher:
    def __init__(self, html: str = "", error: Exception | None = None):
        self.html = html
        self.error = error
        self.requested_urls = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        if self.error:
            raise self.error
        return Page(url=url, final_url=url, html=self.html, source="icims-fixture")


class FixtureMappingFetcher:
    def __init__(self, pages: dict[str, Path], errors: dict[str, Exception] | None = None):
        self.pages = pages
        self.errors = errors or {}
        self.requested_urls = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        if url in self.errors:
            raise self.errors[url]
        path = self.pages[url]
        return Page(
            url=url,
            final_url=url,
            html=path.read_text(encoding="utf-8"),
            source=str(path),
        )


class ICIMSAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ICIMSAdapter()

    def test_recognizes_only_icims_careers_job_search_and_detail_urls(self):
        self.assertTrue(self.adapter.recognizes("https://careers-acme.icims.com/jobs/search"))
        self.assertTrue(self.adapter.recognizes("https://careers-acme.icims.com/jobs/search?ss=1"))
        self.assertTrue(self.adapter.recognizes("https://careers-acme.icims.com/jobs/123/data-analyst/job"))
        self.assertFalse(self.adapter.recognizes("https://careers-acme.icims.com/"))
        self.assertFalse(self.adapter.recognizes("https://jobs-acme.icims.com/jobs/search"))
        self.assertFalse(self.adapter.recognizes("https://example.com/jobs/careers-acme.icims.com/jobs/search"))
        self.assertFalse(self.adapter.recognizes("https://careers-.icims.com/jobs/search"))
        self.assertFalse(self.adapter.recognizes("https://evil@careers-acme.icims.com/jobs/search"))
        self.assertFalse(self.adapter.recognizes("https://careers-acme.icims.com:8443/jobs/search"))
        self.assertFalse(self.adapter.recognizes("http://[invalid/jobs/search"))

    def test_identifies_search_page_and_canonicalizes_detail_to_board(self):
        search = self.adapter.identify_board(
            "https://careers-acme.icims.com/jobs/search-jsonld?ss=1#results"
        )
        detail = self.adapter.identify_board(
            "https://careers-acme.icims.com/jobs/123/data-analyst/job?mode=job"
        )

        self.assertEqual(search, JobBoard(
            url="https://careers-acme.icims.com/jobs/search-jsonld",
            provider="icims",
            identifier="careers-acme.icims.com",
        ))
        self.assertEqual(detail, JobBoard(
            url="https://careers-acme.icims.com/jobs/search",
            provider="icims",
            identifier="careers-acme.icims.com",
        ))

    def test_identifies_customer_owned_jibe_board_from_strong_page_evidence(self):
        page = Page(
            url="https://jobs.example.org/region/jobs",
            final_url="https://jobs.example.org/region/jobs",
            html=(
                '<html data-jibe-search-version="4.11">'
                '<script>window.searchConfig = {"externalSearch":true};</script>'
                '<script src="https://app.jibecdn.com/prod/search/4/main.js"></script>'
            ),
        )

        board = self.adapter.identify_board_from_page(page)

        self.assertEqual(board, JobBoard(
            url="https://jobs.example.org/region/jobs",
            provider="icims",
            identifier="jobs.example.org",
        ))
        weak_page = Page(
            url="https://example.org/jobs",
            html='<a href="https://www.icims.com/privacy">Privacy</a>',
        )
        self.assertIsNone(self.adapter.identify_board_from_page(weak_page))

    def test_lists_customer_owned_jibe_api_with_page_search_isolation(self):
        board_url = "https://jobs.example.org/region/jobs"
        board_html = (
            '<html data-jibe-search-version="4.11">'
            '<script src="https://app.jibecdn.com/prod/search/4/main.js"></script>'
            '<script>window.searchConfig = '
            '{"externalSearch":true,"searchOverride":'
            '{"brand":"Example Health","state":"New Mexico|NM"}};'
            '</script></html>'
        )
        api_payload = json.dumps({
            "count": 2,
            "totalCount": 2,
            "jobs": [
                {"data": {
                    "slug": "135333",
                    "title": "Registered Nurse / RN IMC",
                    "ats_code": "icims",
                    "full_location": "Albuquerque, New Mexico",
                    "meta_data": {
                        "canonical_url": "https://jobs.example.org/jobs/135333?lang=en-us"
                    },
                }},
                {"data": {
                    "slug": "outside",
                    "title": "External Role",
                    "ats_code": "other",
                    "meta_data": {
                        "canonical_url": "https://jobs.example.org/jobs/outside"
                    },
                }},
            ],
        })

        class JibeFetcher:
            def __init__(self):
                self.requested_urls = []

            def fetch(self, url, data=None, headers=None):
                self.requested_urls.append(url)
                html = board_html if url == board_url else api_payload
                return Page(url=url, final_url=url, html=html, source="jibe-fixture")

        fetcher = JibeFetcher()
        board = self.adapter.identify_board_from_page(
            Page(url=board_url, final_url=board_url, html=board_html)
        )

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title="Registered Nurse"))

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].url, "https://jobs.example.org/jobs/135333?lang=en-us")
        self.assertEqual(result.candidates[0].location, "Albuquerque, New Mexico")
        params = parse_qs(urlparse(fetcher.requested_urls[1]).query)
        self.assertEqual(params["keywords"], ["Registered Nurse"])
        self.assertEqual(params["brand"], ["Example Health"])
        self.assertEqual(params["state"], ["New Mexico|NM"])
        self.assertEqual(result.trace["variant"], "jibe")
        self.assertEqual(result.trace["search_override_keys"], ["brand", "state"])

    def test_rejects_customer_owned_jibe_api_cross_origin_redirect(self):
        board_url = "https://jobs.example.org/region/jobs"
        board_html = (
            '<html data-jibe-search-version="4.11">'
            '<script src="https://app.jibecdn.com/prod/search/4/main.js"></script>'
            '<script>window.searchConfig = {"externalSearch":true};</script></html>'
        )

        class RedirectFetcher:
            def fetch(self, url, data=None, headers=None):
                if url == board_url:
                    return Page(url=url, final_url=url, html=board_html)
                return Page(
                    url=url,
                    final_url="https://evil.example/api/jobs",
                    html='{"jobs":[]}',
                )

        board = self.adapter.identify_board_from_page(Page(url=board_url, html=board_html))
        result = self.adapter.list_jobs(RedirectFetcher(), board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertIn("redirected outside", result.trace["error"])

    def test_lists_json_ld_job_postings_and_normalizes_locations(self):
        fetcher = StubFetcher("""
            <script type="application/ld+json">
              {"@type":"ItemList","itemListElement":[
                {"@type":"JobPosting","title":" Data Analyst ",
                 "url":"/jobs/2345/data-analyst/job?utm_source=test",
                 "jobLocation":{"address":{"addressLocality":"New York",
                                             "addressRegion":"NY"}}},
                {"@type":"Organization","name":"Not a job","url":"/jobs/9/no/job"},
                {"@type":"JobPosting","title":"External",
                 "url":"https://example.com/jobs/1/external/job"}
              ]}
            </script>
        """)
        board = self.adapter.identify_board("https://careers-acme.icims.com/jobs/search")

        result = self.adapter.list_jobs(fetcher, board, JobQuery(title="Data Analyst"))

        self.assertEqual(fetcher.requested_urls, [board.url])
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, "Data Analyst")
        self.assertEqual(
            result.candidates[0].url,
            "https://careers-acme.icims.com/jobs/2345/data-analyst/job",
        )
        self.assertEqual(result.candidates[0].location, "New York, NY")
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.trace["structured_script_count"], 1)
        self.assertEqual(result.trace["candidate_count"], 1)

    def test_lists_embedded_jobs_and_builds_missing_detail_urls(self):
        fetcher = StubFetcher("""
            <script type="application/json">
              {"jobs":[
                {"id":"3456","title":"Data Analyst","location":"New York"},
                {"jobId":4567,"jobTitle":"ML Engineer",
                 "detailUrl":"/jobs/4567/ml-engineer/job",
                 "location":{"city":"Boston","state":"MA"}},
                {"id":"no-title","location":"Remote"}
              ],"navigation":{"id":"1","title":"Careers"}}
            </script>
        """)
        board = self.adapter.identify_board("https://careers-acme.icims.com/jobs/search-embedded")

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual([candidate.title for candidate in result.candidates], [
            "Data Analyst",
            "ML Engineer",
        ])
        self.assertEqual(
            result.candidates[0].url,
            "https://careers-acme.icims.com/jobs/3456/data-analyst/job",
        )
        self.assertEqual(result.candidates[0].location, "New York")
        self.assertEqual(result.candidates[1].location, "Boston, MA")
        self.assertEqual(result.candidates[0].raw["id"], "3456")

    def test_parses_json_wrapped_in_an_application_json_assignment(self):
        fetcher = StubFetcher("""
            <script type="application/json">
              window.ICIMS_JOBS = {"postings":[
                {"jobNumber":"7890","name":"Security Engineer"}
              ]};
            </script>
        """)
        board = self.adapter.identify_board("https://careers-acme.icims.com/jobs/search")

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(len(result.candidates), 1)
        self.assertIn("/jobs/7890/security-engineer/job", result.candidates[0].url)

    def test_deduplicates_job_postings_found_in_multiple_scripts(self):
        posting = (
            '{"@type":"JobPosting","title":"Data Analyst",'
            '"url":"/jobs/2345/data-analyst/job"}'
        )
        fetcher = StubFetcher(
            f'<script type="application/ld+json">{posting}</script>'
            f'<script type="application/ld+json">{posting}</script>'
        )
        board = self.adapter.identify_board("https://careers-acme.icims.com/jobs/search")

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(len(result.candidates), 1)

    def test_follows_hosted_search_pagination_and_reads_nested_payload_fixture(self):
        fixture_root = (
            ROOT / "samples" / "sites" / "careers-acme.icims.com" / "jobs" / "search-hosted"
        )
        first_url = "https://careers-acme.icims.com/jobs/search"
        second_url = "https://careers-acme.icims.com/jobs/search?pr=1"
        fetcher = FixtureMappingFetcher({
            first_url: fixture_root / "page-0.html",
            second_url: fixture_root / "page-1.html",
        })
        board = self.adapter.identify_board(first_url)

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(fetcher.requested_urls, [first_url, second_url])
        self.assertEqual(
            [candidate.title for candidate in result.candidates],
            ["Platform Engineer", "Security Analyst"],
        )
        self.assertEqual(result.candidates[0].location, "Austin, TX")
        self.assertEqual(result.candidates[1].location, "Remote")
        self.assertEqual(result.trace["page_count"], 2)
        self.assertEqual(result.trace["candidate_count"], 2)
        self.assertIn(
            "https://careers-other.icims.com/jobs/search?pr=2",
            result.trace["rejected_pagination_urls"],
        )
        self.assertIsNone(result.reason_code)

    def test_keeps_first_page_candidates_when_a_later_page_fetch_fails(self):
        fixture = (
            ROOT
            / "samples"
            / "sites"
            / "careers-acme.icims.com"
            / "jobs"
            / "search-hosted"
            / "page-0.html"
        )
        first_url = "https://careers-acme.icims.com/jobs/search"
        second_url = "https://careers-acme.icims.com/jobs/search?pr=1"
        fetcher = FixtureMappingFetcher(
            {first_url: fixture},
            errors={second_url: FetchError("page two blocked")},
        )
        board = self.adapter.identify_board(first_url)

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual([candidate.title for candidate in result.candidates], ["Platform Engineer"])
        self.assertIsNone(result.reason_code)
        self.assertFalse(result.retryable)
        self.assertEqual(result.trace["page_errors"], [
            {"url": second_url, "error": "page two blocked"},
        ])

    def test_rejects_cross_tenant_initial_redirect(self):
        class RedirectFetcher:
            def fetch(self, url, data=None, headers=None):
                return Page(
                    url=url,
                    final_url="https://careers-other.icims.com/jobs/search",
                    html='<script type="application/json">{"jobs":[]}</script>',
                    source="redirect-fixture",
                )

        board = self.adapter.identify_board("https://careers-acme.icims.com/jobs/search")

        result = self.adapter.list_jobs(RedirectFetcher(), board, JobQuery())

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.trace["page_count"], 0)
        self.assertEqual(
            result.trace["rejected_pagination_urls"],
            ["https://careers-other.icims.com/jobs/search"],
        )

    def test_rejects_mismatched_board_before_fetching(self):
        fetcher = StubFetcher()
        board = JobBoard(
            url="https://careers-other.icims.com/jobs/search",
            provider="icims",
            identifier="careers-acme.icims.com",
        )

        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(fetcher.requested_urls, [])
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_returns_structured_failures(self):
        unsupported = self.adapter.list_jobs(
            StubFetcher(),
            JobBoard(url="https://careers.icims.com/jobs/search", provider="icims"),
            JobQuery(),
        )
        failed = self.adapter.list_jobs(
            StubFetcher(error=FetchError("blocked")),
            JobBoard(
                url="https://careers-acme.icims.com/jobs/search",
                provider="icims",
                identifier="careers-acme.icims.com",
            ),
            JobQuery(),
        )
        empty = self.adapter.list_jobs(
            StubFetcher('<script type="application/json">not json</script>'),
            JobBoard(
                url="https://careers-acme.icims.com/jobs/search",
                provider="icims",
                identifier="careers-acme.icims.com",
            ),
            JobQuery(),
        )

        self.assertEqual(unsupported.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(failed.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(failed.retryable)
        self.assertEqual(empty.reason_code, "EMPTY_PROVIDER_RESPONSE")


if __name__ == "__main__":
    unittest.main()
