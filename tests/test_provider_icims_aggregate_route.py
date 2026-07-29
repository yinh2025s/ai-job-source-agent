from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from job_source_agent.providers.base import JobCandidate, JobQuery
from job_source_agent.providers.icims import ICIMSAdapter
from job_source_agent.web import Page


class InlineFetcher:
    def __init__(self, html: str, *, final_url: str | None = None):
        self.html = html
        self.final_url = final_url
        self.requested_urls: list[str] = []

    def fetch(self, url, data=None, headers=None):
        self.requested_urls.append(url)
        return Page(
            url=url,
            final_url=self.final_url or url,
            html=self.html,
            source="icims-aggregate-inline",
        )


def runtime_marker(customer: str) -> str:
    return (
        '<link rel="stylesheet" href="https://cdn02.icims.com/platform_183/'
        f"{customer}/icims2/servlet/icims2?"
        'module=AppInert&amp;action=renderDynamicPortalCss">'
    )


def job_card(
    *,
    href: str,
    title: str,
    location: str = "US-MN-Elk River",
    extra_links: tuple[tuple[str, str], ...] = (),
    header_location: bool = False,
) -> str:
    links = (
        f'<a class="iCIMS_Anchor" href="{href}" title="{title}">'
        f"<h3>{title}</h3></a>"
    )
    links += "".join(
        f'<a href="{extra_href}" title="{extra_title}">{extra_title}</a>'
        for extra_href, extra_title in extra_links
    )
    location_html = (
        '<dt class="iCIMS_JobHeaderField">Campus Location</dt>'
        f'<dd class="iCIMS_JobHeaderData"><span>{location}</span></dd>'
        if header_location
        else (
            '<span class="sr-only field-label">Job Locations</span>'
            f"<span>{location}</span>"
        )
    )
    return (
        '<li class="iCIMS_JobCardItem">'
        f"{location_html}"
        f"{links}"
        "</li>"
    )


def aggregate_html(
    *,
    customer: str | None,
    cards: tuple[str, ...],
    extra_markers: tuple[str, ...] = (),
    outside: str = "",
) -> str:
    markers = "" if customer is None else runtime_marker(customer)
    markers += "".join(runtime_marker(item) for item in extra_markers)
    return (
        "<html><head>"
        f"{markers}"
        "</head><body>"
        f"{outside}<ul class=\"iCIMS_JobsTable\">{''.join(cards)}</ul>"
        "</body></html>"
    )


class ICIMSAggregateRouteTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ICIMSAdapter()

    def _list(
        self,
        *,
        source_host: str,
        html: str,
        title: str = "Engineer",
        final_url: str | None = None,
    ):
        board = self.adapter.identify_board(
            f"https://{source_host}/jobs/search"
        )
        self.assertIsNotNone(board)
        fetcher = InlineFetcher(html, final_url=final_url)
        result = self.adapter.list_jobs(
            fetcher,
            board,
            JobQuery(title=title),
        )
        return fetcher, result

    def test_public_aggregate_shapes_emit_typed_unverified_child_routes(self):
        controls = (
            {
                "name": "cretex",
                "source": "cretex-companies.icims.com",
                "customer": "cretex.icims.com",
                "child": "careers-cretex.icims.com",
                "opening_id": "5219",
                "slug": "it-cyber-security-risk-analyst",
                "title": "IT Cyber Security Risk Analyst",
                "location": "US-MN-Elk River",
                "normalized_location": "Elk River, MN, United States",
                "hub": "15",
            },
            {
                "name": "emory",
                "source": "ehccareers-emory.icims.com",
                "customer": "emory.icims.com",
                "child": "clinical-emory.icims.com",
                "opening_id": "170893",
                "slug": "medical-assistant",
                "title": "Medical Assistant",
                "location": "Johns Creek, GA, 30097",
                "normalized_location": "Johns Creek, GA, 30097",
                "hub": "14",
                "header_location": True,
            },
            {
                "name": "hochunk",
                "source": "hub-hochunk.icims.com",
                "customer": "ho-chunk.icims.com",
                "child": "careers-allnativegroup.icims.com",
                "opening_id": "10483",
                "slug": "cable-foreman",
                "title": "Cable Foreman",
                "location": "US-DC-Washington",
                "normalized_location": "Washington, DC, United States",
                "hub": "26",
                "header_location": False,
            },
        )
        for control in controls:
            with self.subTest(control=control["name"]):
                opening_url = (
                    f"https://{control['child']}/jobs/{control['opening_id']}/"
                    f"{control['slug']}/job"
                )
                href = (
                    f"{opening_url}?hub={control['hub']}&amp;in_iframe=1"
                )
                html = aggregate_html(
                    customer=control["customer"],
                    cards=(
                        job_card(
                            href=href,
                            title=f"{control['opening_id']} - {control['title']}",
                            location=control["location"],
                            header_location=control.get(
                                "header_location",
                                False,
                            ),
                        ),
                    ),
                )

                fetcher, result = self._list(
                    source_host=control["source"],
                    html=html,
                    title=control["title"],
                )

                self.assertEqual(len(fetcher.requested_urls), 1)
                search_url = fetcher.requested_urls[0]
                self.assertEqual(
                    parse_qs(urlparse(search_url).query)["searchKeyword"],
                    [control["title"]],
                )
                self.assertEqual(len(result.candidates), 1)
                candidate = result.candidates[0]
                self.assertEqual(candidate.title, control["title"])
                self.assertEqual(candidate.location, control["normalized_location"])
                self.assertEqual(candidate.url, opening_url)
                self.assertEqual(result.trace["aggregate_route_count"], 1)

                route = candidate.route_evidence
                self.assertIsNotNone(route)
                self.assertEqual(route.provider, "icims")
                self.assertEqual(route.source_tenant, control["source"])
                self.assertEqual(
                    route.source_canonical_board_url,
                    f"https://{control['source']}/jobs/search",
                )
                self.assertEqual(route.target_tenant, control["child"])
                self.assertEqual(
                    route.target_canonical_board_url,
                    f"https://{control['child']}/jobs/search",
                )
                self.assertEqual(route.canonical_opening_url, opening_url)
                self.assertEqual(route.opening_id, control["opening_id"])
                self.assertEqual(route.source_response_url, search_url)
                self.assertEqual(
                    route.source_customer_identity,
                    control["customer"],
                )
                self.assertEqual(route.route_identity, f"hub:{control['hub']}")
                self.assertEqual(
                    route.extraction_method,
                    "icims_aggregate_job_card",
                )
                self.assertIsNone(route.target_customer_identity)
                self.assertIsNone(route.detail_evidence_url)
                self.assertFalse(route.detail_verified)

    def test_ordinary_same_host_candidate_remains_unchanged(self):
        href = "/jobs/1234/data-engineer/job"
        html = aggregate_html(
            customer=None,
            cards=(
                job_card(
                    href=href,
                    title="1234 - Data Engineer",
                    location="US-NY-New York",
                ),
            ),
        )

        _, result = self._list(
            source_host="careers-acme.icims.com",
            html=html,
            title="Data Engineer",
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            result.candidates[0].url,
            "https://careers-acme.icims.com/jobs/1234/data-engineer/job",
        )
        self.assertIsNone(result.candidates[0].route_evidence)
        self.assertEqual(result.trace["aggregate_route_count"], 0)

    def test_source_response_cross_tenant_redirect_fails_closed(self):
        html = aggregate_html(
            customer="emory.icims.com",
            cards=(
                job_card(
                    href=(
                        "https://clinical-emory.icims.com/jobs/170893/"
                        "medical-assistant/job?hub=14&amp;in_iframe=1"
                    ),
                    title="170893 - Medical Assistant",
                ),
            ),
        )

        _, result = self._list(
            source_host="ehccareers-emory.icims.com",
            html=html,
            title="Medical Assistant",
            final_url=(
                "https://hub-hochunk.icims.com/jobs/search?"
                "ss=1&searchKeyword=Medical+Assistant&in_iframe=1"
            ),
        )

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.reason_code, "PROVIDER_VARIANT_UNSUPPORTED")

    def test_cross_customer_child_is_only_an_unverified_claim(self):
        html = aggregate_html(
            customer="emory.icims.com",
            cards=(
                job_card(
                    href=(
                        "https://careers-allnativegroup.icims.com/jobs/10483/"
                        "cable-foreman/job?hub=14&amp;in_iframe=1"
                    ),
                    title="10483 - Cable Foreman",
                    location="US-DC-Washington",
                ),
            ),
        )

        _, result = self._list(
            source_host="ehccareers-emory.icims.com",
            html=html,
            title="Cable Foreman",
        )

        self.assertEqual(len(result.candidates), 1)
        route = result.candidates[0].route_evidence
        self.assertEqual(route.source_customer_identity, "emory.icims.com")
        self.assertEqual(
            route.target_tenant,
            "careers-allnativegroup.icims.com",
        )
        self.assertFalse(route.detail_verified)
        self.assertIsNone(route.target_customer_identity)

    def test_missing_duplicate_or_conflicting_customer_marker_fails_closed(self):
        card = job_card(
            href=(
                "https://careers-cretex.icims.com/jobs/5219/"
                "it-cyber-security-risk-analyst/job?hub=15&amp;in_iframe=1"
            ),
            title="5219 - IT Cyber Security Risk Analyst",
        )
        cases = (
            ("missing", None, ()),
            ("duplicate", "cretex.icims.com", ("cretex.icims.com",)),
            ("conflicting", "cretex.icims.com", ("emory.icims.com",)),
        )
        for name, customer, extras in cases:
            with self.subTest(case=name):
                _, result = self._list(
                    source_host="cretex-companies.icims.com",
                    html=aggregate_html(
                        customer=customer,
                        cards=(card,),
                        extra_markers=extras,
                    ),
                    title="IT Cyber Security Risk Analyst",
                )
                self.assertEqual(result.candidates, [])
                self.assertEqual(result.trace["aggregate_route_count"], 0)

    def test_ambiguous_or_unsafe_hub_query_fails_closed(self):
        query_cases = (
            "",
            "?hub=0",
            "?hub=abc",
            "?hub=15&hub=15",
            "?hub=15&hub=26",
            "?hub=15&token=secret",
            "?hub=15&in_iframe=0",
            "?hub=15&in_iframe=1&in_iframe=1",
            "?hub=15#fragment",
        )
        for query in query_cases:
            with self.subTest(query=query):
                href = (
                    "https://careers-cretex.icims.com/jobs/5219/"
                    f"it-cyber-security-risk-analyst/job{query}"
                )
                _, result = self._list(
                    source_host="cretex-companies.icims.com",
                    html=aggregate_html(
                        customer="cretex.icims.com",
                        cards=(
                            job_card(
                                href=href.replace("&", "&amp;"),
                                title="5219 - IT Cyber Security Risk Analyst",
                            ),
                        ),
                    ),
                    title="IT Cyber Security Risk Analyst",
                )
                self.assertEqual(result.candidates, [])

    def test_child_url_must_be_safe_exact_detail_route(self):
        unsafe_urls = (
            "http://careers-cretex.icims.com/jobs/5219/role/job?hub=15",
            "https://user@careers-cretex.icims.com/jobs/5219/role/job?hub=15",
            "https://careers-cretex.icims.com:8443/jobs/5219/role/job?hub=15",
            "https://careers-cretex.icims.com/jobs/0/role/job?hub=15",
            "https://careers-cretex.icims.com/jobs/not-numeric/role/job?hub=15",
            "https://careers-cretex.icims.com/jobs/5219/job?hub=15",
            "https://careers-cretex.icims.com/jobs/5219/role/job/apply?hub=15",
            "https://careers-cretex.icims.com/jobs/5219/role/apply?hub=15",
            "https://careers-cretex.icims.com/jobs/5219/role%2fapply/job?hub=15",
            "https://careers-cretex.icims.com/jobs/5219/role%ZZ/job?hub=15",
            "https://careers-cretex.icims.com/jobs/profile?hub=15",
        )
        for href in unsafe_urls:
            with self.subTest(href=href):
                _, result = self._list(
                    source_host="cretex-companies.icims.com",
                    html=aggregate_html(
                        customer="cretex.icims.com",
                        cards=(
                            job_card(
                                href=href,
                                title="5219 - IT Cyber Security Risk Analyst",
                            ),
                        ),
                    ),
                    title="IT Cyber Security Risk Analyst",
                )
                self.assertEqual(result.candidates, [])

    def test_child_anchor_title_and_location_must_share_one_unambiguous_card(self):
        child = (
            "https://careers-cretex.icims.com/jobs/5219/"
            "it-cyber-security-risk-analyst/job?hub=15&amp;in_iframe=1"
        )
        cases = (
            (
                "outside-card",
                aggregate_html(
                    customer="cretex.icims.com",
                    cards=(
                        '<li class="iCIMS_JobCardItem">'
                        '<span class="field-label">Job Locations</span>'
                        "<span>US-MN-Elk River</span>"
                        "</li>",
                    ),
                    outside=f'<a href="{child}">IT Cyber Security Risk Analyst</a>',
                ),
            ),
            (
                "location-outside-card",
                aggregate_html(
                    customer="cretex.icims.com",
                    cards=(
                        '<li class="iCIMS_JobCardItem">'
                        f'<a href="{child}" '
                        'title="5219 - IT Cyber Security Risk Analyst"></a>'
                        "</li>",
                    ),
                    outside=(
                        '<span class="field-label">Job Locations</span>'
                        "<span>US-MN-Elk River</span>"
                    ),
                ),
            ),
            (
                "multiple-links-in-card",
                aggregate_html(
                    customer="cretex.icims.com",
                    cards=(
                        job_card(
                            href=child,
                            title="5219 - IT Cyber Security Risk Analyst",
                            extra_links=(
                                (
                                    "https://qts-cretex.icims.com/jobs/6000/"
                                    "other-role/job?hub=15&amp;in_iframe=1",
                                    "6000 - Other Role",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            (
                "title-id-conflict",
                aggregate_html(
                    customer="cretex.icims.com",
                    cards=(
                        job_card(
                            href=child,
                            title="9999 - IT Cyber Security Risk Analyst",
                        ),
                    ),
                ),
            ),
        )
        for name, html in cases:
            with self.subTest(case=name):
                _, result = self._list(
                    source_host="cretex-companies.icims.com",
                    html=html,
                    title="IT Cyber Security Risk Analyst",
                )
                self.assertEqual(result.candidates, [])

    def test_duplicate_card_or_opening_id_fails_closed(self):
        first = job_card(
            href=(
                "https://careers-cretex.icims.com/jobs/5219/"
                "it-cyber-security-risk-analyst/job?hub=15&amp;in_iframe=1"
            ),
            title="5219 - IT Cyber Security Risk Analyst",
        )
        same_id_other_child = job_card(
            href=(
                "https://qts-cretex.icims.com/jobs/5219/"
                "different-role/job?hub=15&amp;in_iframe=1"
            ),
            title="5219 - Different Role",
        )
        for name, cards in (
            ("duplicate-card", (first, first)),
            ("same-id-different-child", (first, same_id_other_child)),
        ):
            with self.subTest(case=name):
                _, result = self._list(
                    source_host="cretex-companies.icims.com",
                    html=aggregate_html(
                        customer="cretex.icims.com",
                        cards=cards,
                    ),
                    title="IT Cyber Security Risk Analyst",
                )
                self.assertEqual(result.candidates, [])

    def test_job_candidate_rejects_untyped_route_evidence(self):
        with self.assertRaisesRegex(
            TypeError,
            "Provider opening route evidence is invalid",
        ):
            JobCandidate(
                title="Engineer",
                url="https://careers-acme.icims.com/jobs/1/engineer/job",
                provider="icims",
                route_evidence={"detail_verified": False},
            )


if __name__ == "__main__":
    unittest.main()
