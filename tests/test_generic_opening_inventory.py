import unittest

from job_source_agent.generic_opening_inventory import (
    collect_generic_opening_inventory,
    generic_opening_inventory_fingerprint,
    has_strong_generic_opening_inventory,
)
from job_source_agent.web import FetchError, Page


BASE_URL = "https://careers.example.com/jobs"


def job_card(title: str, path: str) -> str:
    return (
        '<article class="job-card">'
        f"<h2>{title}</h2><a href=\"{path}\">View job</a>"
        "</article>"
    )


def semantic_job_card(title: str, path: str) -> str:
    return (
        '<article class="job-card">'
        f"<h2>{title}</h2><a href=\"{path}\">View job</a>"
        "</article>"
    )


def page(url: str, body: str, *, final_url: str | None = None) -> Page:
    return Page(url=url, final_url=final_url or url, html=f"<html><body>{body}</body></html>")


def conrad_inventory(*cards: str, count: int = 2, filters: int = 2) -> str:
    controls = "".join(
        f'<select name="filter[field_{index}][]"><option>Any</option></select>'
        for index in range(filters)
    )
    return (
        f'<form id="list_filter">{controls}</form>'
        f'<section id="joboffer_table_container" class="real_table_container" '
        f'data-count="{count}" data-all-count="{count}">'
        + "".join(cards)
        + "</section>"
    )


def conrad_card(title: str, href: str, *, body: str = "") -> str:
    return (
        '<article class="joboffer_container">'
        f'<a href="{href}">{title}</a>{body}'
        "</article>"
    )


def applicant_manager_table(*rows: str, location_header: str = "Location") -> str:
    return (
        '<table id="careers_table" class="display">'
        "<thead><tr><th>Facility</th><th>Job Title</th>"
        f"<th>{location_header}</th><th>Type</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def applicant_manager_row(
    position: str,
    title: str,
    location: str,
    *,
    href: str | None = None,
    row_id: str | None = None,
    title_class: str = "pos_title",
    anchor_class: str = "pos_title",
    extra_anchor: str = "",
) -> str:
    return (
        f'<tr id="{row_id or "tr" + position}">'
        f"<td>{location}</td>"
        f'<td class="{title_class}"><a class="{anchor_class}" '
        f'href="{href or "jobs?pos=" + position}"><span>{title}</span></a>'
        f"{extra_anchor}</td><td>{location}</td><td>Full Time</td></tr>"
    )


def sveltekit_inventory(
    records: str,
    *,
    total: int,
    query: str = "SMB Account Executive",
    page_number: int = 1,
    page_limit: int = 50,
) -> str:
    return (
        '<script type="application/json">'
        '{type:"data",data:{jobs:{currentPage:['
        f"{records}],total:{total}}},initialJobsListRequest:{{"
        f'page:{page_number},pageLimit:{page_limit},query:"{query}",'
        'businessUnits:["square"]}}}</script>'
    )


class RecordingFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def fetch(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        value = self.pages.get(url)
        if isinstance(value, BaseException):
            raise value
        if value is None:
            raise FetchError(f"missing page: {url}")
        return value


class GenericOpeningInventoryTests(unittest.TestCase):
    def collect(self, initial, pages=None, *, max_pages=4, max_candidates=20):
        fetcher = RecordingFetcher(pages or {})
        result = collect_generic_opening_inventory(
            fetcher,
            initial,
            max_pages=max_pages,
            max_candidates=max_candidates,
        )
        return result, fetcher

    def test_attests_conrad_inventory_for_s5_admission(self):
        listing = page(
            "https://www.conradconsulting.com/list_vacancies.php",
            conrad_inventory(
                conrad_card("Project Manager", "/jobs/project-manager-j1001.html"),
                conrad_card("Civil Engineer", "/jobs/civil-engineer-j1002.html"),
            ),
        )

        self.assertTrue(has_strong_generic_opening_inventory(listing))

    def test_listing_fingerprint_ignores_decoration_but_tracks_identity(self):
        first = page(
            BASE_URL,
            semantic_job_card("Data Analyst", "/job/100-data-analyst")
            + "<script>window.requestId='one'</script>",
        )
        decorated = page(
            BASE_URL,
            semantic_job_card("Data Analyst", "/job/100-data-analyst")
            + "<script>window.requestId='two'</script>",
        )
        changed = page(
            BASE_URL,
            semantic_job_card("Data Analyst", "/job/200-data-analyst"),
        )

        self.assertEqual(
            generic_opening_inventory_fingerprint(first),
            generic_opening_inventory_fingerprint(decorated),
        )
        self.assertNotEqual(
            generic_opening_inventory_fingerprint(first),
            generic_opening_inventory_fingerprint(changed),
        )
        self.assertIsNone(
            generic_opening_inventory_fingerprint(
                page(BASE_URL, "<p>No public openings.</p>")
            )
        )

    def test_does_not_attest_unstructured_cards_for_s5_admission(self):
        listing = page(
            "https://careers.example.com/jobs",
            job_card("Project Manager", "/jobs/project-manager")
            + job_card("Civil Engineer", "/jobs/civil-engineer"),
        )

        self.assertFalse(has_strong_generic_opening_inventory(listing))

    def test_collects_and_attests_semantic_ssr_job_card_inventory(self):
        listing = page(
            BASE_URL,
            semantic_job_card("Senior Software Engineer", "/job/8942-senior-software-engineer")
            + semantic_job_card("Product Designer", "/job/8943-product-designer"),
        )

        result, fetcher = self.collect(listing)

        self.assertEqual(fetcher.requests, [])
        self.assertTrue(has_strong_generic_opening_inventory(listing))
        self.assertEqual(
            [(item.title, item.url, item.origin) for item in result.candidates],
            [
                (
                    "Senior Software Engineer",
                    "https://careers.example.com/job/8942-senior-software-engineer",
                    "semantic_job_card",
                ),
                (
                    "Product Designer",
                    "https://careers.example.com/job/8943-product-designer",
                    "semantic_job_card",
                ),
            ],
        )
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "single_page_unbounded")

    def test_rejects_ambiguous_or_unsafe_semantic_ssr_job_cards(self):
        cases = {
            "marketing_card": (
                '<article class="job-card"><h2>Our Culture</h2>'
                '<a href="/culture">Learn more</a></article>'
            ),
            "multiple_titles": (
                '<article class="job-card"><h2>Data Engineer</h2><h3>Remote</h3>'
                '<a href="/job/8942-data-engineer">View job</a></article>'
            ),
            "multiple_details": (
                '<article class="job-card"><h2>Data Engineer</h2>'
                '<a href="/job/8942-data-engineer">View job</a>'
                '<a href="/job/8943-data-analyst">Other job</a></article>'
            ),
            "cross_origin": (
                '<article class="job-card"><h2>Data Engineer</h2>'
                '<a href="https://jobs.example.net/job/8942-data-engineer">View job</a>'
                '</article>'
            ),
            "apply_link": (
                '<article class="job-card"><h2>Data Engineer</h2>'
                '<a href="/apply/8942-data-engineer">Apply</a></article>'
            ),
            "sign_up_link": (
                '<article class="job-card"><h2>Data Engineer</h2>'
                '<a href="/signup">Sign up</a></article>'
            ),
            "unstable_detail": (
                '<article class="job-card"><h2>Data Engineer</h2>'
                '<a href="/job/data-engineer">View job</a></article>'
            ),
            "detail_query": (
                '<article class="job-card"><h2>Data Engineer</h2>'
                '<a href="/job/8942-data-engineer?source=careers">View job</a>'
                '</article>'
            ),
            "hidden": (
                '<article class="job-card" aria-hidden="true"><h2>Data Engineer</h2>'
                '<a href="/job/8942-data-engineer">View job</a></article>'
            ),
            "script": (
                '<script><article class="job-card"><h2>Data Engineer</h2>'
                '<a href="/job/8942-data-engineer">View job</a></article></script>'
            ),
            "template": (
                '<template><article class="job-card"><h2>Data Engineer</h2>'
                '<a href="/job/8942-data-engineer">View job</a></article></template>'
            ),
        }
        for reason, body in cases.items():
            with self.subTest(reason=reason):
                listing = page(BASE_URL, body)
                result, fetcher = self.collect(listing)
                self.assertEqual(result.candidates, ())
                self.assertEqual(fetcher.requests, [])
                self.assertFalse(has_strong_generic_opening_inventory(listing))

    def test_collects_localized_card_with_repeated_detail_cta(self):
        detail = (
            "/en-us/job/2a0c3ec8-792d-11f1-a7b8-0a05e249917d-"
            "software-engineer-apollo-platform"
        )
        listing = page(
            "https://careers.example.com/en-us/jobs",
            '<article class="job-card">'
            f'<h4><a href="{detail}">Software Engineer - Apollo Platform</a></h4>'
            '<h5 class="jc-company">Apollo</h5>'
            '<a href="/talent/sign-up">Get matched</a>'
            f'<a href="{detail}">View role</a>'
            '</article>',
        )

        result, fetcher = self.collect(listing)

        self.assertEqual(fetcher.requests, [])
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            result.candidates[0].url,
            "https://careers.example.com" + detail,
        )

    def test_collects_applicant_manager_rows_from_strong_table_contract(self):
        board_url = "https://theapplicantmanager.com/careers?co=a7"
        listing = page(
            board_url,
            applicant_manager_table(
                applicant_manager_row(
                    "a513775",
                    "Registered Nurse RN - $40.55 per hour",
                    "Saginaw Senior Care & Rehab",
                ),
                applicant_manager_row(
                    "a513776", "Licensed Practical Nurse", "Midland Center"
                ),
            ),
        )

        result, fetcher = self.collect(listing)

        self.assertEqual(fetcher.requests, [])
        self.assertTrue(has_strong_generic_opening_inventory(listing))
        self.assertEqual(
            [(item.title, item.url, item.origin) for item in result.candidates],
            [
                (
                    "Registered Nurse RN - $40.55 per hour",
                    "https://theapplicantmanager.com/jobs?pos=a513775",
                    "applicant_manager_table",
                ),
                (
                    "Licensed Practical Nurse",
                    "https://theapplicantmanager.com/jobs?pos=a513776",
                    "applicant_manager_table",
                ),
            ],
        )
        self.assertEqual(
            [item.location for item in result.candidates],
            ["Saginaw Senior Care & Rehab", "Midland Center"],
        )
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "single_page_unbounded")

    def test_deduplicates_repeated_applicant_manager_rows(self):
        row = applicant_manager_row("a513775", "Data Analyst", "Saginaw")
        listing = page(
            "https://theapplicantmanager.com/careers?co=a7",
            applicant_manager_table(row, row),
        )

        result, _fetcher = self.collect(listing)

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, "Data Analyst")

    def test_rejects_applicant_manager_table_lookalikes(self):
        valid_row = applicant_manager_row("a513775", "Data Analyst", "Saginaw")
        cases = {
            "wrong_host": (
                "https://careers.example.com/careers?co=a7",
                applicant_manager_table(valid_row),
            ),
            "ambiguous_board_query": (
                "https://theapplicantmanager.com/careers?co=a7&ref=public",
                applicant_manager_table(valid_row),
            ),
            "wrong_header": (
                "https://theapplicantmanager.com/careers?co=a7",
                applicant_manager_table(valid_row, location_header="Office"),
            ),
            "missing_location": (
                "https://theapplicantmanager.com/careers?co=a7",
                applicant_manager_table(
                    applicant_manager_row("a513775", "Data Analyst", "")
                ),
            ),
            "mismatched_row_id": (
                "https://theapplicantmanager.com/careers?co=a7",
                applicant_manager_table(
                    applicant_manager_row(
                        "a513775", "Data Analyst", "Saginaw", row_id="trA999999"
                    )
                ),
            ),
            "extra_detail_query": (
                "https://theapplicantmanager.com/careers?co=a7",
                applicant_manager_table(
                    applicant_manager_row(
                        "a513775",
                        "Data Analyst",
                        "Saginaw",
                        href="jobs?pos=a513775&token=public",
                    )
                ),
            ),
            "cross_host_detail": (
                "https://theapplicantmanager.com/careers?co=a7",
                applicant_manager_table(
                    applicant_manager_row(
                        "a513775",
                        "Data Analyst",
                        "Saginaw",
                        href="https://example.com/jobs?pos=a513775",
                    )
                ),
            ),
            "missing_title_class": (
                "https://theapplicantmanager.com/careers?co=a7",
                applicant_manager_table(
                    applicant_manager_row(
                        "a513775",
                        "Data Analyst",
                        "Saginaw",
                        title_class="marketing",
                    )
                ),
            ),
            "missing_anchor_class": (
                "https://theapplicantmanager.com/careers?co=a7",
                applicant_manager_table(
                    applicant_manager_row(
                        "a513775",
                        "Data Analyst",
                        "Saginaw",
                        anchor_class="marketing",
                    )
                ),
            ),
            "multiple_title_links": (
                "https://theapplicantmanager.com/careers?co=a7",
                applicant_manager_table(
                    applicant_manager_row(
                        "a513775",
                        "Data Analyst",
                        "Saginaw",
                        extra_anchor='<a href="jobs?pos=a513776">Apply</a>',
                    )
                ),
            ),
        }
        for reason, (board_url, body) in cases.items():
            with self.subTest(reason=reason):
                listing = page(board_url, body)
                result, fetcher = self.collect(listing)
                self.assertEqual(result.candidates, ())
                self.assertEqual(fetcher.requests, [])
                self.assertFalse(has_strong_generic_opening_inventory(listing))

    def test_collects_target_from_second_page(self):
        second_url = f"{BASE_URL}?page=2"
        initial = page(
            BASE_URL,
            job_card("Sales Manager", "/jobs/sales-manager")
            + f'<a href="{second_url}">Next page</a>',
        )
        second = page(second_url, job_card("AI Engineer", "/jobs/ai-engineer"))

        result, fetcher = self.collect(initial, {second_url: second})

        self.assertEqual([item.title for item in result.candidates], ["Sales Manager", "AI Engineer"])
        self.assertEqual(fetcher.requests, [(second_url, None, None)])
        self.assertEqual(result.pages_fetched, 2)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.stop_reason, "complete")

    def test_collects_complete_title_filtered_sveltekit_ssr_inventory(self):
        listing = page(
            "https://block.example/careers/jobs?businessUnits%5B%5D=square&"
            "query=SMB+Account+Executive",
            sveltekit_inventory(
                '{id:5282973008,internalId:4498473008,requisitionId:"1741",'
                'title:"SMB Account Executive",bu:"square",employeeType:"Regular",'
                'jobFunction:"Sales & Account Management",isRemote:true,'
                'location:"Bay Area, CA, US",publicationDate:null},'
                '{id:5287754008,title:"SMB Account Executive - Canada",'
                'location:"Toronto, Ontario, Canada"}',
                total=2,
            ),
        )

        result, fetcher = self.collect(listing)

        self.assertEqual(fetcher.requests, [])
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.stop_reason, "complete")
        self.assertEqual(
            [
                (item.title, item.url, item.location, item.origin)
                for item in result.candidates
            ],
            [
                (
                    "SMB Account Executive",
                    "https://block.example/careers/jobs/5282973008",
                    "Bay Area, CA, US",
                    "sveltekit_ssr_inventory",
                ),
                (
                    "SMB Account Executive - Canada",
                    "https://block.example/careers/jobs/5287754008",
                    "Toronto, Ontario, Canada",
                    "sveltekit_ssr_inventory",
                ),
            ],
        )

    def test_attests_complete_empty_sveltekit_title_filter(self):
        listing = page(
            "https://block.example/careers/jobs?query=Closed+Role",
            sveltekit_inventory("", total=0, query="Closed Role"),
        )

        result, _fetcher = self.collect(listing)

        self.assertEqual(result.candidates, ())
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.stop_reason, "complete")

    def test_keeps_paginated_sveltekit_inventory_incomplete(self):
        records = ",".join(
            f'{{id:{index},title:"Data Analyst {index}"}}'
            for index in range(1, 3)
        )
        listing = page(
            "https://block.example/careers/jobs?query=Data+Analyst",
            sveltekit_inventory(
                records,
                total=3,
                query="Data Analyst",
                page_limit=2,
            ),
        )

        result, _fetcher = self.collect(listing)

        self.assertEqual(len(result.candidates), 2)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "single_page_unbounded")

    def test_rejects_unbound_or_malformed_sveltekit_inventory(self):
        valid_record = '{id:5282973008,title:"SMB Account Executive"}'
        cases = {
            "missing_filter": (
                "https://block.example/careers/jobs",
                sveltekit_inventory(valid_record, total=1),
            ),
            "query_echo_mismatch": (
                "https://block.example/careers/jobs?query=Other+Role",
                sveltekit_inventory(valid_record, total=1),
            ),
            "wrong_route": (
                "https://block.example/jobs?query=SMB+Account+Executive",
                sveltekit_inventory(valid_record, total=1),
            ),
            "duplicate_id": (
                "https://block.example/careers/jobs?query=SMB+Account+Executive",
                sveltekit_inventory(f"{valid_record},{valid_record}", total=2),
            ),
            "executable_value": (
                "https://block.example/careers/jobs?query=SMB+Account+Executive",
                sveltekit_inventory(
                    '{id:5282973008,title:alert("unsafe")}',
                    total=1,
                ),
            ),
            "unknown_field": (
                "https://block.example/careers/jobs?query=SMB+Account+Executive",
                sveltekit_inventory(
                    '{id:5282973008,title:"SMB Account Executive",url:'
                    '"https://evil.example/job/1"}',
                    total=1,
                ),
            ),
        }
        for reason, (url, body) in cases.items():
            with self.subTest(reason=reason):
                result, fetcher = self.collect(page(url, body))
                self.assertEqual(result.candidates, ())
                self.assertEqual(fetcher.requests, [])
                self.assertFalse(result.inventory_complete)

    def test_collects_server_rendered_job_list_cards_with_nested_titles(self):
        listing = page(
            "https://careers.example.com/jobs?keywords=Financial+Analyst",
            (
                '<li class="job-list__job"><a class="job-list__inner" '
                'href="https://jobs.example.com/job/Portland-Financial-Analyst/123">'
                '<section><h3 class="job-list__title">Financial Analyst</h3>'
                '<p class="job-list__facts">Portland, OR</p></section>'
                "</a></li>"
            ),
        )

        result, _fetcher = self.collect(listing)

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].title, "Financial Analyst")
        self.assertEqual(
            result.candidates[0].url,
            "https://jobs.example.com/job/Portland-Financial-Analyst/123",
        )

    def test_follows_rel_next_without_visible_label(self):
        second_url = f"{BASE_URL}?page=2"
        initial = page(BASE_URL, f'<link rel="next" href="{second_url}">')

        result, fetcher = self.collect(initial, {second_url: page(second_url, "")})

        self.assertEqual(fetcher.requests[0][0], second_url)
        self.assertTrue(result.inventory_complete)

    def test_follows_ssr_page_route_despite_large_hydration_script(self):
        second_url = f"{BASE_URL}/page-2/"
        hydration = "x" * 2_100_000
        initial = page(
            BASE_URL,
            f'<script>window.__DATA__ = "{hydration}";</script>'
            f'<a href="{second_url}">2</a>',
        )

        result, fetcher = self.collect(initial, {second_url: page(second_url, "")})

        self.assertEqual(fetcher.requests, [(second_url, None, None)])
        self.assertEqual(result.pages_fetched, 2)
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.stop_reason, "complete")

    def test_ignores_pagination_links_inside_non_navigation_content(self):
        fake_url = f"{BASE_URL}?page=2"
        bodies = {
            "script": f'<script><a href="{fake_url}">Next</a></script>',
            "style": f'<style><a href="{fake_url}">Next</a></style>',
            "template": f'<template><a href="{fake_url}">Next</a></template>',
            "noscript": f'<noscript><a href="{fake_url}">Next</a></noscript>',
        }
        for container, body in bodies.items():
            with self.subTest(container=container):
                result, fetcher = self.collect(page(BASE_URL, body))
                self.assertEqual(fetcher.requests, [])
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.stop_reason, "single_page_unbounded")

    def test_rejects_skipped_or_nonconsecutive_numbered_pages(self):
        cases = {
            "query_skip": f"{BASE_URL}?page=3",
            "route_skip": f"{BASE_URL}/page-3/",
            "duplicate_query": f"{BASE_URL}?page=2&page=3",
            "malformed_query": f"{BASE_URL}?page=two",
        }
        for reason, target in cases.items():
            with self.subTest(reason=reason):
                initial = page(BASE_URL, f'<a rel="next" href="{target}">Next</a>')
                result, fetcher = self.collect(initial)
                self.assertEqual(fetcher.requests, [])
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.stop_reason, "pagination_parse_limit")

    def test_requires_consecutive_page_route_after_first_fetch(self):
        second_url = f"{BASE_URL}/page-2/"
        fourth_url = f"{BASE_URL}/page-4/"
        initial = page(BASE_URL, f'<a href="{second_url}">2</a>')
        second = page(second_url, f'<a rel="next" href="{fourth_url}">Next</a>')

        result, fetcher = self.collect(initial, {second_url: second})

        self.assertEqual(fetcher.requests, [(second_url, None, None)])
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "pagination_parse_limit")

    def test_rejects_redirect_that_breaks_page_continuity(self):
        second_url = f"{BASE_URL}/page-2/"
        third_url = f"{BASE_URL}/page-3/"
        initial = page(BASE_URL, f'<a href="{second_url}">2</a>')
        redirected = page(second_url, "", final_url=third_url)

        result, fetcher = self.collect(initial, {second_url: redirected})

        self.assertEqual(fetcher.requests, [(second_url, None, None)])
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "unsafe_response_url")

    def test_rejects_oversized_or_malformed_pagination_evidence(self):
        cases = {
            "oversized_href": f'<a href="/{"x" * 8_193}">Next</a>',
            "oversized_visible_text": f'<p>{"x" * 500_001}</p>',
            "unclosed_next_anchor": '<a href="/jobs?page=2">Next',
            "ambiguous_next_links": (
                '<a href="/jobs?page=2">Next</a>'
                '<a href="/jobs?offset=20">Load More</a>'
            ),
        }
        for reason, body in cases.items():
            with self.subTest(reason=reason):
                result, fetcher = self.collect(page(BASE_URL, body))
                self.assertEqual(fetcher.requests, [])
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.stop_reason, "pagination_parse_limit")

    def test_rejects_unsafe_next_urls(self):
        unsafe_urls = {
            "cross_origin": "https://other.example.net/jobs?page=2",
            "private": "https://127.0.0.1/jobs?page=2",
            "credentials": "https://user:password@careers.example.com/jobs?page=2",
            "port": "https://careers.example.com:8443/jobs?page=2",
            "sensitive_query": "https://careers.example.com/jobs?access_token=secret",
        }
        for reason, unsafe_url in unsafe_urls.items():
            with self.subTest(reason=reason):
                initial = page(BASE_URL, f'<a href="{unsafe_url}">Next</a>')
                result, fetcher = self.collect(initial)
                self.assertEqual(fetcher.requests, [])
                self.assertFalse(result.inventory_complete)
                self.assertEqual(result.stop_reason, "unsafe_next_url")

    def test_detects_cycle(self):
        initial = page(BASE_URL, f'<a href="{BASE_URL}">Load more</a>')

        result, fetcher = self.collect(initial)

        self.assertEqual(fetcher.requests, [])
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "pagination_cycle")

    def test_stops_at_page_cap(self):
        second_url = f"{BASE_URL}?page=2"
        initial = page(BASE_URL, f'<a href="{second_url}">Next</a>')

        result, fetcher = self.collect(initial, max_pages=1)

        self.assertEqual(fetcher.requests, [])
        self.assertEqual(result.pages_fetched, 1)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "page_cap_reached")

    def test_returns_candidates_when_later_fetch_fails(self):
        second_url = f"{BASE_URL}?page=2"
        initial = page(
            BASE_URL,
            job_card("AI Engineer", "/jobs/ai-engineer")
            + f'<a href="{second_url}">Next</a>',
        )

        result, _fetcher = self.collect(initial, {second_url: FetchError("timeout")})

        self.assertEqual([item.title for item in result.candidates], ["AI Engineer"])
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "fetch_error")
        self.assertEqual(result.trace[-1].url, second_url)

    def test_deduplicates_candidates_across_pages(self):
        second_url = f"{BASE_URL}?page=2"
        duplicate = job_card("AI Engineer", "/jobs/ai-engineer")
        initial = page(BASE_URL, duplicate + f'<a href="{second_url}">Next</a>')
        second = page(second_url, duplicate + job_card("Designer", "/jobs/designer"))

        result, _fetcher = self.collect(initial, {second_url: second})

        self.assertEqual([item.title for item in result.candidates], ["AI Engineer", "Designer"])

    def test_single_page_without_pagination_evidence_is_not_declared_complete(self):
        result, fetcher = self.collect(
            page(BASE_URL, job_card("AI Engineer", "/jobs/ai-engineer"))
        )

        self.assertEqual(fetcher.requests, [])
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "single_page_unbounded")
        self.assertEqual(result.trace[-1].candidate_count, 1)
        self.assertNotIn("<html", repr(result.trace))

    def test_collects_career_single_heading_and_apply_link(self):
        initial = page(
            BASE_URL,
            """
            <div class="career-single">
              <div>
                <h4>DevOps <em>Engineer</em></h4>
                <p>Hybrid / San Diego, CA</p>
              </div>
              <span class="button"><a href="/career/devops-engineer/">Apply</a></span>
            </div>
            """,
        )

        result, _fetcher = self.collect(initial)

        self.assertEqual(
            [(item.title, item.url, item.origin) for item in result.candidates],
            [
                (
                    "DevOps Engineer",
                    "https://careers.example.com/career/devops-engineer/",
                    "first_party_job_card",
                )
            ],
        )
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "single_page_unbounded")

    def test_collects_grouped_position_anchors_without_nested_location(self):
        initial = page(
            BASE_URL,
            """
            <section class="wmc-position-container">
              <h3>R&amp;D</h3>
              <ul>
                <li>
                  <a href="/jobs/devops-engineer/">
                    DevOps Engineer <span>Detroit, MI</span>
                  </a>
                </li>
                <li>
                  <a href="/jobs/software-engineer/">
                    Software Engineer <span>New York City</span>
                  </a>
                </li>
              </ul>
            </section>
            <div class="position-container">
              <a href="/jobs/product-manager/">Product Manager <span>Remote</span></a>
            </div>
            """,
        )

        result, _fetcher = self.collect(initial)

        self.assertEqual(
            [item.title for item in result.candidates],
            ["DevOps Engineer", "Software Engineer", "Product Manager"],
        )
        self.assertNotIn("Detroit", result.candidates[0].title)
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "single_page_unbounded")

    def test_rejects_first_party_card_lookalikes_and_blocked_content(self):
        initial = page(
            BASE_URL,
            """
            <div class="career-singleton">
              <h4>Singleton Engineer</h4><a href="/jobs/singleton-engineer/">Apply</a>
            </div>
            <div class="career-single position-container">
              <h4>Ambiguous Engineer</h4><a href="/jobs/ambiguous-engineer/">Apply</a>
            </div>
            <nav>
              <div class="career-single">
                <h4>Navigation Engineer</h4><a href="/jobs/navigation-engineer/">Apply</a>
              </div>
            </nav>
            <div class="wmc-position-container" hidden>
              <a href="/jobs/hidden-engineer/">Hidden Engineer</a>
            </div>
            <div class="position-container" style="display: none">
              <a href="/jobs/invisible-engineer/">Invisible Engineer</a>
            </div>
            """,
        )

        result, _fetcher = self.collect(initial)

        self.assertEqual(result.candidates, ())

    def test_rejects_ambiguous_or_unvalidated_first_party_cards(self):
        cases = {
            "multiple_titles": """
                <div class="career-single">
                  <h4>DevOps Engineer</h4><h5>San Diego</h5>
                  <a href="/career/devops-engineer/">Apply</a>
                </div>
            """,
            "multiple_links": """
                <div class="career-single">
                  <h4>DevOps Engineer</h4>
                  <a href="/career/devops-engineer/">Apply</a>
                  <a href="/jobs/devops-engineer/">Details</a>
                </div>
            """,
            "cross_origin": """
                <div class="position-container">
                  <a href="https://other.example/jobs/devops-engineer/">DevOps Engineer</a>
                </div>
            """,
            "listing_url": """
                <div class="wmc-position-container">
                  <a href="/jobs/">DevOps Engineer</a>
                </div>
            """,
            "nested_text_only": """
                <div class="position-container">
                  <a href="/jobs/devops-engineer/"><span>DevOps Engineer</span></a>
                </div>
            """,
        }
        for reason, body in cases.items():
            with self.subTest(reason=reason):
                result, _fetcher = self.collect(page(BASE_URL, body))
                self.assertEqual(result.candidates, ())

    def test_collects_conrad_style_listing_root_from_strong_structure(self):
        initial = page(
            BASE_URL,
            conrad_inventory(
                conrad_card("AI Engineer", "/AI-Engineer-eng-j1341.html"),
                conrad_card("Product Manager", "/Product-Manager-eng-j1342.html"),
            ),
        )

        result, fetcher = self.collect(initial)

        self.assertEqual(
            [(item.title, item.url, item.origin) for item in result.candidates],
            [
                (
                    "AI Engineer",
                    "https://careers.example.com/AI-Engineer-eng-j1341.html",
                    "first_party_job_card",
                ),
                (
                    "Product Manager",
                    "https://careers.example.com/Product-Manager-eng-j1342.html",
                    "first_party_job_card",
                ),
            ],
        )
        self.assertEqual(fetcher.requests, [])
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "single_page_unbounded")

    def test_uses_redirected_listing_root_without_category_fanout(self):
        category_url = f"{BASE_URL}/engineering"
        initial = page(
            category_url,
            conrad_inventory(
                conrad_card("AI Engineer", "/AI-Engineer-j1341.html"),
                conrad_card("Data Engineer", "/Data-Engineer-j1342.html"),
            ),
            final_url=BASE_URL,
        )

        result, fetcher = self.collect(initial)

        self.assertEqual(
            [item.title for item in result.candidates],
            ["AI Engineer", "Data Engineer"],
        )
        self.assertEqual(fetcher.requests, [])
        self.assertEqual(result.trace[0].url, BASE_URL)
        self.assertFalse(result.inventory_complete)

    def test_conrad_style_inventory_preserves_candidate_cap_semantics(self):
        initial = page(
            BASE_URL,
            conrad_inventory(
                conrad_card("AI Engineer", "/AI-Engineer-j1341.html"),
                conrad_card("Data Engineer", "/Data-Engineer-j1342.html"),
            ),
        )

        result, _fetcher = self.collect(initial, max_candidates=1)

        self.assertEqual([item.title for item in result.candidates], ["AI Engineer"])
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "candidate_cap_reached")

    def test_rejects_weak_conrad_style_inventory_signals(self):
        cases = {
            "one_filter": conrad_inventory(
                conrad_card("AI Engineer", "/AI-Engineer-j1341.html"),
                conrad_card("Data Engineer", "/Data-Engineer-j1342.html"),
                filters=1,
            ),
            "zero_vacancies": conrad_inventory(
                conrad_card("AI Engineer", "/AI-Engineer-j1341.html"),
                conrad_card("Data Engineer", "/Data-Engineer-j1342.html"),
                count=0,
            ),
            "one_opening": conrad_inventory(
                conrad_card("AI Engineer", "/AI-Engineer-j1341.html"),
                count=1,
            ),
            "unstable_schema": conrad_inventory(
                conrad_card("AI Engineer", "/AI-Engineer-eng-j1341.html"),
                conrad_card("Data Engineer", "/teams/Data-Engineer-eng-j1342.html"),
            ),
            "marketing_links": conrad_inventory(
                conrad_card("Our Culture", "/culture.html"),
                conrad_card("Meet the Team", "/team.html"),
            ),
            "category_cards": conrad_inventory(
                conrad_card("Engineering", "/categories/engineering-jobs.html"),
                conrad_card("Sales", "/categories/sales-jobs.html"),
            ),
            "cross_origin_details": conrad_inventory(
                conrad_card(
                    "AI Engineer",
                    "https://other.example/AI-Engineer-j1341.html",
                ),
                conrad_card(
                    "Data Engineer",
                    "https://other.example/Data-Engineer-j1342.html",
                ),
            ),
            "application_forms": conrad_inventory(
                conrad_card(
                    "AI Engineer",
                    "/AI-Engineer-j1341.html",
                    body="<form><input name='email'></form>",
                ),
                conrad_card(
                    "Data Engineer",
                    "/Data-Engineer-j1342.html",
                    body="<form><input name='email'></form>",
                ),
            ),
        }
        for reason, body in cases.items():
            with self.subTest(reason=reason):
                result, fetcher = self.collect(page(BASE_URL, body))
                self.assertEqual(result.candidates, ())
                self.assertEqual(fetcher.requests, [])
                self.assertFalse(result.inventory_complete)

    def test_candidate_cap_prevents_completeness_claim(self):
        initial = page(
            BASE_URL,
            job_card("AI Engineer", "/jobs/ai-engineer")
            + job_card("Designer", "/jobs/designer"),
        )

        result, _fetcher = self.collect(initial, max_candidates=1)

        self.assertEqual([item.title for item in result.candidates], ["AI Engineer"])
        self.assertFalse(result.inventory_complete)
        self.assertEqual(result.stop_reason, "candidate_cap_reached")


if __name__ == "__main__":
    unittest.main()
