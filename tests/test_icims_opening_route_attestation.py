from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from urllib.parse import urlparse

from job_source_agent.identity_continuity import ProviderOpeningRouteEvidence
from job_source_agent.opening_matcher import (
    JobOpeningMatcher,
    _icims_detail_payload_url,
)
from job_source_agent.providers.base import AdapterResult, JobBoard
from job_source_agent.providers.registry import ProviderRegistry
from job_source_agent.web import Page


class _ICIMSRouteAdapter:
    name = "icims"
    supports_listing = True

    def __init__(self, candidate) -> None:
        self.candidate = candidate

    def recognizes(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").casefold()
        return host.endswith(".icims.com")

    def identify_board(self, url: str) -> JobBoard | None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme != "https" or not host.endswith(".icims.com"):
            return None
        return JobBoard(
            url=f"https://{host}/jobs/search",
            provider=self.name,
            identifier=host,
        )

    def list_jobs(self, fetcher, board: JobBoard, query) -> AdapterResult:
        return AdapterResult(
            provider=self.name,
            board=board,
            candidates=[self.candidate],
            inventory_scope="title_filtered",
            inventory_complete=True,
        )


class _DetailFetcher:
    def __init__(
        self,
        expected_url: str,
        html: str,
        *,
        final_url: str | None = None,
    ) -> None:
        self.expected_url = expected_url
        self.html = html
        self.final_url = final_url
        self.calls: list[str] = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append(url)
        if url != self.expected_url:
            raise AssertionError(f"unexpected fetch: {url}")
        return Page(
            url=url,
            html=self.html,
            final_url=self.final_url or url,
            source="fixture",
        )


class _ShellDetailFetcher:
    def __init__(self, canonical_url: str, payload_html: str) -> None:
        self.canonical_url = canonical_url
        self.payload_url = f"{canonical_url}?in_iframe=1"
        self.payload_html = payload_html
        self.calls: list[str] = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append(url)
        if url == self.canonical_url:
            html = (
                "<script>icimsFrame.src = "
                f"'https:\\/\\/{urlparse(url).netloc}"
                f"{urlparse(url).path}?in_iframe=1';</script>"
                f'<iframe src="{self.payload_url}" '
                'id="noscript_icims_content_iframe"></iframe>'
            )
        elif url == self.payload_url:
            html = self.payload_html
        else:
            raise AssertionError(f"unexpected fetch: {url}")
        return Page(url=url, html=html, final_url=url, source="fixture")


def _candidate(
    *,
    title: str,
    location: str,
    opening_url: str,
    route: ProviderOpeningRouteEvidence | None,
):
    return SimpleNamespace(
        title=title,
        location=location,
        url=opening_url,
        provider="icims",
        raw={},
        route_evidence=route,
    )


def _route(
    *,
    source_host: str,
    target_host: str,
    opening_id: str,
    slug: str,
    customer: str,
    hub: str,
) -> ProviderOpeningRouteEvidence:
    opening_url = f"https://{target_host}/jobs/{opening_id}/{slug}/job"
    return ProviderOpeningRouteEvidence(
        provider="icims",
        source_tenant=source_host,
        source_canonical_board_url=f"https://{source_host}/jobs/search",
        target_tenant=target_host,
        target_canonical_board_url=f"https://{target_host}/jobs/search",
        canonical_opening_url=opening_url,
        opening_id=opening_id,
        source_response_url=(
            f"https://{source_host}/jobs/search?ss=1&searchKeyword=Engineer"
        ),
        source_customer_identity=customer,
        target_customer_identity=None,
        route_identity=f"hub:{hub}",
        detail_evidence_url=None,
        extraction_method="icims_aggregate_job_card",
        detail_verified=False,
    )


def _job_posting(
    *,
    title: str,
    location: str,
    opening_url: str,
    employer: str,
) -> dict:
    city, region = [part.strip() for part in location.split(",", 1)]
    return {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": title,
        "url": opening_url,
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": city,
                "addressRegion": region,
            },
        },
        "hiringOrganization": {
            "@type": "Organization",
            "name": employer,
        },
    }


def _detail_html(
    *,
    customer: str | None,
    postings: list[dict],
    hub_values: tuple[str, ...] = (),
    extra_customer: str | None = None,
    closed: bool = False,
) -> str:
    markers = ""
    if customer:
        markers += (
            '<link rel="stylesheet" href="https://cdn02.icims.com/platform/x/'
            f"{customer}/icims2/servlet/icims2?module=AppInert\">"
        )
    if extra_customer:
        markers += (
            '<meta content="https://cdn02.icims.com/platform/x/'
            f"{extra_customer}/icims2/servlet/icims2?module=AppInert\">"
        )
    hubs = "".join(f'<a href="/connect?hub={hub}">route</a>' for hub in hub_values)
    closed_copy = "<main>This job is no longer available</main>" if closed else ""
    return (
        f"{markers}{hubs}{closed_copy}"
        '<script type="application/ld+json">'
        f"{json.dumps(postings if len(postings) != 1 else postings[0])}"
        "</script>"
    )


def _run(
    route: ProviderOpeningRouteEvidence,
    *,
    title: str,
    source_location: str,
    target_location: str,
    html: str,
    final_url: str | None = None,
):
    candidate = _candidate(
        title=title,
        location=source_location,
        opening_url=route.canonical_opening_url,
        route=route,
    )
    fetcher = _DetailFetcher(
        route.canonical_opening_url,
        html,
        final_url=final_url,
    )
    matcher = JobOpeningMatcher(
        fetcher,
        ProviderRegistry([_ICIMSRouteAdapter(candidate)]),
    )
    match, trace = matcher.match(
        route.source_canonical_board_url,
        title,
        target_location,
    )
    return match, trace, fetcher


class ICIMSOpeningRouteAttestationTests(unittest.TestCase):
    def test_iframe_payload_rejects_extra_query_and_cross_host_routes(self):
        canonical = (
            "https://careers-example.icims.com/jobs/123/example-engineer/job"
        )
        cases = (
            (
                f'<iframe src="{canonical}?in_iframe=1&token=secret"></iframe>',
                None,
            ),
            (
                '<iframe src="https://other.icims.com/jobs/123/'
                'example-engineer/job?in_iframe=1"></iframe>',
                None,
            ),
            (
                f'<iframe src="{canonical}?in_iframe=1"></iframe>'
                '<iframe src="https://careers-example.icims.com/jobs/124/'
                'other/job?in_iframe=1"></iframe>',
                f"{canonical}?in_iframe=1",
            ),
        )
        for html, expected in cases:
            with self.subTest(html=html):
                payload_url, reason = _icims_detail_payload_url(html, canonical)
                self.assertEqual(payload_url, expected)
                self.assertIsNone(reason)

    def test_same_opening_iframe_payload_is_attested_once(self):
        route = _route(
            source_host="cretex-companies.icims.com",
            target_host="careers-cretex.icims.com",
            opening_id="5219",
            slug="it-cyber-security-risk-analyst",
            customer="cretex.icims.com",
            hub="15",
        )
        payload_html = _detail_html(
            customer="cretex.icims.com",
            postings=[
                _job_posting(
                    title="IT Cyber Security Risk Analyst",
                    location="Elk River, MN",
                    opening_url=route.canonical_opening_url,
                    employer="Cretex Companies, Inc.",
                )
            ],
        )
        candidate = _candidate(
            title="IT Cyber Security Risk Analyst",
            location="Elk River, MN",
            opening_url=route.canonical_opening_url,
            route=route,
        )
        fetcher = _ShellDetailFetcher(route.canonical_opening_url, payload_html)
        matcher = JobOpeningMatcher(
            fetcher,
            ProviderRegistry([_ICIMSRouteAdapter(candidate)]),
        )

        match, trace = matcher.match(
            route.source_canonical_board_url,
            "IT Cyber Security Risk Analyst",
            "Elk River, MN",
        )

        self.assertIsNotNone(match)
        self.assertTrue(match.route_evidence.detail_verified)
        self.assertEqual(
            fetcher.calls,
            [route.canonical_opening_url, fetcher.payload_url],
        )
        self.assertEqual(
            trace["provider_api"]["provider_route_attestation"][0][
                "detail_payload_url"
            ],
            fetcher.payload_url,
        )

    def test_three_aggregate_child_shapes_attest_exact_detail(self):
        controls = (
            {
                "name": "cretex",
                "source": "cretex-companies.icims.com",
                "target": "careers-cretex.icims.com",
                "customer": "cretex.icims.com",
                "hub": "15",
                "opening_id": "5219",
                "slug": "it-cyber-security-risk-analyst",
                "title": "IT Cyber Security Risk Analyst",
                "source_location": "US-MN-Elk River",
                "detail_location": "Elk River, MN",
                "employer": "Cretex Companies, Inc.",
                "detail_hubs": (),
                "detail_slug": "it-cyber-security-risk-analyst",
            },
            {
                "name": "emory",
                "source": "ehccareers-emory.icims.com",
                "target": "clinical-emory.icims.com",
                "customer": "emory.icims.com",
                "hub": "14",
                "opening_id": "170893",
                "slug": "medical-assistant",
                "title": "Medical Assistant",
                "source_location": "Johns Creek, GA, 30097",
                "detail_location": "Johns Creek, GA",
                "employer": "Emory Healthcare",
                "detail_hubs": ("14", "14"),
                "detail_slug": "medical-assistant",
            },
            {
                "name": "hochunk",
                "source": "hub-hochunk.icims.com",
                "target": "careers-allnativegroup.icims.com",
                "customer": "ho-chunk.icims.com",
                "hub": "26",
                "opening_id": "10483",
                "slug": "cable-foreman",
                "title": "Cable Foreman",
                "source_location": "US-DC-Washington",
                "detail_location": "Washington, DC",
                "employer": "All Native Group",
                "detail_hubs": ("26",),
                "detail_slug": "2026-10483",
            },
        )
        for control in controls:
            with self.subTest(control=control["name"]):
                route = _route(
                    source_host=control["source"],
                    target_host=control["target"],
                    opening_id=control["opening_id"],
                    slug=control["slug"],
                    customer=control["customer"],
                    hub=control["hub"],
                )
                detail_url = (
                    f"https://{control['target']}/jobs/{control['opening_id']}/"
                    f"{control['detail_slug']}/job"
                )
                html = _detail_html(
                    customer=control["customer"],
                    postings=[
                        _job_posting(
                            title=control["title"],
                            location=control["detail_location"],
                            opening_url=detail_url,
                            employer=control["employer"],
                        )
                    ],
                    hub_values=control["detail_hubs"],
                )

                match, trace, fetcher = _run(
                    route,
                    title=control["title"],
                    source_location=control["source_location"],
                    target_location=control["detail_location"],
                    html=html,
                )

                self.assertIsNotNone(match)
                assert match is not None
                self.assertEqual(match.url, route.canonical_opening_url)
                self.assertEqual(match.location, control["detail_location"])
                self.assertEqual(
                    match.hiring_organization_name,
                    control["employer"],
                )
                self.assertIsNotNone(match.route_evidence)
                assert match.route_evidence is not None
                self.assertTrue(match.route_evidence.detail_verified)
                self.assertEqual(
                    match.route_evidence.target_customer_identity,
                    control["customer"],
                )
                self.assertEqual(fetcher.calls, [route.canonical_opening_url])
                self.assertEqual(
                    trace["provider_api"]["provider_route_attestation"][0]["status"],
                    "verified",
                )
                self.assertEqual(
                    trace["selected"]["route_evidence"],
                    match.route_evidence.to_trace_payload(),
                )

    def test_emory_and_hochunk_cross_company_details_are_rejected(self):
        pairs = (
            (
                "ehccareers-emory.icims.com",
                "clinical-emory.icims.com",
                "emory.icims.com",
                "170893",
                "medical-assistant",
                "14",
                "Medical Assistant",
                "Johns Creek, GA",
                "ho-chunk.icims.com",
                "All Native Group",
            ),
            (
                "hub-hochunk.icims.com",
                "careers-allnativegroup.icims.com",
                "ho-chunk.icims.com",
                "10483",
                "cable-foreman",
                "26",
                "Cable Foreman",
                "Washington, DC",
                "emory.icims.com",
                "Emory Healthcare",
            ),
        )
        for (
            source,
            target,
            source_customer,
            opening_id,
            slug,
            hub,
            title,
            location,
            detail_customer,
            employer,
        ) in pairs:
            with self.subTest(source=source, detail_customer=detail_customer):
                route = _route(
                    source_host=source,
                    target_host=target,
                    opening_id=opening_id,
                    slug=slug,
                    customer=source_customer,
                    hub=hub,
                )
                html = _detail_html(
                    customer=detail_customer,
                    postings=[
                        _job_posting(
                            title=title,
                            location=location,
                            opening_url=route.canonical_opening_url,
                            employer=employer,
                        )
                    ],
                    hub_values=(hub,),
                )

                match, trace, _fetcher = _run(
                    route,
                    title=title,
                    source_location=location,
                    target_location=location,
                    html=html,
                )

                self.assertIsNone(match)
                self.assertEqual(
                    trace["provider_api"]["provider_route_attestation"][0]["reason"],
                    "customer_marker_mismatch",
                )

    def test_missing_or_conflicting_customer_marker_is_rejected(self):
        route = _route(
            source_host="cretex-companies.icims.com",
            target_host="careers-cretex.icims.com",
            opening_id="5219",
            slug="it-cyber-security-risk-analyst",
            customer="cretex.icims.com",
            hub="15",
        )
        posting = _job_posting(
            title="IT Cyber Security Risk Analyst",
            location="Elk River, MN",
            opening_url=route.canonical_opening_url,
            employer="Cretex Companies",
        )
        cases = (
            (
                _detail_html(customer=None, postings=[posting]),
                "customer_marker_missing_or_invalid",
            ),
            (
                _detail_html(
                    customer="cretex.icims.com",
                    extra_customer="emory.icims.com",
                    postings=[posting],
                ),
                "customer_marker_conflict",
            ),
        )
        for html, reason in cases:
            with self.subTest(reason=reason):
                match, trace, _fetcher = _run(
                    route,
                    title="IT Cyber Security Risk Analyst",
                    source_location="US-MN-Elk River",
                    target_location="Elk River, MN",
                    html=html,
                )
                self.assertIsNone(match)
                self.assertEqual(
                    trace["provider_api"]["provider_route_attestation"][0]["reason"],
                    reason,
                )

    def test_detail_redirect_is_rejected_before_page_evidence(self):
        route = _route(
            source_host="ehccareers-emory.icims.com",
            target_host="clinical-emory.icims.com",
            opening_id="170893",
            slug="medical-assistant",
            customer="emory.icims.com",
            hub="14",
        )
        html = _detail_html(
            customer="emory.icims.com",
            postings=[
                _job_posting(
                    title="Medical Assistant",
                    location="Johns Creek, GA",
                    opening_url=route.canonical_opening_url,
                    employer="Emory Healthcare",
                )
            ],
        )

        match, trace, _fetcher = _run(
            route,
            title="Medical Assistant",
            source_location="Johns Creek, GA",
            target_location="Johns Creek, GA",
            html=html,
            final_url=(
                "https://careers-allnativegroup.icims.com/jobs/10483/"
                "cable-foreman/job"
            ),
        )

        self.assertIsNone(match)
        self.assertEqual(
            trace["provider_api"]["provider_route_attestation"][0]["reason"],
            "detail_redirect_or_canonical_route_mismatch",
        )

    def test_detail_title_and_location_conflicts_are_rejected(self):
        route = _route(
            source_host="cretex-companies.icims.com",
            target_host="careers-cretex.icims.com",
            opening_id="5219",
            slug="it-cyber-security-risk-analyst",
            customer="cretex.icims.com",
            hub="15",
        )
        cases = (
            (
                "Security Engineer",
                "Elk River, MN",
                "detail_title_identity_mismatch",
            ),
            (
                "IT Cyber Security Risk Analyst",
                "Minneapolis, MN",
                "detail_location_identity_mismatch",
            ),
            (
                "IT Cyber Security Risk Analyst",
                "Elk River, WI",
                "detail_location_identity_mismatch",
            ),
        )
        for detail_title, detail_location, reason in cases:
            with self.subTest(reason=reason):
                html = _detail_html(
                    customer="cretex.icims.com",
                    postings=[
                        _job_posting(
                            title=detail_title,
                            location=detail_location,
                            opening_url=route.canonical_opening_url,
                            employer="Cretex Companies",
                        )
                    ],
                )
                match, trace, _fetcher = _run(
                    route,
                    title="IT Cyber Security Risk Analyst",
                    source_location="US-MN-Elk River",
                    target_location="Elk River, MN",
                    html=html,
                )
                self.assertIsNone(match)
                self.assertEqual(
                    trace["provider_api"]["provider_route_attestation"][0]["reason"],
                    reason,
                )

    def test_conflicting_detail_employers_are_rejected(self):
        route = _route(
            source_host="ehccareers-emory.icims.com",
            target_host="clinical-emory.icims.com",
            opening_id="170893",
            slug="medical-assistant",
            customer="emory.icims.com",
            hub="14",
        )
        postings = [
            _job_posting(
                title="Medical Assistant",
                location="Johns Creek, GA",
                opening_url=route.canonical_opening_url,
                employer=employer,
            )
            for employer in ("Emory Healthcare", "All Native Group")
        ]

        match, trace, _fetcher = _run(
            route,
            title="Medical Assistant",
            source_location="Johns Creek, GA",
            target_location="Johns Creek, GA",
            html=_detail_html(customer="emory.icims.com", postings=postings),
        )

        self.assertIsNone(match)
        self.assertEqual(
            trace["provider_api"]["provider_route_attestation"][0]["reason"],
            "detail_employer_identity_conflict",
        )

    def test_closed_opening_wrong_id_and_hub_conflict_fail_closed(self):
        route = _route(
            source_host="hub-hochunk.icims.com",
            target_host="careers-allnativegroup.icims.com",
            opening_id="10483",
            slug="cable-foreman",
            customer="ho-chunk.icims.com",
            hub="26",
        )
        valid_posting = _job_posting(
            title="Cable Foreman",
            location="Washington, DC",
            opening_url=route.canonical_opening_url,
            employer="All Native Group",
        )
        wrong_id_posting = _job_posting(
            title="Cable Foreman",
            location="Washington, DC",
            opening_url=(
                "https://careers-allnativegroup.icims.com/jobs/99999/"
                "cable-foreman/job"
            ),
            employer="All Native Group",
        )
        cases = (
            (
                _detail_html(
                    customer="ho-chunk.icims.com",
                    postings=[valid_posting],
                    closed=True,
                ),
                "opening_closed_or_unavailable",
            ),
            (
                _detail_html(
                    customer="ho-chunk.icims.com",
                    postings=[wrong_id_posting],
                ),
                "opening_id_or_detail_route_mismatch",
            ),
            (
                _detail_html(
                    customer="ho-chunk.icims.com",
                    postings=[valid_posting],
                    hub_values=("26", "15"),
                ),
                "route_identity_conflict",
            ),
            (
                _detail_html(
                    customer="ho-chunk.icims.com",
                    postings=[valid_posting],
                    hub_values=("15",),
                ),
                "route_identity_mismatch",
            ),
        )
        for html, reason in cases:
            with self.subTest(reason=reason):
                match, trace, _fetcher = _run(
                    route,
                    title="Cable Foreman",
                    source_location="US-DC-Washington",
                    target_location="Washington, DC",
                    html=html,
                )
                self.assertIsNone(match)
                self.assertEqual(
                    trace["provider_api"]["provider_route_attestation"][0]["reason"],
                    reason,
                )

    def test_same_tenant_candidate_without_route_keeps_ordinary_behavior(self):
        opening_url = "https://careers-acme.icims.com/jobs/123/engineer/job"
        candidate = _candidate(
            title="Software Engineer",
            location="Boston, MA",
            opening_url=opening_url,
            route=None,
        )

        class ForbiddenFetcher:
            def fetch(self, url, data=None, headers=None):
                raise AssertionError("ordinary exact native candidate must not fetch detail")

        match, trace = JobOpeningMatcher(
            ForbiddenFetcher(),
            ProviderRegistry([_ICIMSRouteAdapter(candidate)]),
        ).match(
            "https://careers-acme.icims.com/jobs/search",
            "Software Engineer",
            "Boston, MA",
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.url, opening_url)
        self.assertIsNone(match.route_evidence)
        self.assertNotIn(
            "provider_route_attestation",
            trace["provider_api"],
        )
        self.assertNotIn("route_evidence", trace["selected"])


if __name__ == "__main__":
    unittest.main()
