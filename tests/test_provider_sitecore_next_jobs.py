import json
import unittest

from job_source_agent.job_board import DiscoveredJobBoard
from job_source_agent.opening_matcher import JobOpeningMatcher
from job_source_agent.providers.base import JobQuery, ProviderAdapter
from job_source_agent.providers.registry import ProviderRegistry, discover_native_adapters
from job_source_agent.providers.sitecore_next_jobs import (
    ADAPTER,
    SitecoreNextJobsAdapter,
)
from job_source_agent.web import FetchError, Page


BOARD_URL = "https://jobs.example.com/en-us/careers/job-results"
API_URL = "https://jobs.example.com/api/data/jobs/summarized"
BASE_QUERY = "&facet.range=PostedDate&f.PostedDate.facet.range.start=NOW-30DAYS/DAY"
FILTERS = "{REMOTE-FILTER}|{CATEGORY-FILTER}"


def next_data_html(
    *,
    host="jobs.example.com",
    site="example-jobs",
    language="en",
    country="US",
    brand="examplebrand",
    dictionary_brand="example",
    base_query=BASE_QUERY,
    filters=FILTERS,
    include_sitecore=True,
    component_name="JobSearch",
):
    uid = "synthetic-job-search"
    sitecore = {
        "context": {
            "site": {"name": site},
            "language": language,
            "itemPath": "/careers/job-results",
        },
        "route": {
            "placeholders": {
                "content": [
                    {
                        "uid": uid,
                        "componentName": component_name,
                        "params": {"JobSearchFiltering": filters},
                    }
                ]
            }
        },
    }
    page_props = {
        "dictionary": {"brandAustralia": dictionary_brand},
        "layoutData": {"sitecore": sitecore} if include_sitecore else {},
        "componentProps": {
            uid: {
                "siteHost": host,
                "contextLanguage": language,
                "contextCountry": country,
                "brandName": brand,
                "brandAustralia": dictionary_brand,
                "baseSearchQuery": base_query,
            }
        },
    }
    value = {"props": {"pageProps": page_props}, "page": "/[[...path]]"}
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(value)
        + "</script>"
    )


def job(
    job_id="US_EN_1_123",
    title="Platform Engineer",
    location="Austin, Texas",
    brand="EXAMPLEBRAND",
    language="en-US",
    country="USA",
    **extra,
):
    return {
        "jobTitle": title,
        "jobLocation": location,
        "jobId": job_id,
        "brandName": brand,
        "language": language,
        "countryId": country,
        **extra,
    }


def inventory(jobs, *, total, next_range):
    return {
        "jobs": jobs,
        "facet_counts": {},
        "facets": {},
        "pagination": {
            "nextRange": next_range,
            "total": total,
            "pageCount": 0 if total == 0 else 1,
        },
        "filters": [],
    }


class RecordingFetcher:
    def __init__(self, responses=(), error=None):
        self.responses = list(responses)
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise FetchError(f"unexpected URL: {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class BudgetAwareFetcher(RecordingFetcher):
    timeout = 5.0

    def __init__(
        self,
        responses=(),
        *,
        remaining=0.0,
        remaining_after_fetch=None,
        error=None,
    ):
        super().__init__(responses, error=error)
        self.remaining = remaining
        self.remaining_after_fetch = remaining_after_fetch
        self.recorded_failures = []

    def remaining_fetch_seconds(self):
        return self.remaining

    def fetch(self, url, data=None, headers=None):
        response = super().fetch(url, data=data, headers=headers)
        if self.remaining_after_fetch is not None:
            self.remaining = self.remaining_after_fetch
        return response

    def record_fetch_failure(self, error, url, data=None, headers=None):
        self.recorded_failures.append((error, url, data, headers))


def response(payload, *, url=API_URL, final_url=None):
    html = payload if isinstance(payload, str) else json.dumps(payload)
    return Page(url=url, final_url=final_url, html=html, source="synthetic-sitecore-api")


class SitecoreNextJobsAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = SitecoreNextJobsAdapter()
        self.board = self.adapter.identify_board_from_page(
            Page(url=BOARD_URL + "?campaign=test#jobs", html=next_data_html())
        )
        self.assertIsNotNone(self.board)

    def test_native_page_aware_adapter_is_auto_discovered(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["sitecore_next_jobs"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertFalse(ADAPTER.recognizes(BOARD_URL))
        self.assertIsNone(ADAPTER.identify_board(BOARD_URL))
        selected = ProviderRegistry((self.adapter,)).board_for_page(
            Page(url=BOARD_URL, html=next_data_html())
        )
        self.assertEqual(selected, (self.adapter, self.board))

    def test_identifies_board_and_binds_all_tenant_configuration(self):
        self.assertTrue(self.board.replay_safe)
        self.assertEqual(self.board.url, BOARD_URL)
        identity = json.loads(self.board.identifier)
        self.assertEqual(
            identity,
            {
                "origin": "https://jobs.example.com",
                "path": "/en-us/careers/job-results",
                "site": "example-jobs",
                "language": "en",
                "country": "US",
                "brand": "examplebrand",
                "config": {
                    "baseSearchQuery": BASE_QUERY,
                    "filtersToDisplay": FILTERS,
                    "brandFromDictionary": "example",
                },
            },
        )

    def test_identifies_second_tenant_without_company_specific_state(self):
        url = "https://careers.second.test:443/fr-ca/emplois/resultats?q=x#open"
        second = self.adapter.identify_board_from_page(
            Page(
                url=url,
                html=next_data_html(
                    host="careers.second.test",
                    site="second-site",
                    language="fr",
                    country="CA",
                    brand="second-brand",
                    dictionary_brand="second-dictionary",
                    base_query="&facet.range=Second",
                    filters="{SECOND-FILTER}",
                ),
            )
        )

        self.assertIsNotNone(second)
        self.assertEqual(second.url, "https://careers.second.test:443/fr-ca/emplois/resultats")
        self.assertNotEqual(second.identifier, self.board.identifier)
        self.assertEqual(json.loads(second.identifier)["site"], "second-site")

    def test_typed_board_handoff_skips_second_landing_page_fetch(self):
        fetcher = RecordingFetcher(
            [response(inventory([job(title="AI Engineer")], total=1, next_range=10))]
        )
        discovered = DiscoveredJobBoard(
            board=self.board,
            detection_method="page_evidence",
            evidence_url=BOARD_URL,
        )

        match, trace = JobOpeningMatcher(
            fetcher,
            ProviderRegistry((self.adapter,)),
        ).match(
            BOARD_URL,
            "AI Engineer",
            discovered_board=discovered,
        )

        self.assertIsNotNone(match)
        self.assertEqual([request[0] for request in fetcher.requests], [API_URL])
        self.assertEqual(
            trace["provider_api"]["provider_detection"]["method"],
            "typed_stage_handoff",
        )

    def test_requires_safe_url_next_json_sitecore_jobsearch_and_configuration(self):
        cases = [
            ("http://jobs.example.com/en-us/careers/job-results", next_data_html()),
            ("https://user@jobs.example.com/en-us/careers/job-results", next_data_html()),
            ("https://jobs.example.com:8443/en-us/careers/job-results", next_data_html()),
            (BOARD_URL, "<script id=\"__NEXT_DATA__\">{broken</script>"),
            (BOARD_URL, next_data_html(include_sitecore=False)),
            (BOARD_URL, next_data_html(component_name="Hero")),
            (BOARD_URL, next_data_html(base_query="")),
            (BOARD_URL, next_data_html(host="other.example.com")),
        ]
        for url, html in cases:
            with self.subTest(url=url, html=html[:80]):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(Page(url=url, html=html))
                )

    def test_posts_frozen_body_and_completes_two_pages_with_safe_candidate_urls(self):
        fetcher = RecordingFetcher(
            [
                response(inventory([job(title="Data Engineer")], total=2, next_range=10)),
                response(
                    inventory(
                        [job("US_EN_2_ABC", "ML Engineer", location=None)],
                        total=2,
                        next_range=20,
                    )
                ),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="AI Engineer / R&D?"),
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertEqual(result.reason_code, None)
        self.assertEqual(result.trace["ranges"], [0, 10])
        self.assertEqual(
            [candidate.url for candidate in result.candidates],
            [
                BOARD_URL + "?jobId=us_en_1_123",
                BOARD_URL + "?jobId=us_en_2_abc",
            ],
        )
        self.assertIsNone(result.candidates[1].location)
        first_url, first_data, first_headers = fetcher.requests[0]
        self.assertEqual(first_url, API_URL)
        self.assertEqual(
            first_headers,
            {
                "Accept": "application/json",
                "Content-Type": "text/plain;charset=UTF-8",
            },
        )
        self.assertIsInstance(first_data, bytes)
        self.assertEqual(
            json.loads(first_data),
            {
                "baseSearchQuery": BASE_QUERY,
                "filtersToDisplay": FILTERS,
                "queryString": (
                    "&sort=PostedDate desc&facet.pivot=IsRemote"
                    "&q=AI%20Engineer%20%2F%20R%26D%3F"
                ),
                "range": 0,
                "siteName": "example-jobs",
                "brand": "examplebrand",
                "countryCookie": "US",
                "langCookie": "en",
                "brandFromDictionary": "example",
            },
        )
        self.assertEqual(json.loads(fetcher.requests[1][1])["range"], 10)

    def test_exact_title_stops_early_and_is_explicitly_incomplete(self):
        fetcher = RecordingFetcher(
            [
                response(
                    inventory(
                        [job(title="AI   Engineer")],
                        total=50,
                        next_range=10,
                    )
                )
            ]
        )

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="ai engineer"))

        self.assertEqual(len(fetcher.requests), 1)
        self.assertEqual(len(result.candidates), 1)
        self.assertFalse(result.inventory_complete)
        self.assertTrue(result.trace["stopped_on_exact_title"])

    def test_soft_deadline_reserve_retains_partial_page_without_fetching_next(self):
        fetcher = BudgetAwareFetcher(
            [response(inventory([job(title="Data Engineer")], total=2, next_range=10))],
            remaining=7.0,
            remaining_after_fetch=6.0,
        )

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery())

        self.assertEqual(len(fetcher.requests), 1)
        self.assertEqual(
            [candidate.title for candidate in result.candidates],
            ["Data Engineer"],
        )
        self.assertEqual(result.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["ranges"], [0])
        self.assertEqual(result.trace["stop_reason"], "soft_deadline_reserve")
        self.assertNotIn("remaining", repr(result.trace).lower())
        self.assertEqual(len(fetcher.recorded_failures), 1)
        error, url, data, headers = fetcher.recorded_failures[0]
        self.assertEqual(error.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertEqual(url, API_URL)
        self.assertEqual(json.loads(data)["range"], 10)
        self.assertEqual(headers["Content-Type"], "text/plain;charset=UTF-8")

    def test_soft_deadline_reserve_skips_first_page_at_timeout_boundary(self):
        fetcher = BudgetAwareFetcher(
            [response(inventory([job()], total=1, next_range=10))],
            remaining=6.0,
        )

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery())

        self.assertEqual(fetcher.requests, [])
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.trace["ranges"], [])
        self.assertEqual(result.trace["page_count"], 0)
        self.assertEqual(result.trace["stop_reason"], "soft_deadline_reserve")
        self.assertEqual(len(fetcher.recorded_failures), 1)
        error, url, data, headers = fetcher.recorded_failures[0]
        self.assertEqual(error.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(error.retryable)
        self.assertEqual(url, API_URL)
        self.assertEqual(json.loads(data)["range"], 0)
        self.assertEqual(headers["Content-Type"], "text/plain;charset=UTF-8")

    def test_exact_title_wins_before_soft_deadline_reserve(self):
        fetcher = BudgetAwareFetcher(
            [response(inventory([job(title="AI Engineer")], total=2, next_range=10))],
            remaining=7.0,
            remaining_after_fetch=0.0,
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="AI Engineer"),
        )

        self.assertEqual(len(fetcher.requests), 1)
        self.assertEqual(result.reason_code, None)
        self.assertFalse(result.inventory_complete)
        self.assertTrue(result.trace["stopped_on_exact_title"])
        self.assertIsNone(result.trace["stop_reason"])

    def test_completed_inventory_wins_before_soft_deadline_reserve(self):
        fetcher = BudgetAwareFetcher(
            [response(inventory([job()], total=1, next_range=10))],
            remaining=7.0,
            remaining_after_fetch=0.0,
        )

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery())

        self.assertEqual(len(fetcher.requests), 1)
        self.assertEqual(result.reason_code, None)
        self.assertTrue(result.inventory_complete)
        self.assertIsNone(result.trace["stop_reason"])

    def test_rejects_board_identity_tampering(self):
        identity = json.loads(self.board.identifier)
        identity["origin"] = "https://other.example.com"
        tampered = type(self.board)(
            url=self.board.url,
            provider=self.board.provider,
            identifier=json.dumps(identity, separators=(",", ":"), sort_keys=True),
        )

        result = self.adapter.list_jobs(RecordingFetcher(), tampered, JobQuery())

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(result.inventory_complete)

    def test_repeated_range_and_page_cap_remain_incomplete(self):
        repeated = self.adapter.list_jobs(
            RecordingFetcher(
                [response(inventory([job()], total=2, next_range=0))]
            ),
            self.board,
            JobQuery(),
        )
        cap_fetcher = RecordingFetcher(
            [
                response(
                    inventory(
                        [job(f"US_EN_{index}_ID", title=f"Role {index}")],
                        total=11,
                        next_range=index + 1,
                    )
                )
                for index in range(10)
            ]
        )
        capped = self.adapter.list_jobs(cap_fetcher, self.board, JobQuery())

        self.assertFalse(repeated.inventory_complete)
        self.assertEqual(repeated.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertIn("invalid nextRange", repr(repeated.trace["errors"]))
        self.assertEqual(len(cap_fetcher.requests), 10)
        self.assertFalse(capped.inventory_complete)
        self.assertEqual(capped.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(capped.retryable)
        self.assertIn("pagination cap reached", repr(capped.trace["errors"]))

    def test_fetch_error_redirect_and_malformed_json_are_incomplete(self):
        cases = [
            (
                RecordingFetcher(error=FetchError("The read operation timed out")),
                "NETWORK_TIMEOUT",
                True,
            ),
            (
                RecordingFetcher(
                    [
                        response(
                            inventory([], total=0, next_range=0),
                            final_url="https://evil.example/api/data/jobs/summarized",
                        )
                    ]
                ),
                "PROVIDER_VARIANT_UNSUPPORTED",
                False,
            ),
            (
                RecordingFetcher(
                    [
                        response(
                            inventory([], total=0, next_range=0),
                            final_url="https://jobs.example.com/api/data/jobs/other",
                        )
                    ]
                ),
                "PROVIDER_VARIANT_UNSUPPORTED",
                False,
            ),
            (RecordingFetcher([response("{broken")]), "INVALID_STRUCTURED_DATA", False),
            (
                RecordingFetcher(
                    [response({"jobs": [], "pagination": {"total": 0, "nextRange": 0}})]
                ),
                "INVALID_STRUCTURED_DATA",
                False,
            ),
        ]
        for fetcher, reason, retryable in cases:
            with self.subTest(reason=reason):
                result = self.adapter.list_jobs(fetcher, self.board, JobQuery())
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.reason_code, reason)
                self.assertEqual(result.retryable, retryable)

    def test_preserves_typed_fetch_error_reason_code(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(
                error=FetchError(
                    "opaque transport failure",
                    reason_code="RATE_LIMITED",
                )
            ),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "RATE_LIMITED")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)

    def test_contradictory_total_is_incomplete(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    response(inventory([job()], total=2, next_range=10)),
                    response(
                        inventory(
                            [job("US_EN_2_456", "Security Engineer")],
                            total=3,
                            next_range=20,
                        )
                    ),
                ]
            ),
            self.board,
            JobQuery(),
        )

        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertIn("contradictory total", repr(result.trace["errors"]))

    def test_duplicate_job_ids_across_pages_are_incomplete(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    response(inventory([job()], total=2, next_range=10)),
                    response(
                        inventory(
                            [job(title="Duplicate title")],
                            total=2,
                            next_range=20,
                        )
                    ),
                ]
            ),
            self.board,
            JobQuery(),
        )

        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(len(result.candidates), 1)
        self.assertIn(
            "invalid, duplicate, or cross-tenant job record",
            repr(result.trace["errors"]),
        )

    def test_rejects_unsafe_ids_and_cross_tenant_record_identity(self):
        records = [
            job("../../admin"),
            job(brand="OTHER"),
            job(language="fr-CA"),
            job(country="GBR"),
        ]
        for record in records:
            with self.subTest(record=record):
                result = self.adapter.list_jobs(
                    RecordingFetcher([response(inventory([record], total=1, next_range=10))]),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.candidates, [])
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")

    def test_normalizes_harmless_whitespace_in_public_record_fields(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(
                [
                    response(
                        inventory(
                            [
                                job(
                                    title="  Data   Analyst  ",
                                    location=" Austin,   Texas ",
                                    brand=" EXAMPLEBRAND ",
                                    language=" en-US ",
                                    country=" USA ",
                                )
                            ],
                            total=1,
                            next_range=10,
                        )
                    )
                ]
            ),
            self.board,
            JobQuery(title="Data Analyst"),
        )

        self.assertEqual(result.reason_code, None)
        self.assertEqual(result.candidates[0].title, "Data Analyst")
        self.assertEqual(result.candidates[0].location, "Austin, Texas")

    def test_complete_empty_inventory_has_typed_scope_for_filtered_and_full(self):
        for query, scope in [
            (JobQuery(title="Missing Role"), "title_filtered"),
            (JobQuery(), "full"),
        ]:
            with self.subTest(scope=scope):
                result = self.adapter.list_jobs(
                    RecordingFetcher([response(inventory([], total=0, next_range=0))]),
                    self.board,
                    query,
                )
                self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
                self.assertEqual(result.inventory_scope, scope)
                self.assertTrue(result.inventory_complete)


if __name__ == "__main__":
    unittest.main()
