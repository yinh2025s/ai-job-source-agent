import unittest
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from job_source_agent.providers.base import JobQuery
from job_source_agent.providers.smartrecruiters import SmartRecruitersAdapter
from job_source_agent.web import FetchError, Fetcher, Page


ROOT = Path(__file__).resolve().parents[1]


def widget_html(
    company_code: str = "AcmeApi",
    *,
    script_url: str = "https://static.smartrecruiters.com/job-widget/1.6.1/script/smart_widget.js",
    api_url: str = "https://api.smartrecruiters.com/v1/companies/",
    job_ad_url: str = "https://jobs.smartrecruiters.com/",
) -> str:
    return (
        f'<script src="{script_url}"></script>'
        "<script>SmartRecruitersWidget.init({"
        f'company_code: "{company_code}", api_url: "{api_url}", '
        f'job_ad_url: "{job_ad_url}"'
        "});</script>"
    )


def legacy_widget_config(company_code: str, *, job_ad_url: str | None = None) -> str:
    job_ad_field = f', job_ad_url: "{job_ad_url}"' if job_ad_url is not None else ""
    return (
        "<script>SmartRecruitersWidget.init({"
        f'company_code: "{company_code}", '
        'api_url: "https://www.smartrecruiters.com"'
        f"{job_ad_field}"
        "});</script>"
    )


class _StaticFetcher:
    def __init__(self, html: str) -> None:
        self.html = html

    def fetch(self, url: str) -> Page:
        return Page(url=url, html=self.html, source="test")


class SmartRecruitersAdapterTests(unittest.TestCase):
    def test_identifies_static_widget_config_on_public_first_party_page(self):
        adapter = SmartRecruitersAdapter()
        page = Page(
            url="https://careers.example.com/open-positions?source=site",
            html=widget_html(),
        )

        board = adapter.identify_board_from_page(page)

        self.assertIsNotNone(board)
        self.assertEqual(board.url, "https://jobs.smartrecruiters.com/AcmeApi")
        self.assertEqual(board.identifier, "AcmeApi")
        self.assertEqual(board.provider, "smartrecruiters")
        self.assertTrue(board.replay_safe)

    def test_identifies_static_widget_assignment_config(self):
        adapter = SmartRecruitersAdapter()
        html = (
            '<script src="https://static.smartrecruiters.com/job-widget/widget.js"></script>'
            "<script>"
            "var company_code = 'AcmeApi';"
            "var api_url = 'https://api.smartrecruiters.com/v1/companies/';"
            "var job_ad_url = 'https://jobs.smartrecruiters.com/';"
            "</script>"
        )

        board = adapter.identify_board_from_page(
            Page("https://careers.example.com/open-positions", html)
        )

        self.assertIsNotNone(board)
        self.assertEqual(board.identifier, "AcmeApi")

    def test_identifies_multiple_legacy_widget_configs_with_optional_job_ad_url(self):
        adapter = SmartRecruitersAdapter()
        html = (
            '<script src="https://static.smartrecruiters.com/job-widget/widget.js"></script>'
            + legacy_widget_config("ExampleTenant")
            + legacy_widget_config(
                "exampletenant",
                job_ad_url="https://jobs.smartrecruiters.com/ExampleTenant",
            )
        )

        board = adapter.identify_board_from_page(
            Page("https://careers.example.com/open-positions", html)
        )

        self.assertIsNotNone(board)
        self.assertEqual(board.identifier, "ExampleTenant")
        self.assertEqual(
            board.url,
            "https://jobs.smartrecruiters.com/ExampleTenant",
        )

    def test_rejects_any_widget_config_missing_or_invalid_api_url(self):
        adapter = SmartRecruitersAdapter()
        marker = (
            '<script src="https://static.smartrecruiters.com/job-widget/widget.js"></script>'
        )
        cases = (
            marker
            + legacy_widget_config("ExampleTenant")
            + '<script>company_code: "ExampleTenant"</script>',
            marker
            + legacy_widget_config("ExampleTenant")
            + (
                '<script>company_code: "ExampleTenant", '
                'api_url: "https://evil.example/v1/companies/"</script>'
            ),
            marker
            + (
                '<script>company_code: "ExampleTenant", '
                'api_url: "https://www.smartrecruiters.com/jobs"</script>'
            ),
            marker
            + (
                "<script>"
                "SmartRecruitersWidget.init({"
                'company_code: "ExampleTenant", '
                'api_url: "https://www.smartrecruiters.com"});'
                'SmartRecruitersWidget.init({company_code: "ExampleTenant"});'
                "</script>"
            ),
        )

        for html in cases:
            with self.subTest(html=html):
                self.assertIsNone(
                    adapter.identify_board_from_page(
                        Page("https://careers.example.com/open-positions", html)
                    )
                )

    def test_rejects_conflicting_company_codes(self):
        adapter = SmartRecruitersAdapter()
        page = Page(
            url="https://careers.example.com/open-positions",
            html=widget_html() + widget_html("OtherTenant"),
        )

        self.assertIsNone(adapter.identify_board_from_page(page))

    def test_rejects_missing_or_forged_widget_script_marker(self):
        adapter = SmartRecruitersAdapter()
        pages = (
            Page(
                url="https://careers.example.com/open-positions",
                html=widget_html(script_url="https://evil.example/job-widget/widget.js"),
            ),
            Page(
                url="https://careers.example.com/open-positions",
                html=(
                    "<!-- <script src=\"https://static.smartrecruiters.com/"
                    "job-widget/widget.js\"></script> -->"
                    '<script>company_code: "AcmeApi", '
                    'api_url: "https://api.smartrecruiters.com/v1/companies/", '
                    'job_ad_url: "https://jobs.smartrecruiters.com/"</script>'
                ),
            ),
        )

        for page in pages:
            with self.subTest(html=page.html):
                self.assertIsNone(adapter.identify_board_from_page(page))

    def test_rejects_cross_tenant_config_urls(self):
        adapter = SmartRecruitersAdapter()
        pages = (
            Page(
                url="https://careers.example.com/open-positions",
                html=widget_html(
                    api_url="https://api.smartrecruiters.com/v1/companies/OtherTenant/postings"
                ),
            ),
            Page(
                url="https://careers.example.com/open-positions",
                html=widget_html(job_ad_url="https://jobs.smartrecruiters.com/OtherTenant"),
            ),
        )

        for page in pages:
            with self.subTest(html=page.html):
                self.assertIsNone(adapter.identify_board_from_page(page))

    def test_rejects_unsafe_page_and_widget_config_urls(self):
        adapter = SmartRecruitersAdapter()
        cases = (
            Page("http://careers.example.com/jobs", widget_html()),
            Page("https://user@careers.example.com/jobs", widget_html()),
            Page("https://127.0.0.1/jobs", widget_html()),
            Page(
                "https://careers.example.com/jobs",
                widget_html(script_url="https://user@static.smartrecruiters.com/job-widget/widget.js"),
            ),
            Page(
                "https://careers.example.com/jobs",
                widget_html(api_url="https://api.smartrecruiters.com.evil.example/v1/companies/"),
            ),
            Page(
                "https://careers.example.com/jobs",
                widget_html(job_ad_url="http://jobs.smartrecruiters.com/"),
            ),
        )

        for page in cases:
            with self.subTest(url=page.url, html=page.html):
                self.assertIsNone(adapter.identify_board_from_page(page))

    def test_recognizes_only_public_job_board_host(self):
        adapter = SmartRecruitersAdapter()

        self.assertTrue(adapter.recognizes("https://jobs.smartrecruiters.com/AcmeCorp"))
        self.assertFalse(adapter.recognizes("https://api.smartrecruiters.com/v1/companies/AcmeCorp"))
        self.assertFalse(adapter.recognizes("https://smartrecruiters.com.example.com/AcmeCorp"))
        self.assertFalse(adapter.recognizes("https://user@jobs.smartrecruiters.com/AcmeCorp"))
        self.assertFalse(adapter.recognizes("https://jobs.smartrecruiters.com:8443/AcmeCorp"))

    def test_identifies_company_from_board_or_detail_url(self):
        adapter = SmartRecruitersAdapter()

        board = adapter.identify_board(
            "https://jobs.smartrecruiters.com/AcmeCorp/743999999999999-data-analyst"
        )

        self.assertEqual(board.identifier, "AcmeCorp")
        self.assertEqual(board.provider, "smartrecruiters")

    def test_lists_fixture_candidates_and_preserves_fallback_detail_url(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")

        result = adapter.list_jobs(
            Fetcher(fixtures_dir=ROOT / "samples" / "sites", offline=True),
            board,
            JobQuery(title="Data Analyst"),
        )

        self.assertIsNone(result.reason_code)
        self.assertEqual([candidate.title for candidate in result.candidates], ["Data Analyst", "Sales Manager"])
        self.assertEqual(
            result.candidates[0].url,
            "https://jobs.smartrecruiters.com/AcmeApi/743999111111111-data-analyst",
        )
        self.assertEqual(
            result.candidates[1].url,
            "https://jobs.smartrecruiters.com/AcmeApi/743999222222222-sales-manager",
        )
        self.assertEqual(result.trace["candidate_count"], 2)
        self.assertEqual(result.trace["exposed_candidate_count"], 2)
        self.assertFalse(result.trace["tenant_identity_verified"])
        self.assertFalse(result.trace["tenant_identity_conflict"])

    def test_marks_matching_record_company_identity_as_verified_tenant(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")
        payload = json.dumps(
            {
                "totalFound": 1,
                "limit": 100,
                "content": [
                    {
                        "name": "Backend Engineer",
                        "id": "job-1",
                        "company": {"identifier": "AcmeApi", "name": "Acme API"},
                    }
                ],
            }
        )

        result = adapter.list_jobs(_StaticFetcher(payload), board, JobQuery())

        self.assertTrue(result.trace["tenant_identity_verified"])
        self.assertEqual(result.candidates[0].raw["company_identifier"], "AcmeApi")

    def test_uses_keyword_query_and_follows_bounded_offset_pages(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")

        class PagedFetcher:
            def __init__(self):
                self.requested_urls = []

            def fetch(self, url):
                self.requested_urls.append(url)
                params = parse_qs(urlparse(url).query)
                offset = int(params.get("offset", ["0"])[0])
                payload = {
                    "totalFound": 2,
                    "limit": 1,
                    "offset": offset,
                    "content": [
                        {
                            "name": "Data Analyst" if offset == 0 else "Senior Data Analyst",
                            "id": f"job-{offset}",
                            "company": {"identifier": "AcmeApi"},
                        }
                    ],
                }
                return Page(url=url, final_url=url, html=json.dumps(payload), source="paged")

        fetcher = PagedFetcher()
        result = adapter.list_jobs(fetcher, board, JobQuery(title="Target Analyst"))

        first_params = parse_qs(urlparse(fetcher.requested_urls[0]).query)
        second_params = parse_qs(urlparse(fetcher.requested_urls[1]).query)
        self.assertEqual(first_params["q"], ["Target Analyst"])
        self.assertNotIn("offset", first_params)
        self.assertEqual(second_params["offset"], ["1"])
        self.assertEqual(result.trace["page_count"], 2)
        self.assertEqual(result.trace["total_found"], 2)
        self.assertFalse(result.trace["exact_title_found"])
        self.assertEqual(len(result.candidates), 2)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.trace["inventory_complete"], result.inventory_complete)

    def test_exact_normalized_title_does_not_bypass_inventory_completeness(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")

        class ExactFetcher:
            def __init__(self):
                self.requested_urls = []

            def fetch(self, url):
                self.requested_urls.append(url)
                return Page(
                    url=url,
                    final_url=url,
                    html=json.dumps({
                        "totalFound": 300,
                        "limit": 100,
                        "offset": 0,
                        "content": [{
                            "name": "  Data   Analyst ",
                            "id": "job-1",
                            "company": {"identifier": "AcmeApi"},
                        }],
                    }),
                )

        fetcher = ExactFetcher()
        result = adapter.list_jobs(fetcher, board, JobQuery(title="data analyst"))

        self.assertEqual(len(fetcher.requested_urls), 5)
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(result.candidates, [])
        self.assertFalse(result.inventory_complete)

    def test_later_fetch_failure_hides_candidate_and_is_incomplete(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")

        class PartialFetcher:
            def __init__(self):
                self.calls = 0

            def fetch(self, url):
                self.calls += 1
                if self.calls == 2:
                    raise FetchError("page two unavailable")
                return Page(
                    url=url,
                    final_url=url,
                    html=json.dumps({
                        "totalFound": 2,
                        "limit": 1,
                        "offset": 0,
                        "content": [{
                            "name": "First Role",
                            "id": "job-1",
                            "company": {"identifier": "AcmeApi"},
                        }],
                    }),
                )

        result = adapter.list_jobs(PartialFetcher(), board, JobQuery(title="Missing Role"))

        self.assertEqual(result.candidates, [])
        self.assertIsNone(result.reason_code)
        self.assertFalse(result.inventory_complete)
        self.assertFalse(result.trace["inventory_complete"])

    def test_page_cap_with_remaining_total_is_incomplete(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")

        class CappedFetcher:
            def fetch(self, url):
                offset = int(parse_qs(urlparse(url).query).get("offset", ["0"])[0])
                return Page(
                    url=url,
                    final_url=url,
                    html=json.dumps({
                        "totalFound": 6,
                        "limit": 1,
                        "offset": offset,
                        "content": [{
                            "name": f"Role {offset}",
                            "id": f"job-{offset}",
                            "company": {"identifier": "AcmeApi"},
                        }],
                    }),
                )

        result = adapter.list_jobs(CappedFetcher(), board, JobQuery())

        self.assertEqual(result.trace["page_count"], 5)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["candidate_count"], 5)
        self.assertFalse(result.inventory_complete)
        self.assertFalse(result.trace["inventory_complete"])

    def test_rejects_cross_company_api_redirect(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")

        class RedirectFetcher:
            def fetch(self, url):
                return Page(
                    url=url,
                    final_url="https://api.smartrecruiters.com/v1/companies/Other/postings",
                    html='{"content":[]}',
                )

        result = adapter.list_jobs(RedirectFetcher(), board, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertIn("redirected outside", result.trace["error"])

    def test_rejects_cross_company_detail_without_safe_id_fallback(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")
        fetcher = _StaticFetcher(
            '{"content":[{"name":"External","actions":'
            '{"details":"https://jobs.smartrecruiters.com/Other/job-1"}}]}'
        )

        result = adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")

    def test_normalizes_location_and_relative_detail_url(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")
        fetcher = _StaticFetcher(
            '{"content":[{"name":"ML Engineer","id":"job-1",'
            '"company":{"identifier":"AcmeApi"},'
            '"location":{"city":"Paris","region":"Ile-de-France","country":"FR"},'
            '"actions":{"details":"/AcmeApi/job-1"}}]}'
        )

        result = adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(result.candidates[0].location, "Paris, Ile-de-France, FR")
        self.assertEqual(result.candidates[0].url, "https://jobs.smartrecruiters.com/AcmeApi/job-1")

    def test_reports_invalid_structured_data(self):
        adapter = SmartRecruitersAdapter()
        board = adapter.identify_board("https://jobs.smartrecruiters.com/AcmeApi")

        result = adapter.list_jobs(_StaticFetcher("not json"), board, JobQuery())

        self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(result.candidates, [])


if __name__ == "__main__":
    unittest.main()
