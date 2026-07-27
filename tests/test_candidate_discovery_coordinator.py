from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from job_source_agent.candidate_discovery_coordinator import (
    CandidateDiscoveryCoordinator,
    CandidateDiscoveryInput,
    CandidateDiscoveryRoute,
    CandidateDiscoveryRouteStatus,
    ExternalApplyObservation,
    RouteFailure,
    RouteProducerOutput,
    RouteProvenance,
    WebsiteCareerRouteInput,
    WebsiteCareerRouteSuppression,
)
from job_source_agent.provider_candidates import ProviderCandidate


def source_input(
    *,
    observation: ExternalApplyObservation = ExternalApplyObservation.OBSERVED,
    external_apply_url: str | None = "https://jobs.example.com/apply",
) -> CandidateDiscoveryInput:
    return CandidateDiscoveryInput(
        source_company_name="Example",
        target_title="AI Engineer",
        target_location="New York, NY",
        linkedin_job_url="https://www.linkedin.com/jobs/view/1234567890",
        linkedin_job_id="1234567890",
        linkedin_company_url="https://www.linkedin.com/company/example",
        source="linkedin_extension",
        source_evidence_provenance="authenticated_detail_dom",
        external_apply_observation=observation,
        external_apply_url=external_apply_url,
    )


def route_input() -> WebsiteCareerRouteInput:
    return WebsiteCareerRouteInput(
        company_website_url="https://www.example.com",
        career_page_url="https://www.example.com/careers",
        evidence_scope="verified_first_party_career",
        evidence_urls=(
            "https://www.example.com",
            "https://www.example.com/careers",
        ),
    )


def candidate(
    url: str,
    source_kind: str,
    *,
    rank: int | None = None,
) -> ProviderCandidate:
    kwargs = {
        "url": url,
        "source_kind": source_kind,
        "source_url": url if not source_kind.startswith("targeted_") else "https://www.bing.com/search?q=example",
        "company_name": "Example",
        "target_title": "AI Engineer",
        "target_location": "New York, NY",
    }
    if source_kind.startswith("targeted_"):
        kwargs["query"] = "Example AI Engineer"
        kwargs["result_rank"] = rank or 1
    return ProviderCandidate(**kwargs)


def output(*candidates: ProviderCandidate, producer: str = "fixture") -> RouteProducerOutput:
    return RouteProducerOutput(tuple(candidates), RouteProvenance(producer))


def coordinator(
    *,
    external=lambda value: output(),
    provider=lambda value: output(),
    website=lambda value, route: output(),
    limit: int = 12,
) -> CandidateDiscoveryCoordinator:
    return CandidateDiscoveryCoordinator(
        external_apply=external,
        provider_search=provider,
        website_career=website,
        candidate_limit=limit,
    )


class CandidateDiscoveryCoordinatorTests(unittest.TestCase):
    def test_input_is_frozen_and_observed_external_apply_is_canonicalized(self):
        value = source_input(external_apply_url="https://jobs.example.com/apply/")

        self.assertEqual(value.external_apply_url, "https://jobs.example.com/apply")
        with self.assertRaises(FrozenInstanceError):
            value.source_company_name = "Other"  # type: ignore[misc]

    def test_external_apply_observation_states_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "requires a sanitized URL"):
            source_input(external_apply_url=None)
        with self.assertRaisesRegex(ValueError, "requires observed state"):
            source_input(
                observation=ExternalApplyObservation.OBSERVED_ABSENT,
                external_apply_url="https://jobs.example.com/apply",
            )
        with self.assertRaisesRegex(ValueError, "Candidate discovery URL"):
            source_input(external_apply_url="http://jobs.example.com/apply")

        calls: list[str] = []
        result = coordinator(
            external=lambda value: calls.append("external") or output(),
            provider=lambda value: calls.append("provider") or output(),
        ).discover(
            source_input(
                observation=ExternalApplyObservation.NOT_OBSERVED,
                external_apply_url=None,
            )
        )
        self.assertEqual(calls, ["provider"])
        self.assertEqual(
            result.route_results[0].status,
            CandidateDiscoveryRouteStatus.NOT_APPLICABLE,
        )
        self.assertEqual(result.route_results[0].failure.reason_code, "detail_not_observed")

    def test_website_route_requires_verified_immutable_evidence(self):
        with self.assertRaisesRegex(ValueError, "requires a verified URL"):
            WebsiteCareerRouteInput(None, None, "scope", ("https://www.example.com",))
        with self.assertRaisesRegex(ValueError, "immutable evidence URLs"):
            WebsiteCareerRouteInput("https://www.example.com", None, "scope", [])  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "cover every route URL"):
            WebsiteCareerRouteInput(
                "https://www.example.com",
                "https://www.example.com/careers",
                "scope",
                ("https://www.example.com",),
            )
        with self.assertRaises(FrozenInstanceError):
            route_input().evidence_scope = "other"  # type: ignore[misc]

    def test_matching_typed_suppression_only_stops_website_route(self):
        calls: list[str] = []
        result = coordinator(
            external=lambda value: calls.append("external") or output(
                candidate("https://jobs.example.com/external", "external_apply")
            ),
            provider=lambda value: calls.append("provider") or output(
                candidate("https://jobs.example.com/search", "targeted_board_search")
            ),
            website=lambda value, route: calls.append("website") or output(),
        ).discover(
            source_input(),
            website_career_input=route_input(),
            website_career_suppression=WebsiteCareerRouteSuppression(
                rejected_url="https://www.example.com/careers",
                evidence_scope="verified_first_party_career",
                reason_code="identity_rejected",
            ),
        )

        self.assertEqual(calls, ["external", "provider"])
        self.assertEqual(result.route_results[2].status, CandidateDiscoveryRouteStatus.SUPPRESSED)
        self.assertEqual(result.route_results[2].suppression.reason_code, "identity_rejected")
        self.assertEqual(len(result.candidates), 2)

    def test_mismatched_suppression_does_not_hide_website_route(self):
        calls: list[str] = []
        result = coordinator(
            website=lambda value, route: calls.append("website") or output(
                candidate("https://boards.greenhouse.io/example", "first_party_ats_link")
            )
        ).discover(
            source_input(),
            website_career_input=route_input(),
            website_career_suppression=WebsiteCareerRouteSuppression(
                rejected_url="https://www.example.com/careers",
                evidence_scope="another_scope",
                reason_code="identity_rejected",
            ),
        )

        self.assertEqual(calls, ["website"])
        self.assertEqual(result.route_results[2].status, CandidateDiscoveryRouteStatus.COMPLETED)

    def test_one_route_failure_does_not_prevent_other_independent_routes(self):
        result = coordinator(
            external=lambda value: (_ for _ in ()).throw(RuntimeError("network")),
            provider=lambda value: output(
                candidate("https://jobs.lever.co/example", "targeted_board_search")
            ),
            website=lambda value, route: output(
                candidate("https://boards.greenhouse.io/example", "first_party_ats_link")
            ),
        ).discover(source_input(), website_career_input=route_input())

        self.assertEqual(result.route_results[0].status, CandidateDiscoveryRouteStatus.FAILED)
        self.assertEqual(result.route_results[0].failure.error_type, "RuntimeError")
        self.assertEqual(
            [item.url for item in result.candidates],
            ["https://boards.greenhouse.io/example", "https://jobs.lever.co/example"],
        )

    def test_explicit_budget_exhaustion_is_retained_without_blocking_other_routes(self):
        result = coordinator(
            provider=lambda value: RouteProducerOutput(
                (),
                RouteProvenance("provider_fixture", request_count=5, truncated=True),
                status=CandidateDiscoveryRouteStatus.BUDGET_EXHAUSTED,
                failure=RouteFailure("query_budget_exhausted", retryable=True),
            ),
            website=lambda value, route: output(
                candidate("https://boards.greenhouse.io/example", "first_party_ats_link")
            ),
        ).discover(source_input(), website_career_input=route_input())

        provider_result = result.route_results[1]
        self.assertEqual(provider_result.status, CandidateDiscoveryRouteStatus.BUDGET_EXHAUSTED)
        self.assertTrue(provider_result.failure.retryable)
        self.assertEqual(provider_result.provenance.request_count, 5)
        self.assertEqual(len(result.candidates), 1)

    def test_productive_route_reservations_survive_global_cap(self):
        external = candidate("https://jobs.example.com/external", "external_apply")
        provider_candidates = tuple(
            candidate(f"https://jobs.lever.co/example-{index}", "targeted_board_search", rank=index + 1)
            for index in range(3)
        )
        website = candidate("https://boards.greenhouse.io/example", "first_party_ats_link")

        result = coordinator(
            external=lambda value: output(external),
            provider=lambda value: output(*provider_candidates),
            website=lambda value, route: output(website),
            limit=3,
        ).discover(source_input(), website_career_input=route_input())

        self.assertEqual(len(result.candidates), 3)
        sources = {item.source_kind for item in result.candidates}
        self.assertEqual(sources, {"external_apply", "targeted_board_search", "first_party_ats_link"})
        self.assertTrue(result.truncated)

    def test_url_dedupe_keeps_every_route_attribution(self):
        shared_external = candidate("https://jobs.ashbyhq.com/example/role", "external_apply")
        shared_search = candidate("https://jobs.ashbyhq.com/example/role/", "targeted_opening_search")
        result = coordinator(
            external=lambda value: output(shared_external),
            provider=lambda value: output(shared_search),
        ).discover(source_input())

        self.assertEqual(result.candidates, (shared_external,))
        self.assertEqual(
            result.attributions[0].routes,
            (CandidateDiscoveryRoute.EXTERNAL_APPLY, CandidateDiscoveryRoute.PROVIDER_SEARCH),
        )

    def test_ordering_is_deterministic_across_producer_result_order(self):
        first = candidate("https://jobs.lever.co/example", "targeted_board_search", rank=2)
        second = candidate("https://jobs.ashbyhq.com/example", "targeted_board_search", rank=1)
        a = coordinator(provider=lambda value: output(first, second)).discover(source_input())
        b = coordinator(provider=lambda value: output(second, first)).discover(source_input())

        self.assertEqual(a.candidates, b.candidates)
        self.assertEqual(a.attributions, b.attributions)
        self.assertEqual(
            [item.url for item in a.candidates],
            ["https://jobs.ashbyhq.com/example", "https://jobs.lever.co/example"],
        )

    def test_invalid_producer_output_fails_closed_without_blocking_other_routes(self):
        result = coordinator(
            external=lambda value: "not an output",  # type: ignore[return-value]
            provider=lambda value: output(
                candidate("https://jobs.lever.co/example", "targeted_board_search")
            ),
        ).discover(source_input())

        self.assertEqual(result.route_results[0].status, CandidateDiscoveryRouteStatus.FAILED)
        self.assertEqual(result.route_results[0].failure.error_type, "TypeError")
        self.assertEqual(len(result.candidates), 1)

    def test_invalid_input_and_constructor_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "LinkedIn job URL must use linkedin.com"):
            CandidateDiscoveryInput(
                source_company_name="Example",
                target_title=None,
                target_location=None,
                linkedin_job_url="https://jobs.example.com/123",
                linkedin_job_id="123",
                linkedin_company_url=None,
                source="linkedin_extension",
                source_evidence_provenance="authenticated_detail_dom",
                external_apply_observation=ExternalApplyObservation.NOT_OBSERVED,
            )
        with self.assertRaisesRegex(ValueError, "does not bind"):
            CandidateDiscoveryInput(
                source_company_name="Example",
                target_title=None,
                target_location=None,
                linkedin_job_url="https://www.linkedin.com/jobs/view/1234567890",
                linkedin_job_id="9876543210",
                linkedin_company_url=None,
                source="linkedin_extension",
                source_evidence_provenance="authenticated_detail_dom",
                external_apply_observation=ExternalApplyObservation.NOT_OBSERVED,
            )
        without_linkedin = CandidateDiscoveryInput(
            source_company_name="Example",
            target_title="Engineer",
            target_location=None,
            linkedin_job_url=None,
            linkedin_job_id=None,
            linkedin_company_url=None,
            source="input",
            source_evidence_provenance="normalized_input",
            external_apply_observation=ExternalApplyObservation.NOT_OBSERVED,
        )
        self.assertIsNone(without_linkedin.linkedin_job_url)
        slug_bound = CandidateDiscoveryInput(
            source_company_name="Example",
            target_title="Engineer",
            target_location=None,
            linkedin_job_url=(
                "https://www.linkedin.com/jobs/view/engineer-at-example-1234567890"
            ),
            linkedin_job_id="1234567890",
            linkedin_company_url=None,
            source="input",
            source_evidence_provenance="normalized_input",
            external_apply_observation=ExternalApplyObservation.NOT_OBSERVED,
        )
        self.assertEqual(slug_bound.linkedin_job_id, "1234567890")
        with self.assertRaisesRegex(ValueError, "supplied together"):
            CandidateDiscoveryInput(
                source_company_name="Example",
                target_title=None,
                target_location=None,
                linkedin_job_url="https://www.linkedin.com/jobs/view/1234567890",
                linkedin_job_id=None,
                linkedin_company_url=None,
                source="input",
                source_evidence_provenance="normalized_input",
                external_apply_observation=ExternalApplyObservation.NOT_OBSERVED,
            )
        with self.assertRaisesRegex(ValueError, "limit"):
            coordinator(limit=13)
        with self.assertRaisesRegex(ValueError, "limit"):
            coordinator(limit=0)
        self.assertEqual(coordinator(limit=1)._candidate_limit, 1)
        with self.assertRaisesRegex(TypeError, "requires immutable S1 input"):
            coordinator().discover("not input")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
