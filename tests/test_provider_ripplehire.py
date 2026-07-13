import json
import unittest
from urllib.parse import parse_qs

from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.providers.ripplehire import ADAPTER, RippleHireAdapter
from job_source_agent.web import FetchError, Page


BOARD_URL = "https://acme.ripplehire.com/ripplehire/careers"
API_URL = "https://acme.ripplehire.com/candidate/candidatejobsearch"
TOKEN = "fixture-public-routing-token"


def portal_html(token=TOKEN, source="CAREERSITE"):
    return (
        f'<input type="hidden" id="token" value="{token}">'
        f'<input type="hidden" id="source" value="{source}">'
    )


def response_xml(jobs, total=None):
    records = "".join(
        "<jobVoList>"
        f"<jobSeq>{job['id']}</jobSeq>"
        f"<jobTitle>{job['title']}</jobTitle>"
        f"<locations>{job.get('location', '')}</locations>"
        f"<jobCode>{job.get('code', '')}</jobCode>"
        "</jobVoList>"
        for job in jobs
    )
    return (
        "<JobPageVO><startJobIndex>0</startJobIndex><maxJobSize>20</maxJobSize>"
        f"<totalJobCount>{len(jobs) if total is None else total}</totalJobCount>"
        f"<jobVoList>{records}</jobVoList><errorStatus>success</errorStatus></JobPageVO>"
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
        page = self.pages.get(url)
        if page is None:
            raise FetchError(f"unexpected URL: {url}")
        return page


class RippleHireAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = RippleHireAdapter()
        self.board = JobBoard(BOARD_URL, "ripplehire", "acme.ripplehire.com")

    def test_native_adapter_is_discovered_and_canonicalizes_public_urls(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["ripplehire"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        for url in (
            BOARD_URL,
            f"https://acme.ripplehire.com/candidate/?token={TOKEN}&source=CAREERSITE#list",
            f"https://acme.ripplehire.com/candidate/?token={TOKEN}&source=CAREERSITE#detail/job/42",
        ):
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)

        self.assertFalse(self.adapter.recognizes("http://acme.ripplehire.com/ripplehire/careers"))
        self.assertFalse(self.adapter.recognizes("https://ripplehire.com/ripplehire/careers"))
        self.assertFalse(self.adapter.recognizes("https://evil.example/ripplehire/careers"))
        self.assertFalse(self.adapter.recognizes("https://user@acme.ripplehire.com/ripplehire/careers"))

    def test_lists_title_filtered_jobs_without_exposing_token_in_trace(self):
        fetcher = RecordingFetcher({
            BOARD_URL: Page(
                url=BOARD_URL,
                final_url=f"https://acme.ripplehire.com/candidate/?token={TOKEN}&source=CAREERSITE",
                html=portal_html(),
                source="ripplehire-contract",
            ),
            API_URL: Page(
                url=API_URL,
                html=response_xml([{"id": "42", "title": "Data Analyst", "location": "Pune"}]),
            ),
        })

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="Data Analyst"))

        self.assertIsNone(result.reason_code)
        self.assertEqual(result.candidates[0].title, "Data Analyst")
        self.assertEqual(result.candidates[0].location, "Pune")
        self.assertEqual(
            result.candidates[0].url,
            f"https://acme.ripplehire.com/candidate/?token={TOKEN}&source=CAREERSITE#detail/job/42",
        )
        form = parse_qs(fetcher.requests[1][1].decode())
        payload = json.loads(form["careerSiteUrlParams"][0])
        self.assertEqual(payload["search"], "Analyst")
        self.assertEqual(payload["pagesize"], 50)
        self.assertEqual(fetcher.requests[1][2]["Content-Type"], "application/x-www-form-urlencoded; charset=UTF-8")
        self.assertNotIn(TOKEN, json.dumps(result.trace))
        self.assertEqual(result.trace["inventory_scope"], "title_filtered")
        self.assertTrue(result.inventory_complete)
        self.assertTrue(result.trace["inventory_complete"])

    def test_normalizes_provider_search_syntax_without_changing_match_title(self):
        fetcher = RecordingFetcher({
            BOARD_URL: Page(url=BOARD_URL, html=portal_html()),
            API_URL: Page(url=API_URL, html=response_xml([])),
        })

        self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Python Developer - AI/ML applications"),
        )

        form = parse_qs(fetcher.requests[1][1].decode())
        payload = json.loads(form["careerSiteUrlParams"][0])
        self.assertEqual(payload["search"], "applications")

    def test_paginates_with_bound_and_stops_on_exact_title(self):
        first = [{"id": str(index + 1), "title": f"Engineer {index}"} for index in range(50)]
        second = [{"id": "999", "title": "Data Analyst"}]

        class PagedFetcher(RecordingFetcher):
            def fetch(inner_self, url, data=None, headers=None):
                inner_self.requests.append((url, data, headers))
                if url == BOARD_URL:
                    return Page(url=url, html=portal_html())
                page = json.loads(parse_qs(data.decode())["careerSiteUrlParams"][0])["page"]
                jobs = first if page == 0 else second
                return Page(url=url, html=response_xml(jobs, total=51))

        fetcher = PagedFetcher()
        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="Data Analyst"))

        self.assertEqual(result.trace["pages_fetched"], 2)
        self.assertEqual(len(fetcher.requests), 3)
        self.assertEqual(result.candidates[-1].title, "Data Analyst")

    def test_rejects_mismatched_config_and_cross_tenant_redirects(self):
        mismatched = RecordingFetcher({
            BOARD_URL: Page(
                url=BOARD_URL,
                final_url=f"https://acme.ripplehire.com/candidate/?token=another-public-token&source=CAREERSITE",
                html=portal_html(),
            )
        })
        cross_tenant = RecordingFetcher({
            BOARD_URL: Page(
                url=BOARD_URL,
                final_url=f"https://other.ripplehire.com/candidate/?token={TOKEN}&source=CAREERSITE",
                html=portal_html(),
            )
        })

        invalid = self.adapter.list_jobs(mismatched, self.board, JobQuery(title="Data Analyst"))
        redirected = self.adapter.list_jobs(cross_tenant, self.board, JobQuery(title="Data Analyst"))

        self.assertEqual(invalid.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_returns_structured_failures(self):
        failed = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("timeout")),
            self.board,
            JobQuery(title="Data Analyst"),
        )
        malformed = self.adapter.list_jobs(
            RecordingFetcher({
                BOARD_URL: Page(url=BOARD_URL, html=portal_html()),
                API_URL: Page(url=API_URL, html="<not-xml"),
            }),
            self.board,
            JobQuery(title="Data Analyst"),
        )

        self.assertEqual(failed.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(failed.retryable)
        self.assertEqual(malformed.reason_code, "INVALID_STRUCTURED_DATA")

    def test_job_board_traversal_keeps_registry_backed_low_signal_link(self):
        career_url = "https://company.example/careers"
        fetcher = RecordingFetcher({
            career_url: Page(
                url=career_url,
                html=f'<a href="{BOARD_URL}">View opportunities</a>',
            ),
            BOARD_URL: Page(url=BOARD_URL, html=portal_html()),
        })

        job_list, trace = JobSourceAgent(fetcher, max_job_pages=2).find_job_board(career_url)

        self.assertEqual(job_list, BOARD_URL)
        self.assertEqual(trace["provider"], "ripplehire")


if __name__ == "__main__":
    unittest.main()
