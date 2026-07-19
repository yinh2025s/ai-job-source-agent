import json
import unittest
from urllib.parse import parse_qs

from job_source_agent.providers.base import JobQuery, ProviderAdapter
from job_source_agent.providers.wp_job_manager import ADAPTER, WPJobManagerAdapter
from job_source_agent.web import FetchError, Page


BOARD_URL = "https://careers.example.com/careers/"
ENDPOINT_URL = "https://careers.example.com/jm-ajax/get_listings/"


def listing_page(*, per_page="10", config=None, script_host="careers.example.com"):
    config = config or {"ajax_url": "/jm-ajax/%%endpoint%%/", "lang": None}
    return Page(
        BOARD_URL,
        f"""
        <div class="job_listings" data-per_page="{per_page}"
             data-orderby="featured" data-order="DESC">
          <form class="job_filters">
            <input name="search_keywords"><input name="search_location">
          </form><ul class="job_listings"></ul>
        </div>
        <script>var job_manager_ajax_filters = {json.dumps(config)};</script>
        <script src="https://{script_host}/wp-content/plugins/wp-job-manager/assets/dist/js/ajax-filters.js?ver=1"></script>
        """,
        final_url=BOARD_URL,
    )


def payload(records="", *, found=True, pages=1):
    return json.dumps({"found_jobs": found, "max_num_pages": pages, "html": records})


def record(title, path, location="Omaha, NE"):
    return f"""
      <li class="post-42 job_listing type-job_listing">
        <a href="{path}"><div class="position"><h3>{title}</h3></div>
        <div class="location">{location}</div></a>
      </li>
    """


class RecordingFetcher:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return Page(url, value, final_url=url)


class WPJobManagerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = WPJobManagerAdapter()
        self.board = self.adapter.identify_board_from_page(listing_page())

    def test_is_page_aware_typed_adapter(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertIsNotNone(self.board)
        self.assertEqual(self.board.url, BOARD_URL)
        self.assertEqual(self.board.provider, "wp_job_manager")
        self.assertFalse(self.board.replay_safe)
        self.assertFalse(self.adapter.recognizes(BOARD_URL))
        self.assertIsNone(self.adapter.identify_board(BOARD_URL))

    def test_rejects_weak_unsafe_or_credentialed_page_evidence(self):
        rejected = (
            listing_page(per_page="0"),
            listing_page(per_page="101"),
            listing_page(script_host="evil.example"),
            listing_page(config={"ajax_url": "/wp-admin/admin-ajax.php"}),
            listing_page(config={"ajax_url": "/jm-ajax/%%endpoint%%/", "nonce": "x"}),
            Page("http://careers.example.com/careers/", listing_page().html),
            Page(BOARD_URL, listing_page().html.replace('name="search_keywords"', 'name="q"')),
        )
        for page in rejected:
            with self.subTest(url=page.url, size=len(page.html)):
                self.assertIsNone(self.adapter.identify_board_from_page(page))

    def test_lists_filtered_jobs_and_preserves_location(self):
        response = payload(
            record("Cybersecurity Analyst", "/job/cybersecurity-analyst/")
        )
        fetcher = RecordingFetcher([response])

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery("Cybersecurity Analyst", "Omaha, NE"),
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].location, "Omaha, NE")
        self.assertEqual(
            result.candidates[0].url,
            "https://careers.example.com/job/cybersecurity-analyst/",
        )
        fields = parse_qs(fetcher.requests[0][1].decode("utf-8"), keep_blank_values=True)
        self.assertEqual(fields["search_keywords"], ["Cybersecurity Analyst"])
        self.assertEqual(fields["search_location"], ["Omaha, NE"])
        self.assertEqual(fields["per_page"], ["10"])

    def test_accepts_schema_valid_complete_empty_inventory(self):
        fetcher = RecordingFetcher(
            [payload('<li class="no_job_listings_found">No jobs</li>', found=False, pages=0)]
        )

        result = self.adapter.list_jobs(
            fetcher, self.board, JobQuery("Cybersecurity Analyst", "Omaha, NE")
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.trace["declared_pages"], 0)

    def test_paginates_to_declared_end(self):
        fetcher = RecordingFetcher(
            [
                payload(record("Engineer I", "/job/engineer-i/"), pages=2),
                payload(record("Engineer II", "/job/engineer-ii/"), pages=2),
            ]
        )

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery("Engineer"))

        self.assertTrue(result.inventory_complete)
        self.assertEqual([item.title for item in result.candidates], ["Engineer I", "Engineer II"])
        self.assertEqual(len(fetcher.requests), 2)

    def test_rejects_redirect_cross_origin_duplicate_and_bad_metadata(self):
        cases = (
            ('{"found_jobs":true}', "INVALID_STRUCTURED_DATA"),
            (payload(record("Engineer", "https://evil.example/job/engineer/")), None),
            (payload(record("Engineer", "/about/engineer/")), None),
            (payload("", found=False, pages=1), "INVALID_STRUCTURED_DATA"),
        )
        for response, reason in cases:
            with self.subTest(response=response[:50]):
                result = self.adapter.list_jobs(
                    RecordingFetcher([response]), self.board, JobQuery("Engineer")
                )
                if reason is None:
                    self.assertTrue(result.inventory_complete)
                    self.assertEqual(result.candidates, [])
                else:
                    self.assertFalse(result.inventory_complete)
                    self.assertEqual(result.reason_code, reason)

        duplicate = record("Engineer", "/job/engineer/")
        result = self.adapter.list_jobs(
            RecordingFetcher([payload(duplicate + duplicate)]),
            self.board,
            JobQuery("Engineer"),
        )
        self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")

    def test_projects_fetch_failure_without_claiming_no_match(self):
        result = self.adapter.list_jobs(
            RecordingFetcher([FetchError("timed out")]),
            self.board,
            JobQuery("Engineer"),
        )

        self.assertFalse(result.inventory_complete)
        self.assertTrue(result.retryable)


if __name__ == "__main__":
    unittest.main()
