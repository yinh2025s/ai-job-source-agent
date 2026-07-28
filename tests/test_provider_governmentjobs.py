import json
import unittest

from job_source_agent.job_board import JobBoard, is_replay_safe_job_board
from job_source_agent.providers.base import JobQuery, ProviderAdapter
from job_source_agent.providers.governmentjobs import ADAPTER, GovernmentJobsAdapter
from job_source_agent.providers.registry import discover_native_adapters
from job_source_agent.rendered_fetcher import FORCE_RENDER_HEADER
from job_source_agent.web import FetchError, Page


LUBBOCK = "https://www.governmentjobs.com/careers/lubbock"
CSTX = "https://www.governmentjobs.com/careers/cstx"
SEATTLE = "https://www.governmentjobs.com/careers/seattle"


class RecordingFetcher:
    def __init__(self, response=None, error=None, final_url=None, responses=None):
        self.responses = list(responses) if responses is not None else [board_html(), response]
        self.error = error
        self.final_url = final_url
        self.requests = []
        self.interactions = []

    def fetch(self, url, data=None, headers=None, *, interaction=None):
        self.requests.append((url, data, headers))
        self.interactions.append(interaction)
        if self.error:
            raise self.error
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, Page):
            return response
        raw = response if isinstance(response, str) else json.dumps(response)
        return Page(url=url, final_url=self.final_url or url, html=raw, source="fixture-governmentjobs")


def board_html(tenant="lubbock", *, search_form=False):
    form = (
        '<form class="search-form">'
        '<input id="keyword-search-input" name="keyword" placeholder="Search" '
        'data-action="/careers/Home/SearchByKeyword">'
        '<button type="submit">Search</button></form>'
        if search_form
        else ""
    )
    return (
        f'<html data-agency-folder-name="{tenant}">'
        f"<title>Job Opportunities</title>{form}</html>"
    )


def rendered_inventory(
    tenant="lubbock",
    *,
    total=1,
    title="Information Security and Compliance Analyst",
    location="Information Technology, Lubbock, TX",
):
    rows = (
        '<article class="job-item">'
        f'<a href="/careers/{tenant}/jobs/5342417-0/'
        f'{title.lower().replace(" ", "-")}">'
        f'{title}<span class="location">{location}</span></a></article>'
        if total
        else ""
    )
    return (
        f'<html data-agency-folder-name="{tenant}">'
        f'<div id="number-found-items">{total} '
        f'{"job" if total == 1 else "jobs"} found</div>'
        f'<div id="job-list-container">{rows}</div>'
        '<div id="job-list-overlay" style="display: none; z-index: -1"></div>'
        "</html>"
    )


def job(tenant="lubbock", job_id=5342417, title="Information Security and Compliance Analyst", location="Information Technology, Lubbock, TX"):
    return {
        "JobId": job_id,
        "JobTitle": title,
        "Location": location,
        "JobUrl": f"/careers/{tenant}/jobs/{job_id}-0/{title.lower().replace(' ', '-')}",
    }


class GovernmentJobsAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = GovernmentJobsAdapter()
        self.board = JobBoard(LUBBOCK, "governmentjobs", "lubbock", replay_safe=True)

    def test_auto_discovered_and_canonicalizes_listing_and_detail_urls(self):
        self.assertIsInstance(ADAPTER, ProviderAdapter)
        self.assertIn("governmentjobs", {item.name for item in discover_native_adapters()})
        accepted = (
            LUBBOCK,
            LUBBOCK + "/",
            LUBBOCK + "?department=Information+Technology",
            LUBBOCK + "/jobs/5342417-0/information-security-and-compliance-analyst",
            "https://www.governmentjobs.com:443/careers/lubbock/jobs/5342417/information-security-analyst",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(self.adapter.recognizes(url))
                self.assertEqual(self.adapter.identify_board(url), self.board)

    def test_canonical_boards_are_replay_safe_for_multiple_tenants(self):
        for tenant, url in (
            ("lubbock", LUBBOCK),
            ("cstx", CSTX),
            ("seattle", SEATTLE),
        ):
            with self.subTest(tenant=tenant):
                board = self.adapter.identify_board(url)
                self.assertEqual(
                    board,
                    JobBoard(url, "governmentjobs", tenant, replay_safe=True),
                )
                self.assertTrue(is_replay_safe_job_board(board))

    def test_tampered_board_locators_are_not_replay_safe(self):
        tampered = (
            JobBoard(
                "https://governmentjobs.com/careers/lubbock",
                "governmentjobs",
                "lubbock",
                replay_safe=True,
            ),
            JobBoard(
                LUBBOCK + "/promotionaljobs",
                "governmentjobs",
                "lubbock",
                replay_safe=True,
            ),
            JobBoard(LUBBOCK, "governmentjobs", "cstx", replay_safe=True),
            JobBoard(LUBBOCK, "governmentjobs", "LUBBOCK", replay_safe=True),
        )
        for board in tampered:
            with self.subTest(board=board):
                self.assertFalse(is_replay_safe_job_board(board))

    def test_rejects_unsafe_ambiguous_and_non_public_routes(self):
        rejected = (
            "http://www.governmentjobs.com/careers/lubbock",
            "https://governmentjobs.com/careers/lubbock",
            "https://www.governmentjobs.com.evil.test/careers/lubbock",
            "https://user@www.governmentjobs.com/careers/lubbock",
            "https://www.governmentjobs.com:8443/careers/lubbock",
            "https://www.governmentjobs.com/careers/bad_tenant",
            "https://www.governmentjobs.com/careers/lubbock/promotionaljobs",
            "https://www.governmentjobs.com/careers/lubbock/jobs/newprint/5342417",
            "https://www.governmentjobs.com/careers/lubbock/jobs/5342417",
            LUBBOCK + "#jobs",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(self.adapter.recognizes(url))
                self.assertIsNone(self.adapter.identify_board(url))

    def test_lists_complete_json_inventory_and_reports_query_matches(self):
        fetcher = RecordingFetcher({"TotalCount": 1, "Jobs": [job()]})
        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title=" information security AND compliance analyst ", location="Lubbock, TX"),
        )

        self.assertEqual(fetcher.requests[0], (LUBBOCK, None, None))
        url, data, headers = fetcher.requests[1]
        self.assertEqual(
            url,
            LUBBOCK
            + "?keywords=information+security+AND+compliance+analyst",
        )
        self.assertIsNone(data)
        self.assertEqual(headers["Referer"], LUBBOCK)
        self.assertEqual(headers[FORCE_RENDER_HEADER], "force")
        self.assertNotIn("X-Requested-With", headers)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.inventory_scope, "title_filtered")
        self.assertEqual(result.candidates[0].url, LUBBOCK + "/jobs/5342417-0/information-security-and-compliance-analyst")
        self.assertEqual(result.candidates[0].location, "Information Technology, Lubbock, TX")
        self.assertTrue(result.trace["exact_title_found"])
        self.assertTrue(result.trace["location_match_found"])

    def test_uses_declared_interaction_for_complete_title_filtered_inventory(self):
        fetcher = RecordingFetcher(
            responses=[
                board_html(search_form=True),
                rendered_inventory(),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(
                title="Information Security and Compliance Analyst",
                location="Lubbock, TX",
            ),
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            result.candidates[0].url,
            LUBBOCK
            + "/jobs/5342417-0/information-security-and-compliance-analyst",
        )
        self.assertEqual(len(fetcher.requests), 2)
        self.assertIsNotNone(fetcher.interactions[1])
        self.assertEqual(
            fetcher.interactions[1].target_title,
            "Information Security and Compliance Analyst",
        )
        self.assertEqual(
            result.trace["interactive_search"]["status"],
            "submitted",
        )
        self.assertEqual(
            result.trace["variant"],
            "governmentjobs_public_xhr_html",
        )

    def test_unchanged_interaction_falls_back_to_static_inventory(self):
        landing = board_html(search_form=True)
        fetcher = RecordingFetcher(
            responses=[
                landing,
                landing,
                {"TotalCount": 0, "Jobs": []},
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Missing Role"),
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(len(fetcher.requests), 3)
        self.assertEqual(
            result.trace["interactive_search"]["status"],
            "transport_unchanged",
        )

    def test_static_fallback_failure_preserves_interaction_trace(self):
        landing = board_html(search_form=True)
        fetcher = RecordingFetcher(
            responses=[
                landing,
                landing,
                FetchError("static endpoint failed", reason_code="NETWORK_TIMEOUT"),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Missing Role"),
        )

        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.reason_code, "NETWORK_TIMEOUT")
        self.assertEqual(
            result.trace["interactive_search"]["status"],
            "transport_unchanged",
        )
        self.assertRegex(
            result.trace["interactive_search"]["fingerprint"],
            r"^[0-9a-f]{64}$",
        )
        self.assertEqual(
            result.trace["interactive_search"]["fallback_kind"],
            "canonical_keyword_route",
        )

    def test_interaction_timeout_uses_complete_canonical_keyword_route(self):
        title = "Information Security and Compliance Analyst"
        fetcher = RecordingFetcher(
            responses=[
                board_html(search_form=True),
                FetchError(
                    "browser interaction timed out",
                    reason_code="OPENING_DISCOVERY_INCOMPLETE",
                ),
                rendered_inventory(title=title),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title=title, location="Lubbock, TX"),
        )

        keyword_url = LUBBOCK + "?keywords=Information+Security+and+Compliance+Analyst"
        self.assertEqual(fetcher.requests[2][0], keyword_url)
        self.assertEqual(
            fetcher.requests[2][2][FORCE_RENDER_HEADER],
            "force",
        )
        self.assertIsNone(fetcher.interactions[2])
        self.assertTrue(result.inventory_complete)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].raw["tenant"], "lubbock")
        self.assertEqual(result.trace["api_urls"], [keyword_url])
        self.assertEqual(
            result.trace["interactive_search"]["status"],
            "fetch_failed",
        )
        self.assertEqual(
            result.trace["interactive_search"]["fallback_kind"],
            "canonical_keyword_route",
        )

    def test_keyword_route_rejects_cross_tenant_final_url(self):
        title = "Information Security and Compliance Analyst"
        fetcher = RecordingFetcher(
            responses=[
                board_html(search_form=True),
                FetchError(
                    "browser interaction timed out",
                    reason_code="OPENING_DISCOVERY_INCOMPLETE",
                ),
                Page(
                    CSTX,
                    rendered_inventory("cstx", title=title),
                    final_url=CSTX + "?keywords=Information+Security+and+Compliance+Analyst",
                    source="fixture-governmentjobs",
                ),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title=title),
        )

        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.candidates, [])
        self.assertIn("cstx", result.trace["rejected_final_url"])

    def test_keyword_route_rejects_rewritten_or_ambiguous_query(self):
        title = "Information Security and Compliance Analyst"
        for final_url in (
            LUBBOCK + "?keywords=Different+Role",
            LUBBOCK
            + "?keywords=Information+Security+and+Compliance+Analyst&page=2",
        ):
            with self.subTest(final_url=final_url):
                result = self.adapter.list_jobs(
                    RecordingFetcher(
                        responses=[
                            board_html(search_form=True),
                            FetchError(
                                "browser interaction timed out",
                                reason_code="OPENING_DISCOVERY_INCOMPLETE",
                            ),
                            Page(
                                final_url,
                                rendered_inventory(title=title),
                                final_url=final_url,
                                source="fixture-governmentjobs",
                            ),
                        ]
                    ),
                    self.board,
                    JobQuery(title=title),
                )

                self.assertFalse(result.inventory_complete)
                self.assertEqual(
                    result.reason_code,
                    "PROVIDER_VARIANT_UNSUPPORTED",
                )
                self.assertEqual(
                    result.trace["stop_reason"],
                    "keyword_route_identity_mismatch",
                )
                self.assertEqual(result.candidates, [])

    def test_keyword_route_rejects_malformed_or_incomplete_inventory(self):
        title = "Information Security and Compliance Analyst"
        malformed = rendered_inventory(title=title).replace(
            "1 job found",
            "2 jobs found",
        )
        result = self.adapter.list_jobs(
            RecordingFetcher(
                responses=[
                    board_html(search_form=True),
                    FetchError(
                        "browser interaction timed out",
                        reason_code="OPENING_DISCOVERY_INCOMPLETE",
                    ),
                    malformed,
                ]
            ),
            self.board,
            JobQuery(title=title),
        )

        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(result.trace["stop_reason"], "inventory_count_mismatch")
        self.assertEqual(result.candidates, [])

    def test_keyword_route_does_not_accept_loading_shell_as_empty_inventory(self):
        title = "Information Security and Compliance Analyst"
        loading_shell = rendered_inventory(total=0).replace(
            "display: none; z-index: -1",
            "display: block; z-index: 1",
        )
        result = self.adapter.list_jobs(
            RecordingFetcher(
                responses=[
                    board_html(search_form=True),
                    FetchError(
                        "browser interaction timed out",
                        reason_code="OPENING_DISCOVERY_INCOMPLETE",
                    ),
                    loading_shell,
                ]
            ),
            self.board,
            JobQuery(title=title),
        )

        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(result.trace["stop_reason"], "javascript_inventory_shell")

    def test_static_cross_tenant_failure_preserves_interaction_trace(self):
        landing = board_html(search_form=True)
        fetcher = RecordingFetcher(
            responses=[
                landing,
                landing,
                Page(CSTX, board_html("cstx"), final_url=CSTX),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Missing Role"),
        )

        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(
            result.trace["interactive_search"]["status"],
            "transport_unchanged",
        )

    def test_interactive_capability_absence_falls_back_without_crashing(self):
        landing = board_html(search_form=True)

        class LegacyFetcher:
            def __init__(self):
                self.calls = 0

            def fetch(self, url, data=None, headers=None):
                self.calls += 1
                if self.calls == 1:
                    return Page(url, landing, final_url=url)
                return Page(
                    url,
                    json.dumps({"TotalCount": 0, "Jobs": []}),
                    final_url=url,
                )

        fetcher = LegacyFetcher()
        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Missing Role"),
        )

        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(fetcher.calls, 2)
        self.assertEqual(
            result.trace["interactive_search"]["status"],
            "capability_unavailable",
        )

    def test_cross_tenant_interactive_result_fails_closed(self):
        fetcher = RecordingFetcher(
            responses=[
                board_html(search_form=True),
                Page(
                    CSTX,
                    rendered_inventory("cstx"),
                    final_url=CSTX,
                    source="browser",
                ),
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Information Security and Compliance Analyst"),
        )

        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(
            result.trace["stop_reason"],
            "interactive_cross_tenant_or_missing_page_identity",
        )

    def test_incomplete_interactive_count_does_not_become_verified_no_match(self):
        incomplete = (
            '<html data-agency-folder-name="lubbock">'
            '<div id="number-found-items">2 jobs found</div>'
            '<div id="job-list-container">'
            '<article class="job-item">'
            '<a href="/careers/lubbock/jobs/5342417-0/data-analyst">'
            "Data Analyst</a></article></div></html>"
        )
        fetcher = RecordingFetcher(
            responses=[
                board_html(search_form=True),
                incomplete,
                {"TotalCount": 0, "Jobs": []},
            ]
        )

        result = self.adapter.list_jobs(
            fetcher,
            self.board,
            JobQuery(title="Data Analyst"),
        )

        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertEqual(len(fetcher.requests), 2)
        self.assertEqual(
            result.trace["interactive_search"]["parse_stop_reason"],
            "inventory_count_mismatch",
        )

    def test_parses_complete_html_fragment_and_preserves_same_tenant_detail(self):
        html = """
        <div id="number-found-items">1 job found</div>
        <article class="job-item">
          <a href="/careers/cstx/jobs/5372109-0/hr-operations-and-services-manager">
            HR Operations and Services Manager
          </a>
        </article>
        """
        board = JobBoard(CSTX, "governmentjobs", "cstx", replay_safe=True)
        result = self.adapter.list_jobs(
            RecordingFetcher(responses=[board_html("cstx"), html]), board, JobQuery()
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].provider, "governmentjobs")
        self.assertEqual(result.candidates[0].raw["tenant"], "cstx")
        self.assertEqual(result.trace["variant"], "governmentjobs_public_xhr_html")

    def test_verified_empty_inventory_is_complete(self):
        result = self.adapter.list_jobs(
            RecordingFetcher({"TotalCount": 0, "Jobs": []}), self.board, JobQuery(title="missing")
        )
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.reason_code, "EMPTY_PROVIDER_RESPONSE")
        self.assertEqual(result.candidates, [])

    def test_rejects_cross_tenant_response_url_and_records(self):
        response_redirect = self.adapter.list_jobs(
            RecordingFetcher(responses=[Page(LUBBOCK, board_html()), Page(CSTX, "")]),
            self.board,
            JobQuery(),
        )
        cross_tenant_record = self.adapter.list_jobs(
            RecordingFetcher({"TotalCount": 1, "Jobs": [job(tenant="cstx")]}),
            self.board,
            JobQuery(),
        )
        self.assertEqual(response_redirect.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(cross_tenant_record.reason_code, "INVALID_STRUCTURED_DATA")
        self.assertFalse(response_redirect.inventory_complete)
        self.assertEqual(cross_tenant_record.candidates, [])

    def test_rejects_cross_tenant_page_identity_and_javascript_shell(self):
        cross_tenant = self.adapter.list_jobs(
            RecordingFetcher(responses=[board_html("cstx")]), self.board, JobQuery()
        )
        shell = '<html data-agency-folder-name="lubbock"><div>0 jobs found</div><div id="job-list-container"></div></html>'
        javascript_shell = self.adapter.list_jobs(
            RecordingFetcher(responses=[board_html(), shell]), self.board, JobQuery()
        )
        self.assertEqual(cross_tenant.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertEqual(javascript_shell.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")
        self.assertFalse(javascript_shell.inventory_complete)

    def test_invalid_counts_duplicates_and_unsafe_details_fail_closed(self):
        duplicate = job()
        cases = (
            {"TotalCount": 2, "Jobs": [job()]},
            {"TotalCount": 2, "Jobs": [duplicate, duplicate]},
            {"TotalCount": 1, "Jobs": [job() | {"JobUrl": "https://evil.test/job/5342417"}]},
            {"TotalCount": 1, "Jobs": [job() | {"JobUrl": "//evil.test/careers/lubbock/jobs/5342417-0/fake"}]},
            {"TotalCount": 1, "Jobs": [job() | {"JobId": 99}]},
            {"TotalCount": True, "Jobs": []},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                result = self.adapter.list_jobs(RecordingFetcher(payload), self.board, JobQuery())
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.reason_code, "INVALID_STRUCTURED_DATA")
                self.assertEqual(result.candidates, [])

    def test_typed_transport_cap_and_tampered_board_failures(self):
        timeout = self.adapter.list_jobs(
            RecordingFetcher(error=FetchError("read operation timed out")), self.board, JobQuery()
        )
        capped = self.adapter.list_jobs(
            RecordingFetcher({"TotalCount": 2001, "Jobs": []}), self.board, JobQuery()
        )
        tampered = self.adapter.list_jobs(
            RecordingFetcher({"TotalCount": 0, "Jobs": []}),
            JobBoard(LUBBOCK, "governmentjobs", "cstx"),
            JobQuery(),
        )
        self.assertEqual(timeout.reason_code, "NETWORK_TIMEOUT")
        self.assertTrue(timeout.retryable)
        self.assertEqual(capped.reason_code, "FETCH_BUDGET_EXHAUSTED")
        self.assertEqual(tampered.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_preserves_typed_budget_failure_reasons(self):
        for reason in (
            "COMPANY_TIME_BUDGET_EXHAUSTED",
            "FETCH_BUDGET_EXHAUSTED",
        ):
            for message in (
                "human-readable live failure",
                "replay-normalized failure text",
            ):
                with self.subTest(reason=reason, message=message):
                    result = self.adapter.list_jobs(
                        RecordingFetcher(
                            error=FetchError(message, reason_code=reason)
                        ),
                        self.board,
                        JobQuery(),
                    )
                    self.assertEqual(result.reason_code, reason)
                    self.assertTrue(result.retryable)
                    self.assertEqual(result.trace["stop_reason"], "inventory_fetch_failed")


if __name__ == "__main__":
    unittest.main()
