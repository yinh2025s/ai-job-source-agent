import html
import json
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.eightfold import ADAPTER, EightfoldAdapter
from job_source_agent.providers.registry import ProviderRegistry, discover_native_adapters
from job_source_agent.web import FetchError, Page


BOARD_URL = "https://careers.example.com/careers"


def position(job_id, title, url_host="careers.example.com"):
    return {
        "id": int(job_id),
        "name": title,
        "posting_name": title,
        "location": "New York, NY, United States",
        "locations": ["New York, NY, United States"],
        "ats_job_id": f"JR{job_id}",
        "department": "Engineering",
        "canonicalPositionUrl": f"https://{url_host}/careers/job/{job_id}",
    }


def inventory(positions, count=None, domain="example.com", fingerprint=True):
    return {
        "domain": domain,
        "positions": positions,
        "count": len(positions) if count is None else count,
        "isPcsEnabled": fingerprint,
        "pcsOctupleMigration0Enabled": fingerprint,
    }


def shell_html(positions, count=None, domain="example.com", fingerprint=True):
    body = json.dumps(inventory(positions, count, domain, fingerprint))
    return f'<code id="smartApplyData" style="display:none">{html.escape(body)}</code>'


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


class EightfoldAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = EightfoldAdapter()
        self.board = JobBoard(BOARD_URL, "eightfold", "example.com", replay_safe=True)

    def test_native_adapter_is_discovered_and_recognizes_hosted_urls(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}
        self.assertIs(native["eightfold"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        hosted = "https://acme.eightfold.ai/careers/job/123"
        self.assertTrue(self.adapter.recognizes(hosted))
        self.assertEqual(
            self.adapter.identify_board(hosted),
            JobBoard("https://acme.eightfold.ai/careers", "eightfold", "acme"),
        )
        self.assertFalse(self.adapter.recognizes("http://acme.eightfold.ai/careers"))
        self.assertFalse(self.adapter.recognizes("https://user@acme.eightfold.ai/careers"))

    def test_identifies_customer_domain_from_strong_page_state(self):
        registry = ProviderRegistry((self.adapter,))
        selected = registry.board_for_page(Page(url=BOARD_URL, html=shell_html([])))
        self.assertEqual(selected[1], self.board)
        self.assertIsNone(registry.board_for_page(Page(url=BOARD_URL, html=shell_html([], fingerprint=False))))
        self.assertIsNone(registry.board_for_page(Page(url="https://careers.example.com/about", html=shell_html([]))))

    def test_lists_title_filtered_ssr_inventory(self):
        search_url = BOARD_URL + "?query=AI+Engineer&location=New+York"
        fetcher = RecordingFetcher({
            search_url: Page(url=search_url, html=shell_html([position("101", "AI Engineer")]), source="eightfold-contract")
        })
        result = self.adapter.list_jobs(fetcher, self.board, JobQuery("AI Engineer", "New York"))
        self.assertIsNone(result.reason_code)
        self.assertEqual(result.candidates[0].title, "AI Engineer")
        self.assertEqual(result.candidates[0].url, "https://careers.example.com/careers/job/101")
        self.assertEqual(result.trace["inventory_scope"], "title_filtered")
        self.assertEqual(result.trace["pages_fetched"], 1)
        self.assertTrue(result.inventory_complete)
        self.assertTrue(result.trace["inventory_complete"])

    def test_hosted_board_resolves_customer_domain_from_verified_state(self):
        board_url = "https://acme.eightfold.ai/careers"
        board = self.adapter.identify_board(board_url)
        search_url = board_url + "?query=AI+Engineer"
        fetcher = RecordingFetcher({
            search_url: Page(
                url=search_url,
                html=shell_html(
                    [position("101", "AI Engineer", "acme.eightfold.ai")],
                    domain="acme.com",
                ),
            )
        })

        result = self.adapter.list_jobs(fetcher, board, JobQuery("AI Engineer"))

        self.assertIsNone(result.reason_code)
        self.assertEqual(result.candidates[0].url, "https://acme.eightfold.ai/careers/job/101")

    def test_paginates_public_api_and_stops_on_exact_title(self):
        search_url = BOARD_URL + "?query=AI+Engineer"
        api_url = (
            "https://careers.example.com/api/apply/v2/jobs"
            "?domain=example.com&start=10&num=10&query=AI+Engineer"
        )
        first = [position(str(index + 1), f"Engineer {index}") for index in range(10)]
        fetcher = RecordingFetcher({
            search_url: Page(url=search_url, html=shell_html(first, count=25)),
            api_url: Page(url=api_url, html=json.dumps(inventory([position("999", "AI Engineer")], count=25))),
        })
        result = self.adapter.list_jobs(fetcher, self.board, JobQuery("AI Engineer"))
        self.assertEqual(result.trace["pages_fetched"], 2)
        self.assertEqual(result.candidates[-1].title, "AI Engineer")
        self.assertEqual(result.trace["total_found"], 25)

    def test_empty_filtered_inventory_is_verified_empty(self):
        search_url = BOARD_URL + "?query=Missing+Role"
        result = self.adapter.list_jobs(
            RecordingFetcher({search_url: Page(url=search_url, html=shell_html([], count=0))}),
            self.board,
            JobQuery("Missing Role"),
        )
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.trace["total_found"], 0)

    def test_rejects_tenant_mismatch_and_unsafe_job_urls(self):
        search_url = BOARD_URL + "?query=AI+Engineer"
        mismatch = self.adapter.list_jobs(
            RecordingFetcher({search_url: Page(url=search_url, html=shell_html([], domain="other.com"))}),
            self.board,
            JobQuery("AI Engineer"),
        )
        unsafe = self.adapter.list_jobs(
            RecordingFetcher({search_url: Page(url=search_url, html=shell_html([position("1", "AI Engineer", "evil.example")]))}),
            self.board,
            JobQuery("AI Engineer"),
        )
        self.assertEqual(mismatch.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(unsafe.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(len(unsafe.trace["rejected_job_urls"]), 1)

    def test_returns_structured_fetch_failure(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("timeout")), self.board, JobQuery("AI Engineer")
        )
        self.assertEqual(result.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(result.retryable)


if __name__ == "__main__":
    unittest.main()
