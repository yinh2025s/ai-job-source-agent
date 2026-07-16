import json
import unittest

from job_source_agent.opening_matcher import JobOpeningMatcher
from job_source_agent.job_board import DiscoveredJobBoard
from job_source_agent.providers.base import JobQuery
from job_source_agent.providers.greenhouse import GreenhouseAdapter
from job_source_agent.web import Page


def next_data_page(*jobs):
    payload = {"props": {"pageProps": {"jobs": list(jobs)}}}
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}"
        "</script>"
    )


def greenhouse_job(job_id, title, url, **extra):
    return {
        "id": job_id,
        "title": title,
        "absolute_url": url,
        "requisition_id": f"REQ-{job_id}",
        "data_compliance": [],
        **extra,
    }


def inline_assignment_job(variable, job_id, title, url, board_token="acme"):
    return (
        f'{variable}.absolute_url={json.dumps(url)};'
        f'{variable}.data_compliance=[];'
        f'{variable}.id={job_id};'
        f'{variable}.title={json.dumps(title)};'
        f'{variable}.company_name="Acme";'
        f'{variable}.first_published="2026-01-01";'
        f'{variable}.boardToken={json.dumps(board_token)};'
    )


def inline_assignment_page(*records, links=(123, 456)):
    anchors = "".join(f'<a href="/jobs/{job_id}">Role</a>' for job_id in links)
    return anchors + "<script>" + "".join(records) + "</script>"


class MappingFetcher:
    def __init__(self, board_url, html, final_url=None, pages=None):
        self.board_url = board_url
        self.html = html
        self.final_url = final_url
        self.urls = []
        self.pages = pages

    def fetch(self, url, data=None, headers=None):
        self.urls.append(url)
        if self.pages is not None:
            return self.pages[url]
        return Page(
            url=url,
            final_url=self.final_url or url,
            html=self.html,
            source="greenhouse-custom-fixture",
        )


class GreenhouseAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = GreenhouseAdapter()

    def test_host_recognition_rejects_lookalikes_credentials_and_nonstandard_ports(self):
        self.assertTrue(self.adapter.recognizes("https://boards.greenhouse.io/acme"))
        self.assertFalse(self.adapter.recognizes("https://greenhouse.io.evil.example/acme"))
        self.assertFalse(self.adapter.recognizes("https://evil@boards.greenhouse.io/acme"))
        self.assertFalse(self.adapter.recognizes("https://boards.greenhouse.io:8443/acme"))

    def test_identifies_custom_frontend_only_from_complete_greenhouse_records(self):
        url = "https://careers.example.org/careers"
        page = Page(
            url=url,
            html=next_data_page(
                greenhouse_job(123, "Data Analyst", f"{url}/123?gh_jid=123")
            ),
        )

        board = self.adapter.identify_board_from_page(page)

        self.assertEqual(board.provider, "greenhouse")
        self.assertEqual(board.identifier, "custom:careers.example.org")
        weak = Page(
            url=url,
            html=next_data_page({"id": 123, "title": "Data Analyst"}),
        )
        self.assertIsNone(self.adapter.identify_board_from_page(weak))

    def test_page_derived_board_with_sensitive_query_is_not_checkpoint_safe(self):
        url = "https://careers.example.org/careers?client_secret=do-not-persist"
        page = Page(
            url=url,
            html=next_data_page(
                greenhouse_job(
                    123,
                    "Data Analyst",
                    "https://careers.example.org/careers/123?gh_jid=123",
                )
            ),
        )
        board = self.adapter.identify_board_from_page(page)

        with self.assertRaises(ValueError):
            DiscoveredJobBoard(
                board=board,
                detection_method="page_evidence",
                evidence_url=url,
            ).to_checkpoint_payload()

    def test_lists_and_deduplicates_same_origin_custom_frontend_jobs(self):
        url = "https://careers.example.org/careers"
        accepted = greenhouse_job(
            123,
            "Data Analyst II",
            "https://careers.example.org/careers/123?gh_jid=123",
            location={"name": "New York, NY"},
        )
        html = next_data_page(
            accepted,
            accepted,
            greenhouse_job(456, "External", "https://evil.example/jobs/456"),
            greenhouse_job(789, "Malformed", "http://[invalid"),
        )
        board = self.adapter.identify_board_from_page(Page(url=url, html=html))

        result = self.adapter.list_jobs(
            MappingFetcher(url, html),
            board,
            JobQuery(title="Data Analyst II"),
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, "Data Analyst II")
        self.assertEqual(result.candidates[0].location, "New York, NY")
        self.assertEqual(result.trace["variant"], "custom_frontend")

    def test_custom_frontend_rejects_cross_origin_redirect(self):
        url = "https://careers.example.org/careers"
        html = next_data_page(
            greenhouse_job(123, "Data Analyst", f"{url}/123?gh_jid=123")
        )
        board = self.adapter.identify_board_from_page(Page(url=url, html=html))

        result = self.adapter.list_jobs(
            MappingFetcher(url, html, final_url="https://evil.example/careers"),
            board,
            JobQuery(),
        )

        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_opening_matcher_routes_custom_frontend_to_greenhouse_adapter(self):
        url = "https://careers.example.org/careers"
        html = next_data_page(
            greenhouse_job(
                123,
                "Data Analyst II",
                "https://careers.example.org/careers/123?gh_jid=123",
            )
        )

        match, trace = JobOpeningMatcher(MappingFetcher(url, html)).match(
            url,
            "Data Analyst II",
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.provider, "greenhouse")
        self.assertEqual(trace["provider"], "greenhouse")
        self.assertEqual(trace["provider_api"]["adapter_trace"]["variant"], "custom_frontend")

    def test_maps_inline_assignment_record_to_observed_first_party_detail_route(self):
        url = "https://careers.example.org/jobs"
        title = "Account Executive, Brand Partnerships"
        html = inline_assignment_page(
            inline_assignment_job(
                "a",
                123,
                title,
                "https://job-boards.greenhouse.io/acme/jobs/123",
            ),
            inline_assignment_job(
                "b",
                456,
                "Sales Manager",
                "https://job-boards.greenhouse.io/acme/jobs/456",
            ),
        )

        board = self.adapter.identify_board_from_page(Page(url=url, html=html))
        result = self.adapter.list_jobs(MappingFetcher(url, html), board, JobQuery(title=title))

        self.assertEqual(board.identifier, "custom:careers.example.org")
        self.assertEqual(result.candidates[0].title, title)
        self.assertEqual(result.candidates[0].url, "https://careers.example.org/jobs/123")
        self.assertEqual(result.trace["inventory_scope"], "full")

    def test_inline_assignments_fail_closed_without_same_variable_or_stable_template(self):
        url = "https://careers.example.org/jobs"
        split_record = (
            'a.absolute_url="https://job-boards.greenhouse.io/acme/jobs/123";'
            "b.data_compliance=[];b.id=123;"
            'c.title="Data Analyst";c.company_name="Acme";c.boardToken="acme";'
        )
        one_link = inline_assignment_page(
            inline_assignment_job(
                "d",
                123,
                "Data Analyst",
                "https://job-boards.greenhouse.io/acme/jobs/123",
            ),
            links=(123,),
        )

        self.assertIsNone(
            self.adapter.identify_board_from_page(
                Page(url=url, html=inline_assignment_page(split_record))
            )
        )
        self.assertIsNone(self.adapter.identify_board_from_page(Page(url=url, html=one_link)))

        ambiguous_templates = inline_assignment_page(
            inline_assignment_job(
                "e",
                123,
                "Data Analyst",
                "https://job-boards.greenhouse.io/acme/jobs/123",
            ),
            links=(123, 456),
        ).replace(
            "<script>",
            '<a href="/careers/jobs/123">Role</a><a href="/careers/jobs/456">Role</a><script>',
        )
        self.assertIsNone(
            self.adapter.identify_board_from_page(Page(url=url, html=ambiguous_templates))
        )

    def test_inline_assignments_reject_cross_tenant_and_evil_urls(self):
        url = "https://careers.example.org/jobs"
        html = inline_assignment_page(
            inline_assignment_job(
                "a",
                123,
                "Wrong Tenant",
                "https://job-boards.greenhouse.io/other/jobs/123",
                board_token="acme",
            ),
            inline_assignment_job(
                "b",
                456,
                "Evil",
                "https://evil.example/jobs/456",
            ),
        )

        self.assertIsNone(self.adapter.identify_board_from_page(Page(url=url, html=html)))

    def test_inline_assignments_reject_assignment_limit_overflow(self):
        url = "https://careers.example.org/jobs"
        overflow = "".join(f"x{index}.id=1;" for index in range(10_001))
        html = inline_assignment_page(
            overflow,
            inline_assignment_job(
                "valid",
                123,
                "Data Analyst",
                "https://job-boards.greenhouse.io/acme/jobs/123",
            ),
        )

        self.assertIsNone(self.adapter.identify_board_from_page(Page(url=url, html=html)))

    def test_probes_and_lists_greenhouse_jobs_from_nuxt_static_payload(self):
        board_url = "https://www.example.org/careers"
        payload_url = "https://www.example.org/_nuxt/static/123/careers/payload.js"
        shell = (
            f'<link rel="preload" as="script" href="{payload_url}">'
            "<h3>Loading open roles...</h3>"
        )
        payload = (
            'window.__NUXT__=(function(){return {jobs:[{absolute_url:'
            '"https:\\u002F\\u002Fexample.org\\u002Fcareersitem?gh_jid=7351066",'
            'data_compliance:[],internal_job_id:1,metadata:[],id:7351066,'
            'requisition_id:"R-1",title:"Software Engineer, AI Platform - New Grad",'
            'company_name:"Example",first_published:"2026-01-01"}]}}());'
        )
        pages = {
            payload_url: Page(url=payload_url, html=payload, source="nuxt-payload"),
            board_url: Page(url=board_url, html=shell),
        }
        fetcher = MappingFetcher(board_url, shell, pages=pages)

        board = self.adapter.probe_board(fetcher, Page(url=board_url, html=shell))
        result = self.adapter.list_jobs(fetcher, board, JobQuery())

        self.assertEqual(board.provider, "greenhouse")
        self.assertEqual(result.trace["variant"], "nuxt_static_payload")
        self.assertEqual(result.candidates[0].title, "Software Engineer, AI Platform - New Grad")
        self.assertEqual(
            result.candidates[0].url,
            "https://example.org/careersitem?gh_jid=7351066",
        )

    def test_nuxt_probe_rejects_unverified_payload_and_cross_origin_jobs(self):
        board_url = "https://careers.example.org/careers"
        payload_url = "https://careers.example.org/_nuxt/static/123/careers/payload.js"
        shell = f'<link rel="preload" as="script" href="{payload_url}"><p>Loading open roles</p>'
        payload = (
            'absolute_url:"https://evil.example/careersitem?gh_jid=1",'
            'title:"Data Analyst",company_name:"Other"'
        )
        fetcher = MappingFetcher(
            board_url,
            shell,
            pages={payload_url: Page(url=payload_url, html=payload)},
        )

        self.assertIsNone(self.adapter.probe_board(fetcher, Page(url=board_url, html=shell)))
        self.assertIsNone(
            self.adapter.probe_board(
                fetcher,
                Page(
                    url=board_url,
                    html=f'<link rel="preload" as="script" href="https://evil.example/careers/payload.js">Loading open roles',
                ),
            )
        )


if __name__ == "__main__":
    unittest.main()
