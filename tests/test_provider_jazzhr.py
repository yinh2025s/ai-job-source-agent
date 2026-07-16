import unittest

from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.jazzhr import ADAPTER, JazzHRAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.web import FetchError, Page, extract_links


CURRENT_BOARD_URL = "https://kodiakcakes.applytojob.com/apply/"
LEGACY_BOARD_URL = "https://acme.applytojob.com/apply/jobs/"
WIDGET_URL = "https://app.jazz.co/widgets/basic/create/kodiakcakes"


def current_jobs_html(links=""):
    return (
        '<body class="resumator-jobboard-home jobboard job- dept-">'
        '<div class="job-board-list"><div class="jobs-list">'
        f"{links}</div></div>"
        '<footer><a id="resumator-logo" href="https://info.jazzhr.com/job-seekers.html">'
        "Powered by JazzHR</a></footer></body>"
    )


def current_job(title, href):
    return (
        '<li class="list-group-item"><h3 class="list-group-item-heading">'
        f'<a href="{href}">{title}</a></h3></li>'
    )


def legacy_jobs_html(links=""):
    return (
        '<div id="resumator_main_wrapper">'
        '<div id="resumator_container_body">'
        '<form action="/apply/jobs" method="GET"></form>'
        f'<table id="jobs_table">{links}</table>'
        '<a id="resumator-logo" href="https://info.jazzhr.com/job-seekers.html">JazzHR</a>'
        "</div></div>"
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


class JazzHRAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = JazzHRAdapter()
        self.current_board = JobBoard(CURRENT_BOARD_URL, "jazzhr", "kodiakcakes")
        self.legacy_board = JobBoard(LEGACY_BOARD_URL, "jazzhr", "acme")

    def test_native_adapter_maps_widget_and_canonicalizes_both_public_routes(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["jazzhr"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        cases = (
            (WIDGET_URL, self.current_board),
            (CURRENT_BOARD_URL, self.current_board),
            (
                "https://kodiakcakes.applytojob.com/apply/yIwicggW46/Regional-Account-Manager-Sales-South-Central?source=careers",
                self.current_board,
            ),
            (LEGACY_BOARD_URL, self.legacy_board),
            (
                "https://acme.applytojob.com/apply/jobs/details/Abc_123-xy?source=careers",
                self.legacy_board,
            ),
        )
        for url, expected_board in cases:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), expected_board)

    def test_rejects_unsafe_widget_board_and_detail_urls(self):
        for url in (
            "http://app.jazz.co/widgets/basic/create/kodiakcakes",
            "https://user@app.jazz.co/widgets/basic/create/kodiakcakes",
            "https://app.jazz.co:8443/widgets/basic/create/kodiakcakes",
            "https://app.jazz.co/widgets/basic/create/kodiakcakes/extra",
            "https://app.jazz.co/widgets/basic/create/kodiakcakes?tenant=other",
            "https://app.jazz.co.evil.example/widgets/basic/create/kodiakcakes",
            "http://acme.applytojob.com/apply/",
            "https://applytojob.com/apply/",
            "https://user@acme.applytojob.com/apply/",
            "https://acme.applytojob.com:8443/apply/",
            "https://acme.applytojob.com/about",
            "https://applytojob.com.evil.example/apply/jobs/",
            "https://acme.applytojob.com/apply/embed/form/Abcd1234",
            "https://acme.applytojob.com/apply/Abcd1234/bad%2Fslug",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_lists_kodiak_current_inventory_with_canonical_detail_urls(self):
        html = current_jobs_html(
            current_job(
                "Regional Account Manager, Sales - South Central",
                "https://kodiakcakes.applytojob.com/apply/yIwicggW46/Regional-Account-Manager-Sales-South-Central?source=Our%20Career%20Page%20Widget",
            )
            + current_job(
                "Temporary Research &amp; Development Lab Assistant",
                "/apply/CSQHC59vRs/Temporary-Research-Development-Lab-Assistant/",
            )
        )
        fetcher = RecordingFetcher({
            CURRENT_BOARD_URL: Page(
                url=CURRENT_BOARD_URL,
                html=html,
                source="kodiak-jazzhr-contract",
            )
        })

        result = self.adapter.list_jobs(
            fetcher,
            self.current_board,
            JobQuery(title="Regional Account Manager, Sales - South Central"),
        )

        self.assertIsNone(result.reason_code)
        self.assertEqual(
            [candidate.title for candidate in result.candidates],
            [
                "Regional Account Manager, Sales - South Central",
                "Temporary Research & Development Lab Assistant",
            ],
        )
        self.assertEqual(
            result.candidates[0].url,
            "https://kodiakcakes.applytojob.com/apply/yIwicggW46/Regional-Account-Manager-Sales-South-Central",
        )
        self.assertEqual(result.candidates[0].raw["job_id"], "yIwicggW46")
        self.assertTrue(result.trace["exact_title_found"])
        self.assertEqual(result.trace["inventory_scope"], "full")
        self.assertEqual(result.trace["variant"], "public_current_html")

    def test_lists_legacy_inventory_and_normalizes_official_detail_urls(self):
        html = legacy_jobs_html(
            '<a class="job_title_link" href="/apply/jobs/details/9ZW2SJ880l?&">AI Programmer</a>'
            '<a class="job_title_link featured" href="https://acme.applytojob.com/apply/jobs/details/mv8Xr5KgTK/">Program Manager</a>'
        )
        result = self.adapter.list_jobs(
            RecordingFetcher({LEGACY_BOARD_URL: Page(url=LEGACY_BOARD_URL, html=html)}),
            self.legacy_board,
            JobQuery(title="AI Programmer"),
        )

        self.assertIsNone(result.reason_code)
        self.assertEqual(
            [candidate.url for candidate in result.candidates],
            [
                "https://acme.applytojob.com/apply/jobs/details/9ZW2SJ880l",
                "https://acme.applytojob.com/apply/jobs/details/mv8Xr5KgTK",
            ],
        )
        self.assertEqual(result.trace["variant"], "public_legacy_html")
        self.assertEqual(result.trace["inventory_scope"], "full")

    def test_rejects_cross_tenant_non_detail_and_unsafe_inventory_links(self):
        html = current_jobs_html(
            current_job(
                "Wrong tenant",
                "https://other.applytojob.com/apply/Abcd1234/Wrong-Tenant",
            )
            + current_job("Board", "/apply/")
            + current_job("Script URL", "javascript:alert(1)")
            + current_job("Credentials", "https://user@kodiakcakes.applytojob.com/apply/Abcd1234/Credentials")
            + current_job("Unsafe port", "https://kodiakcakes.applytojob.com:8443/apply/Abcd1234/Unsafe-Port")
            + current_job("Embed", "/apply/embed/form/Abcd1234")
        )
        result = self.adapter.list_jobs(
            RecordingFetcher({CURRENT_BOARD_URL: Page(url=CURRENT_BOARD_URL, html=html)}),
            self.current_board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.trace["rejected_link_count"], 6)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "partial")
        self.assertFalse(result.trace["inventory_complete"])

    def test_rejects_cross_tenant_or_route_redirect_and_weak_pages(self):
        redirected = self.adapter.list_jobs(
            RecordingFetcher({
                CURRENT_BOARD_URL: Page(
                    url=CURRENT_BOARD_URL,
                    final_url="https://other.applytojob.com/apply/",
                    html=current_jobs_html(),
                )
            }),
            self.current_board,
            JobQuery(),
        )
        wrong_route = self.adapter.list_jobs(
            RecordingFetcher({
                CURRENT_BOARD_URL: Page(
                    url=CURRENT_BOARD_URL,
                    final_url="https://kodiakcakes.applytojob.com/apply/jobs/",
                    html=legacy_jobs_html(),
                )
            }),
            self.current_board,
            JobQuery(),
        )
        weak = self.adapter.list_jobs(
            RecordingFetcher({
                CURRENT_BOARD_URL: Page(
                    url=CURRENT_BOARD_URL,
                    html='<div class="job-board-list jobs-list"><h1>Current Openings</h1></div>',
                )
            }),
            self.current_board,
            JobQuery(),
        )

        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(wrong_route.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(weak.reason_code, "INVALID_STRUCTURED_DATA")

    def test_requires_complete_variant_specific_fingerprint(self):
        current_without_brand = current_jobs_html().replace(
            '<footer><a id="resumator-logo" href="https://info.jazzhr.com/job-seekers.html">'
            "Powered by JazzHR</a></footer>",
            "",
        )
        legacy_without_table = legacy_jobs_html().replace('id="jobs_table"', "")

        current = self.adapter.list_jobs(
            RecordingFetcher({
                CURRENT_BOARD_URL: Page(url=CURRENT_BOARD_URL, html=current_without_brand)
            }),
            self.current_board,
            JobQuery(),
        )
        legacy = self.adapter.list_jobs(
            RecordingFetcher({
                LEGACY_BOARD_URL: Page(url=LEGACY_BOARD_URL, html=legacy_without_table)
            }),
            self.legacy_board,
            JobQuery(),
        )

        self.assertEqual(current.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(legacy.reason_code, "INVALID_STRUCTURED_DATA")

    def test_rejects_forged_board_contract(self):
        for board in (
            JobBoard(CURRENT_BOARD_URL, "jazzhr", "other"),
            JobBoard("https://kodiakcakes.applytojob.com/apply/jobs", "jazzhr", "kodiakcakes"),
            JobBoard(CURRENT_BOARD_URL, "other", "kodiakcakes"),
        ):
            with self.subTest(board=board):
                result = self.adapter.list_jobs(RecordingFetcher(), board, JobQuery())
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_returns_retryable_fetch_failure(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("timeout")),
            self.current_board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(result.retryable)

    def test_first_party_widget_bootstrap_extracts_and_resolves_to_kodiak_board(self):
        career_url = "https://kodiakcakes.example/work-with-us"
        page = Page(
            url=career_url,
            html=f'<script type="text/javascript" src="{WIDGET_URL}"></script>',
        )

        widget_links = [link.url for link in extract_links(page) if link.url == WIDGET_URL]

        self.assertEqual(widget_links, [WIDGET_URL])
        self.assertEqual(self.adapter.identify_board(widget_links[0]), self.current_board)
        self.assertEqual(self.adapter.identify_board_from_page(page), self.current_board)

    def test_first_party_widget_bootstrap_rejects_ambiguous_or_unstructured_mentions(self):
        ambiguous = Page(
            url="https://kodiakcakes.example/work-with-us",
            html=(
                f'<script src="{WIDGET_URL}"></script>'
                '<script src="https://app.jazz.co/widgets/basic/create/other"></script>'
            ),
        )
        unstructured = Page(
            url="https://kodiakcakes.example/work-with-us",
            html=f'<p>{WIDGET_URL}</p>',
        )

        self.assertIsNone(self.adapter.identify_board_from_page(ambiguous))
        self.assertIsNone(self.adapter.identify_board_from_page(unstructured))

    def test_first_party_widget_bootstrap_traverses_to_kodiak_board(self):
        career_url = "https://kodiakcakes.example/work-with-us"
        fetcher = RecordingFetcher({
            career_url: Page(
                url=career_url,
                html=f'<script type="text/javascript" src="{WIDGET_URL}"></script>',
            ),
            CURRENT_BOARD_URL: Page(url=CURRENT_BOARD_URL, html=current_jobs_html()),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career_url)

        self.assertEqual(job_list, CURRENT_BOARD_URL)
        self.assertEqual(trace["provider"], "jazzhr")


if __name__ == "__main__":
    unittest.main()
