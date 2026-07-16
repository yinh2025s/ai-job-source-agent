import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.paycor import ADAPTER, PaycorAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.web import FetchError, Page


CLIENT_ID = "8a7883c68c602471018c6ec39f3516d5"
OTHER_CLIENT_ID = "8a7883c67f28b4a1017f41e29ef10943"
JOB_ID = "8a7885a888e0b78b0188fe7589c9103e"
OTHER_JOB_ID = "8a7887a878279a000178375cbd607caa"
BOARD_URL = (
    "https://recruitingbypaycor.com/career/CareerHome.action?clientId=" + CLIENT_ID
)


def jobs_html(rows="", form=""):
    return f'<div id="gnewtonCareerBody">{form}{rows}</div>'


def job_row(title, location, href):
    return (
        '<div class="gnewtonCareerGroupRowClass">'
        '<div class="gnewtonCareerGroupJobTitleClass">'
        f'<a href="{href}">{title}</a>'
        "</div>"
        f'<div class="gnewtonCareerGroupJobDescriptionClass">{location}</div>'
        "</div>"
    )


def current_job_row(title, location, href):
    return (
        '<tr><td class="gnewtonJobLink">'
        f'<a href="{href}">{title}</a></td>'
        f'<td class="gnewtonJobLocation">{location}</td></tr>'
    )


class RecordingFetcher:
    def __init__(self, pages=None, error=None):
        self.pages = pages or {}
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error:
            raise self.error
        if url not in self.pages:
            raise FetchError(f"unexpected URL: {url}")
        return self.pages[url]


class PaycorAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = PaycorAdapter()
        self.board = JobBoard(
            BOARD_URL,
            "paycor",
            f"recruitingbypaycor.com|{CLIENT_ID}",
        )

    def test_native_adapter_is_discovered_and_canonicalizes_supported_urls(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["paycor"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        for url in (
            f"https://recruitingbypaycor.com/career/iframe.action?clientId={CLIENT_ID}",
            BOARD_URL,
            BOARD_URL + "&specialization=&source=careers",
            (
                "https://recruitingbypaycor.com/career/JobIntroduction.action"
                f"?clientId={CLIENT_ID}&id={JOB_ID}&source=&lang=en"
            ),
        ):
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)

    def test_identifies_single_script_embedded_board_from_first_party_page(self):
        page = Page(
            "https://company.example/career-openings",
            '<script id="gnewtonjs" '
            f'src="//recruitingbypaycor.com/career/iframe.action?clientId={CLIENT_ID}"></script>',
        )

        discovered = self.adapter.identify_board_from_page(page)
        self.assertIsNotNone(discovered)
        assert discovered is not None
        self.assertEqual(discovered.identifier, self.board.identifier)
        self.assertIn("parentUrl=https%3A%2F%2Fcompany.example%2Fcareer-openings", discovered.url)

        result = self.adapter.list_jobs(
            RecordingFetcher({discovered.url: Page(discovered.url, jobs_html())}),
            discovered,
            JobQuery(title="Account Executive"),
        )
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertNotEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_rejects_ambiguous_or_unsafe_script_embedded_boards(self):
        page = Page(
            "https://company.example/career-openings",
            (
                '<script src="https://evil.test/career/iframe.action?clientId='
                f'{CLIENT_ID}"></script>'
                '<script src="https://recruitingbypaycor.com/career/iframe.action?clientId='
                f'{CLIENT_ID}"></script>'
                '<script src="https://recruitingbypaycor.com/career/iframe.action?clientId='
                f'{OTHER_CLIENT_ID}"></script>'
            ),
        )

        self.assertIsNone(self.adapter.identify_board_from_page(page))

    def test_rejects_unsafe_hosts_clients_and_queries(self):
        urls = (
            f"http://recruitingbypaycor.com/career/CareerHome.action?clientId={CLIENT_ID}",
            f"https://user@recruitingbypaycor.com/career/CareerHome.action?clientId={CLIENT_ID}",
            f"https://recruitingbypaycor.com.evil.test/career/CareerHome.action?clientId={CLIENT_ID}",
            "https://recruitingbypaycor.com/career/CareerHome.action?clientId=../../admin",
            BOARD_URL + f"&clientId={OTHER_CLIENT_ID}",
            BOARD_URL + "&parentUrl=http%3A%2F%2F127.0.0.1%2Fcareers",
            BOARD_URL + "&next=https%3A%2F%2Fevil.test",
            (
                "https://recruitingbypaycor.com/career/JobIntroduction.action"
                f"?clientId={CLIENT_ID}&id=javascript%3Aalert%281%29"
            ),
            (
                "https://recruitingbypaycor.com/career/JobIntroduction.action"
                f"?clientId={CLIENT_ID}&id={JOB_ID}&lang=en&redirect=https%3A%2F%2Fevil.test"
            ),
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_lists_public_jobs_with_titles_locations_and_canonical_details(self):
        first_href = (
            "/career/JobIntroduction.action"
            f"?clientId={CLIENT_ID}&id={JOB_ID}&source=&lang=en"
        )
        second_href = (
            "https://recruitingbypaycor.com/career/JobIntroduction.action"
            f"?clientId={CLIENT_ID}&id={OTHER_JOB_ID}&source=careers&lang=en"
        )
        html = jobs_html(
            job_row("AI Engineer", "Atlanta, GA", first_href)
            + job_row("Program Manager", "Remote", second_href)
        )
        fetcher = RecordingFetcher(
            {BOARD_URL: Page(url=BOARD_URL, html=html, source="paycor-contract")}
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="AI Engineer"),
        )

        self.assertIsNone(result.reason_code)
        self.assertEqual(
            [(candidate.title, candidate.location) for candidate in result.candidates],
            [("AI Engineer", "Atlanta, GA"), ("Program Manager", "Remote")],
        )
        self.assertEqual(
            result.candidates[0].url,
            (
                "https://recruitingbypaycor.com/career/JobIntroduction.action"
                f"?clientId={CLIENT_ID}&id={JOB_ID}"
            ),
        )
        self.assertEqual(result.candidates[0].raw["job_id"], JOB_ID)
        self.assertEqual(result.inventory_scope, "full")
        self.assertTrue(result.trace["exact_title_found"])

    def test_lists_current_table_variant_with_location(self):
        href = (
            "/career/JobIntroduction.action"
            f"?clientId={CLIENT_ID}&id={JOB_ID}&source=&lang=en"
        )
        fetcher = RecordingFetcher({
            BOARD_URL: Page(
                BOARD_URL,
                '<div id="gnewtonCareerBody"><table>'
                + current_job_row("Account Executive", "Salt Lake City, UT", href)
                + "</table></div>",
            )
        })

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Account Executive", location="Salt Lake City, UT"),
        )

        self.assertIsNone(result.reason_code)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, "Account Executive")
        self.assertEqual(result.candidates[0].location, "Salt Lake City, UT")

    def test_rejects_cross_client_cross_origin_and_malicious_detail_links(self):
        rows = "".join(
            (
                job_row(
                    "Wrong client",
                    "Remote",
                    (
                        "/career/JobIntroduction.action"
                        f"?clientId={OTHER_CLIENT_ID}&id={JOB_ID}"
                    ),
                ),
                job_row(
                    "Wrong origin",
                    "Remote",
                    f"https://evil.test/career/JobIntroduction.action?clientId={CLIENT_ID}&id={JOB_ID}",
                ),
                job_row(
                    "Injected query",
                    "Remote",
                    (
                        "/career/JobIntroduction.action"
                        f"?clientId={CLIENT_ID}&id={JOB_ID}&next=https%3A%2F%2Fevil.test"
                    ),
                ),
                job_row("Script", "Remote", "javascript:alert(1)"),
            )
        )
        result = self.adapter.list_jobs(
            RecordingFetcher({BOARD_URL: Page(url=BOARD_URL, html=jobs_html(rows))}),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.trace["rejected_link_count"], 4)

    def test_uses_only_a_discovered_safe_get_search_form(self):
        form = (
            '<form method="GET" action="/career/CareerHomeSearch.action">'
            f'<input type="hidden" name="clientId" value="{CLIENT_ID}" />'
            '<input id="gnewtonKeyword" name="keyword" value="" />'
            '<input type="hidden" name="source" value="careers" />'
            "</form>"
        )
        search_url = (
            "https://recruitingbypaycor.com/career/CareerHomeSearch.action?"
            f"clientId={CLIENT_ID}&keyword=AI+Engineer&source=careers"
        )
        fetcher = RecordingFetcher(
            {
                BOARD_URL: Page(url=BOARD_URL, html=jobs_html(form=form)),
                search_url: Page(
                    url=search_url,
                    html=jobs_html(
                        job_row(
                            "AI Engineer",
                            "Remote",
                            (
                                "/career/JobIntroduction.action"
                                f"?clientId={CLIENT_ID}&id={JOB_ID}"
                            ),
                        )
                    ),
                ),
            }
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="AI Engineer"),
        )

        self.assertIsNone(result.reason_code)
        self.assertEqual([request[0] for request in fetcher.requests], [BOARD_URL, search_url])
        self.assertTrue(result.trace["used_get_search_form"])
        self.assertEqual(result.inventory_scope, "title_filtered")

    def test_does_not_guess_post_or_untrusted_search_parameters(self):
        post_form = (
            '<form method="POST" action="/career/CareerHomeSearch.action">'
            f'<input type="hidden" name="clientId" value="{CLIENT_ID}" />'
            '<input name="keyword" value="" />'
            "</form>"
        )
        malicious_get = (
            '<form method="GET" action="/career/CareerHomeSearch.action">'
            f'<input type="hidden" name="clientId" value="{CLIENT_ID}" />'
            '<input name="redirect" value="https://evil.test" />'
            "</form>"
        )
        for form in (post_form, malicious_get):
            with self.subTest(form=form):
                fetcher = RecordingFetcher(
                    {BOARD_URL: Page(url=BOARD_URL, html=jobs_html(form=form))}
                )
                result = self.adapter.list_jobs(
                    fetcher,
                    self.board,
                    JobQuery(title="AI Engineer"),
                )
                self.assertEqual(len(fetcher.requests), 1)
                self.assertFalse(result.trace["used_get_search_form"])

    def test_rejects_cross_origin_and_cross_client_redirects(self):
        for final_url in (
            f"https://evil.test/career/CareerHome.action?clientId={CLIENT_ID}",
            (
                "https://recruitingbypaycor.com/career/CareerHome.action"
                f"?clientId={OTHER_CLIENT_ID}"
            ),
        ):
            with self.subTest(final_url=final_url):
                result = self.adapter.list_jobs(
                    RecordingFetcher(
                        {
                            BOARD_URL: Page(
                                url=BOARD_URL,
                                final_url=final_url,
                                html=jobs_html(),
                            )
                        }
                    ),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_rejects_forged_board_and_reports_fetch_failure(self):
        forged = JobBoard(
            BOARD_URL.replace(CLIENT_ID, OTHER_CLIENT_ID),
            "paycor",
            f"recruitingbypaycor.com|{CLIENT_ID}",
        )
        invalid = self.adapter.list_jobs(RecordingFetcher(), forged, JobQuery())
        failed = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("timeout")),
            self.board,
            JobQuery(),
        )

        self.assertEqual(invalid.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(failed.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(failed.retryable)


if __name__ == "__main__":
    unittest.main()
