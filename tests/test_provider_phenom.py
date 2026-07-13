import json
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.phenom import ADAPTER, PhenomAdapter
from job_source_agent.providers.registry import build_default_provider_registry, discover_native_adapters
from job_source_agent.web import FetchError, Page


def phenom_html(
    *,
    ref_num="ACMEGLOBAL",
    jobs=None,
    total_hits=None,
    base_url="https://careers.example.com/global/en/",
    guarded=False,
):
    jobs = jobs or []
    total_hits = len(jobs) if total_hits is None else total_hits
    return f'''<html><body><script>
    var phApp = {"phApp || " if guarded else ""}{{"cdnUrl":"https://cdn.phenompeople.com/CareerConnectResources","pageName":"search-results","refNum":"{ref_num}","baseUrl":"{base_url}"}};
    phApp.ddo = {{"eagerLoadRefineSearch":{{"hits":{len(jobs)},"totalHits":{total_hits},"data":{{"jobs":{json.dumps(jobs)}}}}}}};
    </script></body></html>'''


class MappingFetcher:
    def __init__(self, pages=None, error=None):
        self.pages = pages or {}
        self.error = error
        self.requested_urls = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        if self.error:
            raise self.error
        page = self.pages.get(url)
        if page is None:
            raise FetchError(f"unexpected URL: {url}")
        return page


class PhenomAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = PhenomAdapter()
        self.board_url = "https://careers.example.com/global/en/search-results"

    def test_native_page_aware_adapter_is_auto_discovered(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["phenom"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertFalse(ADAPTER.recognizes(self.board_url))

    def test_identifies_only_safe_search_page_with_phenom_tenant_state(self):
        html = phenom_html(guarded=True).replace(
            "</body>",
            '<script>if (phApp) { phApp.viewsFromPage = true } else { var phApp = {"viewsFromPage":true} }</script></body>',
        )
        page = Page(url=self.board_url, html=html)
        selected = build_default_provider_registry().board_for_page(page)

        self.assertIsNotNone(selected)
        self.assertIs(selected[0], ADAPTER)
        self.assertEqual(
            selected[1],
            JobBoard(self.board_url, "phenom", "ACMEGLOBAL", replay_safe=True),
        )
        self.assertIsNone(
            self.adapter.identify_board_from_page(
                Page(url="https://careers.example.com/global/en", html=phenom_html())
            )
        )
        self.assertIsNone(
            self.adapter.identify_board_from_page(
                Page(url=self.board_url, html=phenom_html(ref_num="bad tenant"))
            )
        )

    def test_lists_keyword_jobs_and_builds_same_origin_detail_urls(self):
        search_url = self.board_url + "?keywords=AI+Engineer"
        jobs = [
            {
                "jobId": "REQ-123",
                "title": "AI Engineer",
                "cityStateCountry": "Remote",
                "jobSeqNo": "ACMEREQ123",
            },
            {"jobId": "bad/id", "title": "Unsafe", "cityStateCountry": "Remote"},
        ]
        fetcher = MappingFetcher({
            search_url: Page(url=search_url, html=phenom_html(jobs=jobs), source="phenom-contract")
        })

        result = self.adapter.list_jobs(
            fetcher,
            JobBoard(self.board_url, "phenom", "ACMEGLOBAL"),
            JobQuery(title="AI Engineer"),
        )

        self.assertEqual(fetcher.requested_urls, [search_url])
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            result.candidates[0].url,
            "https://careers.example.com/global/en/job/REQ-123/ai-engineer",
        )
        self.assertEqual(result.candidates[0].location, "Remote")
        self.assertEqual(result.trace["variant"], "ssr_eager_refine_search")
        self.assertEqual(result.trace["inventory_scope"], "title_filtered")
        self.assertEqual(result.trace["rejected_job_ids"], ["bad/id"])
        self.assertTrue(result.inventory_complete)
        self.assertTrue(result.trace["inventory_complete"])

    def test_paginates_with_bound_and_stops_on_exact_title(self):
        first_url = self.board_url + "?keywords=AI+Engineer"
        second_url = self.board_url + "?keywords=AI+Engineer&from=10"
        first_jobs = [
            {"jobId": f"REQ-{index}", "title": f"Platform Engineer {index}"}
            for index in range(10)
        ]
        second_jobs = [{"jobId": "REQ-EXACT", "title": "AI Engineer"}]
        fetcher = MappingFetcher({
            first_url: Page(url=first_url, html=phenom_html(jobs=first_jobs, total_hits=30)),
            second_url: Page(url=second_url, html=phenom_html(jobs=second_jobs, total_hits=30)),
        })

        result = self.adapter.list_jobs(
            fetcher,
            JobBoard(self.board_url, "phenom", "ACMEGLOBAL"),
            JobQuery(title="AI Engineer"),
        )

        self.assertEqual(fetcher.requested_urls, [first_url, second_url])
        self.assertEqual(result.candidates[-1].title, "AI Engineer")
        self.assertFalse(result.inventory_complete)
        self.assertFalse(result.trace["inventory_complete"])

    def test_rejects_tenant_mismatch_cross_origin_and_reports_fetch_failure(self):
        board = JobBoard(self.board_url, "phenom", "ACMEGLOBAL")
        search_url = self.board_url + "?keywords=AI+Engineer"
        mismatch = self.adapter.list_jobs(
            MappingFetcher(
                {search_url: Page(url=search_url, html=phenom_html(ref_num="OTHER"))}
            ),
            board,
            JobQuery(title="AI Engineer"),
        )
        redirected = self.adapter.list_jobs(
            MappingFetcher(
                {
                    search_url: Page(
                        url=search_url,
                        final_url="https://evil.example/search-results",
                        html=phenom_html(),
                    )
                }
            ),
            board,
            JobQuery(title="AI Engineer"),
        )
        failed = self.adapter.list_jobs(
            MappingFetcher(error=FetchError("blocked")),
            board,
            JobQuery(title="AI Engineer"),
        )

        self.assertEqual(mismatch.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(failed.reason_code, "PROVIDER_FETCH_FAILED")
        self.assertTrue(failed.retryable)


if __name__ == "__main__":
    unittest.main()
