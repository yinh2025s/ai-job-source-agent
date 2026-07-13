import json
from itertools import combinations
import unittest
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_source_agent.providers.base import JobBoard, JobQuery, ProviderAdapter
from job_source_agent.providers.ceipal import ADAPTER, CeipalAdapter
from job_source_agent.providers.registry import ProviderRegistry, discover_native_adapters
from job_source_agent.web import FetchError, Page


BOARD_URL = "https://careers.example.com/find-jobs"
API_KEY = "tenant-api-key"
PORTAL_ID = "tenant-career-portal"
API_URL = (
    "https://careerapi.ceipal.com/careerPortalWidget/"
    "?themeid=&bgcolor=&job_id=&apikey=tenant-api-key&cp_id=tenant-career-portal"
)
IFRAME_URL = (
    "https://jobsapi.ceipal.com/APISource/v1/index.html"
    "?bgcolor=1ba1ff&api_key=tenant-api-key"
    "&cp_id=tenant-career-portal&job_id="
)
IFRAME_HTML = (
    '<html><script src="https://jobsapi.ceipal.com/APISource/v1/js/app.min.js">'
    "</script></html>"
)
MULTIPART_BOUNDARY = "----AIJobSourceAgentCEIPALBoundary7MA4YWxkTrZu0gW"


def widget_html(
    *,
    src="https://jobsapi.ceipal.com/APISource/widget.js",
    api_key=API_KEY,
    portal_id=PORTAL_ID,
):
    return (
        f'<script src="{src}" data-ceipal-api-key="{api_key}" '
        f'data-ceipal-career-portal-id="{portal_id}"></script>'
    )


def wrapper_page(iframe_url=IFRAME_URL, *, final_url=None):
    return Page(
        url=API_URL,
        final_url=final_url,
        html=json.dumps({"status": 200, "html": f'<iframe src="{iframe_url}"></iframe>'}),
        source="wrapper-fixture",
    )


def iframe_page(*, url=IFRAME_URL, final_url=None, html=IFRAME_HTML):
    return Page(url=url, final_url=final_url, html=html, source="iframe-fixture")


def inventory_url(page):
    return (
        f"https://careerapi.ceipal.com/{API_KEY}/CareerPortalJobPostings/"
        f"?page={page}"
    )


def job(job_id="job-1", title="AI Engineer", **fields):
    return {"id": job_id, "public_job_title": title, **fields}


def inventory_payload(
    results,
    *,
    count=None,
    limit=25,
    page=1,
    pages=None,
    next_url=None,
    previous=None,
):
    total = len(results) if count is None else count
    total_pages = ((total + limit - 1) // limit) if pages is None and total else (pages or 0)
    return {
        "results": results,
        "count": total,
        "limit": limit,
        "page_number": page,
        "num_pages": total_pages,
        "page_count": len(results),
        "next": next_url,
        "previous": previous,
        "host": "public",
    }


def inventory_page(payload, *, page=1, final_url=None, html=None):
    url = inventory_url(page)
    return Page(
        url=url,
        final_url=final_url,
        html=json.dumps(payload) if html is None else html,
        source=f"inventory-fixture-{page}",
    )


class RecordingFetcher:
    def __init__(self, page=None, error=None, responses=None):
        self.page = page
        self.error = error
        self.responses = list(responses) if responses is not None else None
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if self.responses is not None:
            if not self.responses:
                raise FetchError(f"unexpected URL: {url}")
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        if self.error:
            raise self.error
        if self.page is None:
            raise FetchError(f"unexpected URL: {url}")
        return self.page


def successful_fetcher(*inventory_pages, iframe=IFRAME_HTML, iframe_url=IFRAME_URL):
    return RecordingFetcher(
        responses=[
            wrapper_page(iframe_url),
            iframe_page(url=iframe_url, html=iframe),
            *inventory_pages,
        ]
    )


class CeipalAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = CeipalAdapter()
        self.board = self.adapter.identify_board_from_page(
            Page(url=BOARD_URL, html=widget_html())
        )
        self.assertIsNotNone(self.board)

    def test_native_page_aware_adapter_is_auto_discovered(self):
        native = {adapter.name: adapter for adapter in discover_native_adapters()}

        self.assertIs(native["ceipal"], ADAPTER)
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertFalse(ADAPTER.recognizes("https://jobsapi.ceipal.com/APISource/widget.js"))
        self.assertIsNone(ADAPTER.identify_board(BOARD_URL))

    def test_identifies_active_widget_on_safe_first_party_page(self):
        page = Page(
            url="https://old.example.com/jobs",
            final_url=BOARD_URL + "?source=careers#openings",
            html=widget_html(),
        )
        selected = ProviderRegistry((self.adapter,)).board_for_page(page)

        self.assertIsNotNone(selected)
        self.assertIs(selected[0], self.adapter)
        board = selected[1]
        self.assertEqual(board.url, BOARD_URL)
        self.assertEqual(board.provider, "ceipal")
        self.assertFalse(board.replay_safe)
        self.assertEqual(
            json.loads(board.identifier),
            {
                "origin": "https://careers.example.com",
                "api_key": API_KEY,
                "career_portal_id": PORTAL_ID,
            },
        )

    def test_identifies_a_second_synthetic_tenant_without_cross_tenant_state(self):
        second_url = "https://jobs.second-example.test:443/careers/search?campaign=direct#jobs"
        board = self.adapter.identify_board_from_page(
            Page(
                url=second_url,
                html=(
                    "<!-- an inactive widget must not supply either tenant id -->"
                    + widget_html(
                        api_key="second-api-key",
                        portal_id="second-career-portal",
                    )
                ),
                source="synthetic-second-tenant",
            )
        )

        self.assertIsNotNone(board)
        self.assertEqual(board.url, "https://jobs.second-example.test:443/careers/search")
        identity = json.loads(board.identifier)
        self.assertEqual(
            identity,
            {
                "origin": "https://jobs.second-example.test",
                "api_key": "second-api-key",
                "career_portal_id": "second-career-portal",
            },
        )
        self.assertNotEqual(board.identifier, self.board.identifier)

    def test_rejects_commented_or_inexact_or_split_widget_evidence(self):
        cases = [
            f"<!-- {widget_html()} -->",
            widget_html(src="http://jobsapi.ceipal.com/APISource/widget.js"),
            widget_html(src="https://jobsapi.ceipal.com/APISource/widget.js?v=1"),
            widget_html(src="https://jobsapi.ceipal.com/apisource/widget.js"),
            widget_html(api_key=""),
            widget_html(portal_id="   "),
            (
                '<script src="https://jobsapi.ceipal.com/APISource/widget.js" '
                f'data-ceipal-api-key="{API_KEY}"></script>'
                f'<script data-ceipal-career-portal-id="{PORTAL_ID}"></script>'
            ),
        ]

        for html in cases:
            with self.subTest(html=html):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(Page(url=BOARD_URL, html=html))
                )

    def test_rejects_unsafe_page_urls_and_ambiguous_tenants(self):
        unsafe_urls = [
            "http://careers.example.com/find-jobs",
            "https://user@careers.example.com/find-jobs",
            "https://careers.example.com:8443/find-jobs",
        ]
        for url in unsafe_urls:
            with self.subTest(url=url):
                self.assertIsNone(
                    self.adapter.identify_board_from_page(Page(url=url, html=widget_html()))
                )

        ambiguous = widget_html() + widget_html(api_key="other-key")
        self.assertIsNone(
            self.adapter.identify_board_from_page(Page(url=BOARD_URL, html=ambiguous))
        )

    def test_requests_frozen_widget_endpoint_and_classifies_bot_block(self):
        response = Page(
            url=API_URL,
            html=json.dumps(
                {"status": 400, "success": 0, "message": "Bot access is not allowed"}
            ),
            source="frozen-ceipal-response",
        )
        fetcher = RecordingFetcher(page=response)

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="AI Engineer"))

        self.assertEqual(
            fetcher.requests,
            [
                (
                    API_URL,
                    None,
                    {
                        "Accept": "application/json",
                        "X-Referer-Host": "https://jobsapi.ceipal.com/",
                    },
                )
            ],
        )
        self.assertEqual(result.reason_code, "BOT_PROTECTION")
        self.assertFalse(result.retryable)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["inventory_scope"], "title_filtered")
        self.assertNotIn(API_KEY, json.dumps(result.trace))
        self.assertFalse(result.trace["inventory_complete"])

    def test_accepts_only_omission_of_empty_presentation_params(self):
        parts = urlsplit(API_URL)
        query = parse_qsl(parts.query, keep_blank_values=True)
        optional_names = ("themeid", "bgcolor", "job_id")

        for omitted_count in range(len(optional_names) + 1):
            for omitted in combinations(optional_names, omitted_count):
                response_query = [pair for pair in query if pair[0] not in omitted]
                response_url = urlunsplit(
                    parts._replace(query=urlencode(response_query))
                )
                with self.subTest(omitted=omitted):
                    result = self.adapter.list_jobs(
                        RecordingFetcher(
                            page=Page(
                                url=API_URL,
                                final_url=response_url,
                                html=json.dumps(
                                    {
                                        "status": 400,
                                        "message": "Bot access is not allowed",
                                    }
                                ),
                            )
                        ),
                        self.board,
                        JobQuery(),
                    )

                    self.assertEqual(result.reason_code, "BOT_PROTECTION")

    def test_rejects_semantically_changed_response_urls(self):
        changed_urls = {
            "scheme": API_URL.replace("https://", "http://", 1),
            "host": API_URL.replace("careerapi.ceipal.com", "other.ceipal.com", 1),
            "path": API_URL.replace("careerPortalWidget/", "otherWidget/", 1),
            "credential": API_URL.replace(
                "apikey=tenant-api-key", "apikey=other-key", 1
            ),
            "tenant": API_URL.replace(
                "cp_id=tenant-career-portal", "cp_id=other-portal", 1
            ),
            "missing_credential": API_URL.replace("&apikey=tenant-api-key", "", 1),
            "missing_tenant": API_URL.replace("&cp_id=tenant-career-portal", "", 1),
            "nonempty_theme": API_URL.replace("themeid=", "themeid=dark", 1),
            "extra_param": API_URL + "&page=2",
            "duplicate_param": API_URL + "&apikey=tenant-api-key",
            "duplicate_empty_param": API_URL + "&themeid=",
            "fragment": API_URL + "#jobs",
            "empty_fragment": API_URL + "#",
        }

        for change, response_url in changed_urls.items():
            with self.subTest(change=change):
                result = self.adapter.list_jobs(
                    RecordingFetcher(
                        page=Page(
                            url=API_URL,
                            final_url=response_url,
                            html=json.dumps(
                                {
                                    "status": 400,
                                    "message": "Bot access is not allowed",
                                }
                            ),
                        )
                    ),
                    self.board,
                    JobQuery(),
                )

                self.assertEqual(
                    result.reason_code,
                    "PROVIDER_VARIANT_UNSUPPORTED",
                )

    def test_unknown_success_schema_is_unsupported_and_never_constructs_jobs(self):
        response = Page(
            url=API_URL,
            html=json.dumps(
                {
                    "status": 200,
                    "success": 1,
                    "html": '<a href="/job/123">AI Engineer</a>',
                }
            ),
        )

        result = self.adapter.list_jobs(
            RecordingFetcher(page=response), self.board, JobQuery()
        )

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(result.retryable)
        self.assertEqual(result.inventory_scope, "full")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["inventory_scope"], "full")
        self.assertNotIn(API_KEY, json.dumps(result.trace))

    def test_rejects_identifier_origin_tampering_and_api_redirects(self):
        identity = json.loads(self.board.identifier)
        identity["origin"] = "https://other.example.com"
        tampered = JobBoard(
            self.board.url,
            "ceipal",
            json.dumps(identity, separators=(",", ":"), sort_keys=True),
        )
        invalid = self.adapter.list_jobs(RecordingFetcher(), tampered, JobQuery())
        redirected = self.adapter.list_jobs(
            RecordingFetcher(
                page=Page(
                    url=API_URL,
                    final_url="https://evil.example/widget",
                    html='{"status": 400, "message": "Bot access is not allowed"}',
                )
            ),
            self.board,
            JobQuery(),
        )

        self.assertEqual(invalid.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(invalid.inventory_scope, "full")
        self.assertFalse(invalid.inventory_complete)
        self.assertEqual(invalid.trace["inventory_scope"], "full")
        self.assertEqual(redirected.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(redirected.inventory_scope, "full")
        self.assertFalse(redirected.inventory_complete)
        self.assertEqual(redirected.trace["inventory_scope"], "full")

    def test_http_forbidden_fetch_failure_is_typed_and_nonretryable(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("HTTP Error 403: Forbidden")),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "HTTP_FORBIDDEN")
        self.assertFalse(result.retryable)
        self.assertEqual(result.inventory_scope, "full")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["inventory_scope"], "full")
        self.assertNotIn(API_KEY, json.dumps(result.trace))

    def test_timeout_fetch_failure_is_typed_and_retryable(self):
        result = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("The read operation timed out")),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(result.retryable)
        self.assertEqual(result.inventory_scope, "full")
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["inventory_scope"], "full")
        self.assertNotIn(API_KEY, json.dumps(result.trace))

    def test_fetches_validated_iframe_and_one_page_public_inventory(self):
        payload = inventory_payload(
            [
                job(
                    "job/one",
                    "AI Engineer",
                    state="Texas",
                    country="United States",
                    remote_opportunities=0,
                    updated="2026-07-01",
                )
            ]
        )
        fetcher = successful_fetcher(inventory_page(payload))

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery())

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "full")
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.title, "AI Engineer")
        self.assertEqual(
            candidate.url,
            "https://careers.example.com/find-jobs?job_id=job%2Fone",
        )
        self.assertEqual(candidate.location, "Texas, United States")
        self.assertEqual(
            candidate.raw,
            {"job_id": "job/one", "updated": "2026-07-01"},
        )
        self.assertEqual(fetcher.requests[0][0], API_URL)
        self.assertEqual(fetcher.requests[1], (IFRAME_URL, None, None))
        inventory_request = fetcher.requests[2]
        self.assertEqual(inventory_request[0], inventory_url(1))
        self.assertEqual(
            inventory_request[2],
            {
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={MULTIPART_BOUNDARY}",
                "Origin": "https://jobsapi.ceipal.com",
                "Referer": "https://jobsapi.ceipal.com/APISource/v1/index.html",
            },
        )
        body = inventory_request[1].decode("utf-8")
        for name, value in (
            ("page", "1"),
            ("api_key", API_KEY),
            ("method", "CareerPortalJobPostings"),
            ("cp_id", PORTAL_ID),
            ("from_career_portal", "1"),
        ):
            self.assertIn(f'name="{name}"\r\n\r\n{value}\r\n', body)
        self.assertNotIn('name="searchkey"', body)
        serialized_public_output = json.dumps(
            {"trace": result.trace, "raw": candidate.raw}, sort_keys=True
        )
        self.assertNotIn(API_KEY, serialized_public_output)
        self.assertNotIn(PORTAL_ID, serialized_public_output)
        self.assertNotIn("CareerPortalJobPostings/", serialized_public_output)

    def test_title_filter_is_sent_in_deterministic_multipart_body(self):
        fetcher = successful_fetcher(
            inventory_page(inventory_payload([], count=0, pages=0))
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Senior AI Engineer"),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.inventory_scope, "title_filtered")
        body = fetcher.requests[2][1]
        self.assertIsInstance(body, bytes)
        text = body.decode("utf-8")
        self.assertIn(
            'name="searchkey"\r\n\r\nSenior AI Engineer\r\n',
            text,
        )
        self.assertTrue(text.endswith(f"--{MULTIPART_BOUNDARY}--\r\n"))

        for empty_title in ("", "   \t"):
            with self.subTest(empty_title=repr(empty_title)):
                unfiltered_fetcher = successful_fetcher(
                    inventory_page(inventory_payload([], count=0, pages=0))
                )
                unfiltered = self.adapter.list_jobs(
                    unfiltered_fetcher,
                    self.board,
                    JobQuery(title=empty_title),
                )
                self.assertEqual(unfiltered.inventory_scope, "full")
                self.assertNotIn(
                    b'name="searchkey"',
                    unfiltered_fetcher.requests[2][1],
                )

    def test_collects_all_pages_before_marking_inventory_complete(self):
        page_one = inventory_payload(
            [job("one", "Engineer I")],
            count=2,
            limit=1,
            page=1,
            pages=2,
            next_url=inventory_url(2),
        )
        page_two = inventory_payload(
            [job("two", "Engineer II")],
            count=2,
            limit=1,
            page=2,
            pages=2,
            previous=inventory_url(1),
        )
        fetcher = successful_fetcher(
            inventory_page(page_one, page=1),
            inventory_page(page_two, page=2),
        )

        result = self.adapter.list_jobs(fetcher, self.board, JobQuery(title="Engineer"))

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual([item.raw["job_id"] for item in result.candidates], ["one", "two"])
        self.assertEqual(result.trace["records_seen"], 2)
        self.assertEqual(result.trace["page_count"], 2)
        self.assertEqual(result.trace["stop_reason"], "complete")

    def test_accepts_exactly_redacted_snapshot_pagination_paths(self):
        redacted = "https://careerapi.ceipal.com/[REDACTED]/CareerPortalJobPostings/"
        page_one = inventory_payload(
            [job("one", "Engineer I")],
            count=2,
            limit=1,
            page=1,
            pages=2,
            next_url=f"{redacted}?page=2",
        )
        page_two = inventory_payload(
            [job("two", "Engineer II")],
            count=2,
            limit=1,
            page=2,
            pages=2,
            previous=f"{redacted}?page=1",
        )

        result = self.adapter.list_jobs(
            successful_fetcher(
                inventory_page(page_one, page=1),
                inventory_page(page_two, page=2),
            ),
            self.board,
            JobQuery(title="Engineer"),
        )

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual([item.raw["job_id"] for item in result.candidates], ["one", "two"])

        page_one["next"] = page_one["next"].replace("[REDACTED]", "[REDACTED-extra]")
        rejected = self.adapter.list_jobs(
            successful_fetcher(inventory_page(page_one, page=1)),
            self.board,
            JobQuery(title="Engineer"),
        )
        self.assertEqual(rejected.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(rejected.trace["stop_reason"], "pagination_mismatch")

    def test_models_live_num_pages_and_terminal_partial_page_schema(self):
        pages = []
        next_job_id = 1
        for page_number in range(1, 26):
            result_count = 20 if page_number < 25 else 2
            records = []
            for _index in range(result_count):
                records.append(job(str(next_job_id), f"Engineer {next_job_id}"))
                next_job_id += 1
            payload = inventory_payload(
                records,
                count=482,
                limit=20,
                page=page_number,
                pages=25,
                next_url=inventory_url(page_number + 1) if page_number < 25 else None,
                previous=inventory_url(page_number - 1) if page_number > 1 else None,
            )
            self.assertEqual(payload["num_pages"], 25)
            self.assertEqual(payload["page_count"], result_count)
            pages.append(inventory_page(payload, page=page_number))

        result = self.adapter.list_jobs(
            successful_fetcher(*pages),
            self.board,
            JobQuery(),
        )

        self.assertIsNone(result.reason_code)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(result.candidates), 482)
        self.assertEqual(result.trace["total"], 482)
        self.assertEqual(result.trace["expected_page_count"], 25)
        self.assertEqual(result.trace["page_count"], 25)

    def test_valid_zero_result_inventory_is_complete(self):
        result = self.adapter.list_jobs(
            successful_fetcher(
                inventory_page(inventory_payload([], count=0, limit=25, pages=0))
            ),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertFalse(result.retryable)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.trace["total"], 0)

    def test_duplicate_and_malformed_inventory_records_are_rejected(self):
        payloads = {
            "duplicate": inventory_payload(
                [job("same", "One"), job("same", "Two")],
                count=2,
            ),
            "non_dict": inventory_payload(["not-a-record"], count=1),
            "missing_id": inventory_payload([{"public_job_title": "Engineer"}], count=1),
            "missing_title": inventory_payload([{"id": "job-1"}], count=1),
            "control_character": inventory_payload([job("bad\njob", "Engineer")], count=1),
            "missing_next_key": {
                key: value
                for key, value in inventory_payload([job()], count=1).items()
                if key != "next"
            },
            "wrong_current_page_count": {
                **inventory_payload([job()], count=1),
                "page_count": 0,
            },
        }

        for name, payload in payloads.items():
            with self.subTest(name=name):
                result = self.adapter.list_jobs(
                    successful_fetcher(inventory_page(payload)),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.candidates, [])

    def test_rejects_mutated_or_ambiguous_iframe_contracts(self):
        variants = {
            "host": IFRAME_URL.replace("jobsapi.ceipal.com", "evil.example"),
            "path": IFRAME_URL.replace("/APISource/v1/index.html", "/other"),
            "api_key": IFRAME_URL.replace(API_KEY, "other-key"),
            "portal": IFRAME_URL.replace(PORTAL_ID, "other-portal"),
            "job_id": IFRAME_URL.replace("job_id=", "job_id=123"),
            "bad_color": IFRAME_URL.replace("bgcolor=1ba1ff", "bgcolor=purple"),
            "extra": IFRAME_URL + "&theme=dark",
            "duplicate": IFRAME_URL + f"&api_key={API_KEY}",
            "fragment": IFRAME_URL + "#jobs",
        }
        for name, iframe_url in variants.items():
            with self.subTest(name=name):
                result = self.adapter.list_jobs(
                    RecordingFetcher(responses=[wrapper_page(iframe_url)]),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

        commented = Page(
            url=API_URL,
            html=json.dumps(
                {
                    "html": (
                        f"<!-- <iframe src='{IFRAME_URL}&extra=ignored'></iframe> -->"
                        f"<iframe src='{IFRAME_URL}'></iframe>"
                    )
                }
            ),
        )
        result = self.adapter.list_jobs(
            RecordingFetcher(
                responses=[
                    commented,
                    iframe_page(),
                    inventory_page(inventory_payload([], count=0, pages=0)),
                ]
            ),
            self.board,
            JobQuery(),
        )
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")

    def test_requires_frozen_iframe_structure_and_rejects_iframe_redirect(self):
        cases = {
            "no_structure": iframe_page(html="<html><body>Careers</body></html>"),
            "invalid_script_port": iframe_page(
                html=(
                    '<script src="https://jobsapi.ceipal.com:invalid/'
                    'APISource/v1/js/app.min.js"></script>'
                )
            ),
            "redirect": iframe_page(final_url=IFRAME_URL.replace(API_KEY, "other-key")),
        }
        for name, page in cases.items():
            with self.subTest(name=name):
                result = self.adapter.list_jobs(
                    RecordingFetcher(responses=[wrapper_page(), page]),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
                self.assertFalse(result.inventory_complete)

    def test_location_matches_official_remote_and_multiple_location_priority(self):
        payload = inventory_payload(
            [
                job(
                    "onsite",
                    "Onsite Engineer",
                    remote_opportunities=0,
                    multpile_job_location="Austin / Dallas",
                    state="Texas",
                    country="United States",
                ),
                job(
                    "remote",
                    "Remote Engineer",
                    remote_opportunities=1,
                    multpile_job_location="N/A",
                    state="Texas",
                    country="United States",
                ),
                job(
                    "fallback",
                    "Fallback Engineer",
                    remote_opportunities=False,
                    multpile_job_location="N/A",
                    state="California",
                    country="United States",
                ),
            ],
            count=3,
        )
        result = self.adapter.list_jobs(
            successful_fetcher(inventory_page(payload)),
            self.board,
            JobQuery(),
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(
            [candidate.location for candidate in result.candidates],
            ["Austin / Dallas", "Remote Job", "California, United States"],
        )
        serialized = json.dumps(
            [candidate.location for candidate in result.candidates]
        )
        self.assertNotIn('"0"', serialized)
        self.assertNotIn("False", serialized)
        self.assertNotIn("N/A", serialized)

    def test_rejects_inventory_redirects_without_leaking_credential_path(self):
        redirects = {
            "cross_host": "https://evil.example/tenant-api-key/CareerPortalJobPostings/?page=1",
            "changed_key": inventory_url(1).replace(API_KEY, "other-key"),
            "extra_query": inventory_url(1) + "&cursor=abc",
            "duplicate_page": inventory_url(1) + "&page=1",
        }
        for name, final_url in redirects.items():
            with self.subTest(name=name):
                result = self.adapter.list_jobs(
                    successful_fetcher(
                        inventory_page(
                            inventory_payload([job()]),
                            final_url=final_url,
                        )
                    ),
                    self.board,
                    JobQuery(),
                )
                self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
                serialized = json.dumps(result.trace, sort_keys=True)
                self.assertNotIn(API_KEY, serialized)
                self.assertNotIn(PORTAL_ID, serialized)

    def test_pagination_mismatch_and_cycle_are_incomplete(self):
        mismatch = inventory_payload(
            [job("one")],
            count=2,
            limit=1,
            pages=2,
            next_url=inventory_url(3),
        )
        mismatch_result = self.adapter.list_jobs(
            successful_fetcher(inventory_page(mismatch)),
            self.board,
            JobQuery(),
        )
        self.assertEqual(mismatch_result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(mismatch_result.trace["stop_reason"], "pagination_mismatch")

        first = inventory_payload(
            [job("one")],
            count=3,
            limit=1,
            pages=3,
            next_url=inventory_url(2),
        )
        cycle = inventory_payload(
            [job("two")],
            count=3,
            limit=1,
            page=2,
            pages=3,
            next_url=inventory_url(2),
            previous=inventory_url(1),
        )
        cycle_result = self.adapter.list_jobs(
            successful_fetcher(
                inventory_page(first),
                inventory_page(cycle, page=2),
            ),
            self.board,
            JobQuery(),
        )
        self.assertEqual(cycle_result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(cycle_result.trace["stop_reason"], "pagination_cycle")
        self.assertEqual(len(cycle_result.candidates), 1)

    def test_page_cap_is_retryable_and_preserves_fifty_pages(self):
        pages = []
        for page_number in range(1, 51):
            payload = inventory_payload(
                [job(str(page_number), f"Engineer {page_number}")],
                count=51,
                limit=1,
                page=page_number,
                pages=51,
                next_url=inventory_url(page_number + 1),
                previous=inventory_url(page_number - 1) if page_number > 1 else None,
            )
            pages.append(inventory_page(payload, page=page_number))

        result = self.adapter.list_jobs(
            successful_fetcher(*pages),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(len(result.candidates), 50)
        self.assertEqual(result.trace["page_count"], 50)
        self.assertEqual(result.trace["stop_reason"], "page_cap_reached")

    def test_second_page_fetch_failure_preserves_first_page_candidates(self):
        first = inventory_payload(
            [job("one", "AI Engineer")],
            count=2,
            limit=1,
            pages=2,
            next_url=inventory_url(2),
        )
        result = self.adapter.list_jobs(
            successful_fetcher(
                inventory_page(first),
                FetchError("The read operation timed out"),
            ),
            self.board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(result.retryable)
        self.assertFalse(result.inventory_complete)
        self.assertEqual([item.raw["job_id"] for item in result.candidates], ["one"])
        self.assertEqual(result.trace["stop_reason"], "inventory_fetch_failed")

    def test_bot_responses_at_iframe_and_inventory_are_classified(self):
        cases = {
            "iframe": RecordingFetcher(
                responses=[wrapper_page(), iframe_page(html="Bot access is not allowed")]
            ),
            "inventory": successful_fetcher(
                inventory_page({}, html='{"message":"Bot access is not allowed"}')
            ),
        }
        for phase, fetcher in cases.items():
            with self.subTest(phase=phase):
                result = self.adapter.list_jobs(fetcher, self.board, JobQuery())
                self.assertEqual(result.reason_code, "BOT_PROTECTION")
                self.assertFalse(result.retryable)
                self.assertFalse(result.inventory_complete)
                self.assertNotIn(API_KEY, json.dumps(result.trace))
                self.assertNotIn(PORTAL_ID, json.dumps(result.trace))


if __name__ == "__main__":
    unittest.main()
