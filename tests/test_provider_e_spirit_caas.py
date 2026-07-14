from dataclasses import asdict
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import unittest

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.e_spirit_caas import (
    ADAPTER,
    ESpiritCaaSAdapter,
    _MAX_COUNTRY_IDS,
    _MAX_PAGES,
    _MAX_ROWS,
    _PAGE_SIZE,
)
from job_source_agent.providers.registry import ProviderRegistry, discover_native_adapters
from job_source_agent.web import FetchError, Page


PAGE_URL = "https://careers.fiction.example/en/openings?source=test#jobs"
BOARD_URL = "https://careers.fiction.example/en/openings"
API_ORIGIN = "https://fiction-caas-api.e-spirit.cloud"
TENANT = "fiction-prod"
PROJECT = "fiction-careers"
DETAIL_PREFIX = "https://careers.fiction.example/en/job/"
CREDENTIAL = "fictional-runtime-key-7c38a18c"
ENDPOINT = f"{API_ORIGIN}/{TENANT}/{PROJECT}.jobs.content/_aggrs/get_jobs"


def config_html(
    *,
    base_url=API_ORIGIN,
    tenant=TENANT,
    project=PROJECT,
    collection="jobs",
    api_key=CREDENTIAL,
    detail_prefix=DETAIL_PREFIX,
    country_ids=None,
):
    jobs = {
        "baseUrl": base_url,
        "tenant": tenant,
        "project": project,
        "collection": collection,
        "apiKey": api_key,
    }
    country_ids = ["FI"] if country_ids is None else country_ids
    return (
        "<html><head><script type='text/javascript'>"
        "window.EXTERNAL_CONFIG="
        + json.dumps(
            {"jobsApi": jobs, "jobAdLinkPrefix": detail_prefix},
            separators=(",", ":"),
        )
        + ";</script>"
        + '<script type="text/json" data-prop-name="countryFilterOptions">'
        + json.dumps(
            [{"label": f"Country {index}", "ids": [country_id], "index": 1120}
             for index, country_id in enumerate(country_ids)],
            separators=(",", ":"),
        )
        + "</script></head></html>"
    )


def job(number, *, route=None, title=None, location=None):
    return {
        "_id": f"JOB-{number}_en",
        "name": title or f"Fictional Data Engineer {number}",
        "jobUrl": route or f"JOB-{number}-fictional-data-engineer",
        "refNumber": f"JOB-{number}",
        "releasedDate": "2026-07-01",
        "location": location
        if location is not None
        else {
            "workLocation": f"Fiction City {number}",
            "country": "Fictionland",
        },
    }


def hal(rows, *, count=None, returned=1, results=None):
    result = {
        "data": rows,
        "meta": [{"count": len(rows) if count is None else count}],
        "filter_function": [],
    }
    return {
        "_returned": returned,
        "_embedded": {"rh:result": [result] if results is None else results},
    }


class RecordingFetcher:
    def __init__(self, payloads=(), *, final_url=None, error=None):
        self.payloads = list(payloads)
        self.final_url = final_url
        self.error = error
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.error is not None:
            raise self.error
        if not self.payloads:
            raise AssertionError(f"unexpected request number {len(self.requests)}")
        payload = self.payloads.pop(0)
        body = payload if isinstance(payload, str) else json.dumps(payload)
        return Page(
            url=url,
            final_url=self.final_url,
            html=body,
            source="fictional-provider-fixture",
        )


class ESpiritCaaSAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ESpiritCaaSAdapter()
        self.board = self.identify()

    def identify(self, *, html=None, url=PAGE_URL):
        board = self.adapter.identify_board_from_page(
            Page(url=url, html=config_html() if html is None else html)
        )
        self.assertIsNotNone(board)
        return board

    def assert_credential_absent(self, value):
        serialized = json.dumps(value, default=str, sort_keys=True)
        self.assertNotIn(CREDENTIAL, serialized)
        self.assertNotIn("Authorization", serialized)

    def test_native_page_aware_adapter_is_discovered_without_url_recognition(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["e_spirit_caas"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertTrue(ADAPTER.supports_listing)
        self.assertFalse(ADAPTER.recognizes(PAGE_URL))
        self.assertIsNone(ADAPTER.identify_board(PAGE_URL))

    def test_identifies_one_jobs_config_and_keeps_board_nonsensitive(self):
        selected = ProviderRegistry((self.adapter,)).board_for_page(
            Page(
                url="https://old.fiction.example/jobs",
                final_url=PAGE_URL,
                html=config_html(country_ids=["FI", "ISLAND-2"]),
            )
        )

        self.assertIsNotNone(selected)
        self.assertIs(selected[0], self.adapter)
        board = selected[1]
        self.assertEqual(board.url, BOARD_URL)
        self.assertEqual(board.provider, "e_spirit_caas")
        self.assertFalse(board.replay_safe)
        self.assertEqual(
            json.loads(board.identifier),
            {
                "api_origin": API_ORIGIN,
                "career_origin": "https://careers.fiction.example",
                "collection": "jobs",
                "country_ids": ["FI", "ISLAND-2"],
                "detail_prefix": DETAIL_PREFIX,
                "project": PROJECT,
                "tenant": TENANT,
            },
        )
        self.assert_credential_absent(asdict(board))

    def test_identifies_second_fictional_tenant_generically(self):
        board = self.adapter.identify_board_from_page(
            Page(
                url="https://jobs.second-fiction.example/de/search",
                html=config_html(
                    base_url="https://second-caas-api.e-spirit.cloud",
                    tenant="second-prod",
                    project="second-jobs",
                    api_key="second-fictional-runtime-key-8899",
                    detail_prefix="https://jobs.second-fiction.example/de/job/",
                    country_ids=["DE"],
                ),
            )
        )

        self.assertIsNotNone(board)
        identity = json.loads(board.identifier)
        self.assertEqual(identity["tenant"], "second-prod")
        self.assertEqual(identity["project"], "second-jobs")
        self.assertEqual(identity["country_ids"], ["DE"])

    def test_identifies_sanitized_bosch_shape_only_with_runtime_credential(self):
        snapshot = Path(
            "/private/tmp/holdout85-snapshots/sites/jobs.bosch.com/en/index.html"
        )
        if not snapshot.exists():
            self.skipTest("holdout snapshot is not available")
        sanitized = snapshot.read_text(encoding="utf-8")

        replay_board = self.adapter.identify_board_from_page(
            Page(url="https://jobs.bosch.com/en/", html=sanitized)
        )
        self.assertIsNotNone(replay_board)
        self.assertNotIn("[REDACTED]", replay_board.identifier)
        board = self.adapter.identify_board_from_page(
            Page(
                url="https://jobs.bosch.com/en/",
                html=sanitized.replace(
                    'apiKey:"[REDACTED]"',
                    f'apiKey:"{CREDENTIAL}"',
                    1,
                ),
            )
        )

        self.assertIsNotNone(board)
        identity = json.loads(board.identifier)
        self.assertEqual(identity["collection"], "jobs")
        self.assertEqual(len(identity["country_ids"]), 40)
        self.assertIn("us", identity["country_ids"])

    def test_ignores_unrelated_json_caas_config_but_rejects_jobs_ambiguity(self):
        unrelated = """
          <script type="application/json" id="external-config">
            {"caas":{"baseUrl":"https://content.example","collection":"pages"}}
          </script>
        """
        board = self.adapter.identify_board_from_page(
            Page(url=PAGE_URL, html=unrelated + config_html())
        )
        ambiguous = self.adapter.identify_board_from_page(
            Page(url=PAGE_URL, html=config_html() + config_html())
        )

        self.assertIsNotNone(board)
        self.assertIsNone(ambiguous)

    def test_rejects_api_origin_ssrf_lookalikes_and_unsafe_page_urls(self):
        unsafe_api_origins = (
            "http://fiction-caas-api.e-spirit.cloud",
            "https://fiction-caas-api.e-spirit.cloud.evil.example",
            "https://e-spirit.cloud",
            "https://127.0.0.1",
            "https://user@fiction-caas-api.e-spirit.cloud",
            "https://fiction-caas-api.e-spirit.cloud:8443",
            "https://fiction-caas-api.e-spirit.cloud/path",
            "https://fiction-caas-api.e-spirit.cloud?key=value",
            "https://fiction-caas-api.e-spirit.cloud#fragment",
        )
        for origin in unsafe_api_origins:
            with self.subTest(origin=origin):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(
                        Page(url=PAGE_URL, html=config_html(base_url=origin))
                    )
                )
        for url in (
            "http://careers.fiction.example/jobs",
            "https://user@careers.fiction.example/jobs",
            "https://careers.fiction.example:8443/jobs",
        ):
            with self.subTest(url=url):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(
                        Page(url=url, html=config_html())
                    )
                )

    def test_rejects_redacted_ambiguous_and_unsafe_config_values(self):
        cases = (
            {"api_key": "redacted-runtime-key"},
            {"api_key": "${API_KEY}"},
            {"api_key": "************"},
            {"tenant": "../tenant"},
            {"project": "project/path"},
            {"collection": "openings"},
            {"detail_prefix": "https://evil.example/en/job/"},
            {"detail_prefix": "https://careers.fiction.example/en/opening/"},
            {"detail_prefix": "https://careers.fiction.example/en/job/?next=evil"},
            {"country_ids": ["FI", "fi"]},
            {"country_ids": ["../FI"]},
            {"country_ids": [f"C{i}" for i in range(_MAX_COUNTRY_IDS + 1)]},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(
                        Page(url=PAGE_URL, html=config_html(**changes))
                    )
                )

    def test_constructs_title_filtered_endpoint_with_bounded_country_ids(self):
        board = self.identify(html=config_html(country_ids=["FI", "ISLAND-2"]))
        fetcher = RecordingFetcher([hal([job(1)], count=1)])

        result = self.adapter.list_jobs(
            fetcher,
            board,
            JobQuery(title="Senior Data Scientist", location="Fiction City"),
        )

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertEqual(len(fetcher.requests), 1)
        request_url, data, headers = fetcher.requests[0]
        parsed = urlparse(request_url)
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", ENDPOINT)
        self.assertEqual(
            parse_qs(parsed.query),
            {
                "avars": [
                    json.dumps(
                        {
                            "country": ["FI", "ISLAND-2"],
                            "search_term": "Senior Data Scientist",
                            "sort": {"releasedDate": -1},
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                ],
                "page": ["1"],
                "pagesize": [str(_PAGE_SIZE)],
            },
        )
        self.assertIsNone(data)
        self.assertEqual(
            headers,
            {
                "Accept": "application/hal+json, application/json",
                "Authorization": f"Bearer {CREDENTIAL}",
            },
        )
        self.assertNotIn(CREDENTIAL, request_url)
        candidate = result.candidates[0]
        self.assertEqual(candidate.url, DETAIL_PREFIX + "JOB-1-fictional-data-engineer")
        self.assertEqual(candidate.location, "Fiction City 1, Fictionland")
        self.assertEqual(
            candidate.raw,
            {
                "job_id": "JOB-1_en",
                "reference": "JOB-1",
                "released": "2026-07-01",
            },
        )
        self.assert_credential_absent(
            {"board": asdict(board), "trace": result.trace, "raw": candidate.raw}
        )

    def test_credential_handoff_is_consumed_once_at_list_start(self):
        no_title = self.adapter.list_jobs(RecordingFetcher(), self.board, JobQuery())
        after_invalid = self.adapter.list_jobs(
            RecordingFetcher([hal([], count=0)]),
            self.board,
            JobQuery(title="Data Scientist"),
        )

        self.assertEqual(no_title.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(no_title.trace["stop_reason"], "bounded_title_required")
        self.assertEqual(after_invalid.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(
            after_invalid.trace["stop_reason"], "runtime_credential_unavailable"
        )

        board = self.identify()
        first = self.adapter.list_jobs(
            RecordingFetcher([hal([], count=0)]), board, JobQuery(title="Missing Role")
        )
        second = self.adapter.list_jobs(
            RecordingFetcher([hal([], count=0)]), board, JobQuery(title="Missing Role")
        )
        self.assertEqual(first.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertTrue(first.inventory_complete)
        self.assertEqual(second.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_paginates_to_consistent_count_and_deduplicates_identity(self):
        rows = [job(number) for number in range(1, _PAGE_SIZE + 2)]
        fetcher = RecordingFetcher(
            [
                hal(rows[:_PAGE_SIZE], count=len(rows)),
                hal(rows[_PAGE_SIZE:], count=len(rows)),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher, self.board, JobQuery(title="Data Engineer")
        )

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(result.candidates), _PAGE_SIZE + 1)
        self.assertEqual(result.trace["page_count"], 2)
        self.assertEqual(
            [parse_qs(urlparse(request[0]).query)["page"] for request in fetcher.requests],
            [["1"], ["2"]],
        )

    def test_missing_or_contradictory_pagination_is_incomplete_not_empty(self):
        cases = (
            [hal([], count=1)],
            [hal([job(1)], count=2)],
            [hal([job(1)], count=_PAGE_SIZE + 1), hal([job(2)], count=_PAGE_SIZE)],
        )
        for payloads in cases:
            with self.subTest(payloads=payloads):
                board = self.identify()
                result = self.adapter.list_jobs(
                    RecordingFetcher(payloads), board, JobQuery(title="Data Scientist")
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)

    def test_rejects_hal_schema_matrix(self):
        invalid_payloads = (
            {},
            {"_returned": 1, "_embedded": {}},
            {"_returned": 2, "_embedded": {"rh:result": []}},
            hal([], results=[]),
            hal([], results=[{}, {}]),
            hal([], results=[{"data": [], "meta": []}]),
            hal([], results=[{"data": {}, "meta": [{"count": 0}]}]),
            hal([], results=[{"data": [], "meta": [{"count": -1}]}]),
            hal([], results=[{"data": [], "meta": [{"count": True}]}]),
            hal([], results=[{"data": [], "meta": [{"count": 0, "extra": 1}]}]),
            "not json",
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                board = self.identify()
                result = self.adapter.list_jobs(
                    RecordingFetcher([payload]), board, JobQuery(title="Data Scientist")
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])

    def test_rejects_duplicate_records_and_detail_route_escapes(self):
        duplicate = job(1)
        cases = (
            [duplicate, dict(duplicate)],
            [job(1), {**job(2), "jobUrl": job(1)["jobUrl"]}],
            [job(1, route="../admin")],
            [job(1, route="nested/route")],
            [job(1, route="https://evil.example/job")],
            [{**job(1), "_id": "bad\nidentifier"}],
        )
        for rows in cases:
            with self.subTest(rows=rows):
                board = self.identify()
                result = self.adapter.list_jobs(
                    RecordingFetcher([hal(rows, count=len(rows))]),
                    board,
                    JobQuery(title="Data Scientist"),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)

    def test_rejects_cross_origin_or_semantically_changed_response_url(self):
        request_url = (
            ENDPOINT
            + "?avars=%7B%22countryIds%22%3A%5B%5D%2C%22searchTerm%22%3A%22Data+Scientist%22%7D"
            + f"&page=1&pagesize={_PAGE_SIZE}"
        )
        changed = (
            "https://evil.example/jobs",
            request_url.replace("https://", "http://", 1),
            request_url.replace("page=1", "page=2"),
            request_url + "#fragment",
        )
        for final_url in changed:
            with self.subTest(final_url=final_url):
                board = self.identify()
                result = self.adapter.list_jobs(
                    RecordingFetcher([hal([], count=0)], final_url=final_url),
                    board,
                    JobQuery(title="Data Scientist"),
                )
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
                self.assertFalse(result.inventory_complete)

    def test_enforces_response_row_and_page_caps(self):
        over_rows = self.adapter.list_jobs(
            RecordingFetcher([hal([], count=_MAX_ROWS + 1)]),
            self.board,
            JobQuery(title="Popular Role"),
        )
        self.assertEqual(over_rows.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(over_rows.retryable)
        self.assertFalse(over_rows.inventory_complete)

        board = self.identify()
        total = _MAX_PAGES * _PAGE_SIZE + 1
        pages = [
            hal(
                [job(page * _PAGE_SIZE + offset) for offset in range(_PAGE_SIZE)],
                count=total,
            )
            for page in range(_MAX_PAGES)
        ]
        capped = self.adapter.list_jobs(
            RecordingFetcher(pages), board, JobQuery(title="Popular Role")
        )
        self.assertEqual(capped.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(capped.retryable)
        self.assertFalse(capped.inventory_complete)

    def test_fetch_failures_are_typed_without_leaking_credential(self):
        cases = (
            (FetchError(f"timeout while using bearer {CREDENTIAL}"), "NETWORK_TIMEOUT", True),
            (FetchError(f"HTTP Error 403 for {CREDENTIAL}"), "HTTP_FORBIDDEN", False),
        )
        for error, reason, retryable in cases:
            with self.subTest(reason=reason):
                board = self.identify()
                result = self.adapter.list_jobs(
                    RecordingFetcher(error=error),
                    board,
                    JobQuery(title="Data Scientist"),
                )
                self.assertEqual(result.reason_code, reason)
                self.assertEqual(result.retryable, retryable)
                self.assertFalse(result.inventory_complete)
                self.assert_credential_absent(
                    {"board": asdict(result.board), "trace": result.trace}
                )

    def test_identifier_tampering_is_unsupported_and_consumes_handoff(self):
        identity = json.loads(self.board.identifier)
        identity["api_origin"] = "https://evil-caas-api.e-spirit.cloud"
        tampered = JobBoard(
            url=self.board.url,
            provider=self.board.provider,
            identifier=json.dumps(identity, separators=(",", ":"), sort_keys=True),
            replay_safe=False,
        )
        invalid = self.adapter.list_jobs(
            RecordingFetcher(), tampered, JobQuery(title="Data Scientist")
        )
        replayable = JobBoard(
            url=self.board.url,
            provider=self.board.provider,
            identifier=self.board.identifier,
            replay_safe=True,
        )
        unsafe_replay = self.adapter.list_jobs(
            RecordingFetcher(), replayable, JobQuery(title="Data Scientist")
        )

        self.assertEqual(invalid.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(unsafe_replay.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(invalid.candidates, [])
        self.assertEqual(unsafe_replay.candidates, [])


if __name__ == "__main__":
    unittest.main()
