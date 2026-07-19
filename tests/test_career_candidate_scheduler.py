import unittest

from job_source_agent.career_candidate_scheduler import (
    candidate_concrete_host,
    candidate_evidence_tier,
    candidate_host_family,
    candidate_locale_key,
    candidate_route_family,
    schedule_career_candidates,
)
from job_source_agent.models import LinkCandidate
from job_source_agent.pipeline import JobSourceAgent
from job_source_agent.web import FetchError, Fetcher


class RecordingFailureFetcher(Fetcher):
    def __init__(self):
        super().__init__(offline=True)
        self.calls = []

    def fetch(self, url, data=None, headers=None):
        self.calls.append(url)
        raise FetchError(f"fixture miss: {url}")


def candidate(
    url,
    score,
    reasons,
    *,
    text="",
    origin="unknown",
    source_url="https://example.com",
):
    return LinkCandidate(
        url=url,
        text=text,
        source_url=source_url,
        score=score,
        reasons=reasons,
        origin=origin,
    )


def schedule(agent, candidates):
    return schedule_career_candidates(
        candidates,
        fetch_limit=agent.max_career_candidate_fetches,
    )


class CareerCandidateSchedulerTests(unittest.TestCase):
    def test_same_host_embedded_explicit_job_list_uses_first_party_evidence_rank(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        scheduled, trace = schedule(
            agent,
            [
                candidate(
                    "https://example.com/careers",
                    200,
                    ["career keyword 'careers'", "homepage navigation link"],
                    origin="page_link",
                ),
                candidate(
                    "https://example.com/jobs",
                    300,
                    ["explicit job-list route"],
                    origin="embedded_url",
                ),
            ],
        )

        self.assertEqual(
            [item.url for item in scheduled],
            ["https://example.com/jobs", "https://example.com/careers"],
        )
        self.assertEqual(trace["version"], "9")

    def test_verified_homepage_navigation_has_page_link_tier_and_boost(self):
        agent = JobSourceAgent(Fetcher(offline=True))
        verified = candidate(
            "https://example.com/careers",
            100,
            ["career keyword 'careers'", "homepage navigation link"],
            origin="verified_homepage_navigation",
        )
        path_probe = candidate(
            "https://example.com/jobs",
            500,
            ["generated path probe"],
            origin="path_probe",
        )

        scheduled, _trace = schedule(agent, [path_probe, verified])

        self.assertEqual(candidate_evidence_tier(verified), 1)
        self.assertEqual(scheduled[0].origin, "verified_homepage_navigation")

    def test_small_budget_prioritizes_explicit_job_list_command(self):
        command = candidate(
            "https://example.com/careers/search",
            60,
            ["explicit job-list command"],
            text="Search Jobs",
            origin="page_link",
        )
        low_evidence = [
            candidate(
                f"https://example.com/{route}",
                500 - index,
                ["generated path probe"],
                origin="path_probe",
            )
            for index, route in enumerate(("careers", "jobs", "open-positions"))
        ]

        scheduled, _trace = schedule_career_candidates(
            low_evidence + [command],
            fetch_limit=1,
        )

        self.assertEqual(scheduled[0].url, command.url)
        self.assertEqual(candidate_evidence_tier(command), 1)

    def test_small_budget_prioritizes_same_origin_get_form_job_command(self):
        form_action = candidate(
            "https://example.com/careers/open-positions",
            55,
            ["explicit job-list command"],
            text="Open Positions",
            origin="form_action",
            source_url="https://example.com/careers",
        )
        path_probe = candidate(
            "https://example.com/jobs",
            900,
            ["generated path probe"],
            origin="path_probe",
        )

        scheduled, _trace = schedule_career_candidates(
            [path_probe, form_action],
            fetch_limit=1,
        )

        self.assertEqual(scheduled[0].url, form_action.url)
        self.assertEqual(candidate_evidence_tier(form_action), 1)

    def test_unsafe_form_actions_do_not_gain_job_command_evidence(self):
        unsafe = (
            candidate(
                "https://evil.example.net/jobs",
                900,
                ["explicit job-list command"],
                text="Open Positions",
                origin="form_action",
                source_url="https://example.com/careers",
            ),
            candidate(
                "https://user:secret@example.com/jobs",
                900,
                ["explicit job-list command"],
                text="Jobs",
                origin="form_action",
                source_url="https://example.com/careers",
            ),
            candidate(
                "http://example.com/jobs",
                900,
                ["explicit job-list command"],
                text="Jobs",
                origin="form_action",
                source_url="https://example.com/careers",
            ),
        )

        self.assertTrue(all(candidate_evidence_tier(item) > 1 for item in unsafe))

    def test_small_budget_prioritizes_observed_cross_site_ats_candidate(self):
        ats_candidate = candidate(
            "https://boards.greenhouse.io/example",
            60,
            ["known ATS domain", "ATS company board URL", "homepage navigation link"],
            origin="verified_homepage_navigation",
        )
        path_probe = candidate(
            "https://example.com/careers",
            900,
            ["generated path probe"],
            origin="path_probe",
        )

        scheduled, _trace = schedule_career_candidates(
            [path_probe, ats_candidate],
            fetch_limit=1,
        )

        self.assertEqual(scheduled[0].url, ats_candidate.url)
        self.assertEqual(candidate_evidence_tier(ats_candidate), 1)

    def test_observed_http_ats_anchor_precedes_blind_guesses(self):
        ats_candidate = candidate(
            "http://job-boards.greenhouse.io:80/aperiasolutions",
            60,
            ["known ATS domain", "ATS company board URL", "homepage navigation link"],
            origin="page_link",
            source_url="https://www.aperia.com/",
        )
        path_probe = candidate(
            "https://www.aperia.com/careers",
            900,
            ["generated path probe"],
            origin="path_probe",
        )
        blind_ats = candidate(
            "https://job-boards.greenhouse.io/aperia",
            850,
            ["known ATS domain", "ATS company board URL"],
            origin="blind_ats_probe",
        )

        scheduled, _trace = schedule_career_candidates(
            [path_probe, blind_ats, ats_candidate],
            fetch_limit=1,
        )

        self.assertEqual(candidate_evidence_tier(ats_candidate), 2)
        self.assertEqual(scheduled[0].url, ats_candidate.url)

    def test_unsafe_or_unobserved_http_ats_candidates_are_not_promoted(self):
        unsafe_candidates = [
            candidate(
                "http://careers.example.net/jobs",
                900,
                ["career keyword 'jobs'", "homepage navigation link"],
                origin="page_link",
                source_url="https://www.aperia.com/",
            ),
            candidate(
                "http://evil.job-boards.greenhouse.io/aperiasolutions",
                900,
                ["known ATS domain", "homepage navigation link"],
                origin="page_link",
                source_url="https://www.aperia.com/",
            ),
            candidate(
                "http://job-boards.greenhouse.io:8080/aperiasolutions",
                900,
                ["known ATS domain", "homepage navigation link"],
                origin="page_link",
                source_url="https://www.aperia.com/",
            ),
            candidate(
                "http://user:secret@job-boards.greenhouse.io/aperiasolutions",
                900,
                ["known ATS domain", "homepage navigation link"],
                origin="page_link",
                source_url="https://www.aperia.com/",
            ),
            candidate(
                "http://job-boards.greenhouse.io/aperiasolutions",
                900,
                ["known ATS domain", "homepage navigation link"],
                origin="page_link",
                source_url="http://www.aperia.com/",
            ),
            candidate(
                "http://job-boards.greenhouse.io/aperiasolutions",
                900,
                ["known ATS domain", "ATS company board URL"],
                origin="blind_ats_probe",
                source_url="https://www.aperia.com/",
            ),
        ]

        self.assertTrue(
            all(candidate_evidence_tier(item) >= 3 for item in unsafe_candidates)
        )

    def test_blind_or_unsafe_job_list_candidates_are_not_promoted(self):
        path_probe = candidate(
            "https://example.com/careers",
            300,
            ["generated path probe"],
            origin="path_probe",
        )
        blind_ats = candidate(
            "https://boards.greenhouse.io/example",
            200,
            ["known ATS domain", "ATS company board URL"],
            origin="blind_ats_probe",
        )
        unsafe_command = candidate(
            "https://user:secret@example.net/jobs",
            950,
            ["explicit job-list command"],
            text="All Jobs",
            origin="page_link",
        )
        unrelated_command = candidate(
            "https://unrelated.example.net/jobs",
            940,
            ["explicit job-list command"],
            text="Open Positions",
            origin="page_link",
        )

        scheduled, _trace = schedule_career_candidates(
            [blind_ats, unsafe_command, unrelated_command, path_probe],
            fetch_limit=1,
        )

        self.assertEqual(scheduled[0].url, path_probe.url)
        self.assertEqual(candidate_evidence_tier(blind_ats), 3)
        self.assertGreater(candidate_evidence_tier(unsafe_command), 1)
        self.assertGreater(candidate_evidence_tier(unrelated_command), 1)

    def test_cross_host_embedded_explicit_job_list_stays_in_lower_tier(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        scheduled, _trace = schedule(
            agent,
            [
                candidate(
                    "https://example.com/careers",
                    100,
                    ["career keyword 'careers'"],
                    origin="page_link",
                ),
                candidate(
                    "https://jobs.example.net/jobs",
                    900,
                    ["explicit job-list route"],
                    origin="embedded_url",
                ),
            ],
        )

        self.assertEqual(scheduled[0].url, "https://example.com/careers")

    def test_non_explicit_embedded_route_stays_in_lower_tier(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        scheduled, _trace = schedule(
            agent,
            [
                candidate(
                    "https://example.com/careers",
                    100,
                    ["career keyword 'careers'"],
                    origin="page_link",
                ),
                candidate(
                    "https://example.com/about",
                    900,
                    ["embedded page reference"],
                    origin="embedded_url",
                ),
            ],
        )

        self.assertEqual(scheduled[0].url, "https://example.com/careers")

    def test_non_https_embedded_explicit_job_list_stays_in_lower_tier(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        scheduled, _trace = schedule(
            agent,
            [
                candidate(
                    "https://example.com/careers",
                    100,
                    ["career keyword 'careers'"],
                    origin="page_link",
                ),
                candidate(
                    "http://example.com/jobs",
                    900,
                    ["explicit job-list route"],
                    origin="embedded_url",
                ),
            ],
        )

        self.assertEqual(scheduled[0].url, "https://example.com/careers")

    def test_embedded_job_list_from_non_https_source_stays_in_lower_tier(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        scheduled, _trace = schedule(
            agent,
            [
                candidate(
                    "https://example.com/careers",
                    100,
                    ["career keyword 'careers'"],
                    origin="page_link",
                ),
                candidate(
                    "https://example.com/jobs",
                    900,
                    ["explicit job-list route"],
                    origin="embedded_url",
                    source_url="http://example.com",
                ),
            ],
        )

        self.assertEqual(scheduled[0].url, "https://example.com/careers")

    def test_existing_identity_evidence_precedes_promoted_embedded_job_list(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        scheduled, _trace = schedule(
            agent,
            [
                candidate(
                    "https://example.com/career-root",
                    60,
                    ["identity-supplied career root requiring verification"],
                    origin="identity_career_root",
                ),
                candidate(
                    "https://example.com/jobs",
                    900,
                    ["explicit job-list route"],
                    origin="embedded_url",
                ),
            ],
        )

        self.assertEqual(scheduled[0].url, "https://example.com/career-root")

    def test_lower_score_embedded_explicit_job_list_remains_ineligible(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        scheduled, trace = schedule(
            agent,
            [
                candidate(
                    "https://example.com/jobs",
                    49,
                    ["explicit job-list route"],
                    origin="embedded_url",
                ),
            ],
        )

        self.assertEqual(scheduled, [])
        self.assertEqual(trace["eligible_count"], 0)

    def test_evidence_tiers_precede_generated_score(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        scheduled, _trace = schedule(
            agent,
            [
                candidate(
                    "https://example.com/en-us/careers",
                    900,
                    ["generated path probe"],
                ),
                candidate(
                    "https://example.com/team",
                    100,
                    [
                        "homepage navigation link",
                        "homepage team link requiring employment evidence",
                    ],
                    text="Team",
                ),
                candidate(
                    "https://example.com/career-root",
                    60,
                    ["identity-supplied career root requiring verification"],
                ),
            ]
        )

        self.assertEqual(
            [item.url for item in scheduled],
            [
                "https://example.com/career-root",
                "https://example.com/team",
                "https://example.com/en-us/careers",
            ],
        )

    def test_generated_families_get_representatives_before_host_and_locale_aliases(self):
        agent = JobSourceAgent(Fetcher(offline=True))
        candidates = [
            candidate("https://www.example.com/careers", 500, ["generated path probe"]),
            candidate("https://example.com/en/careers", 490, ["generated path probe"]),
            candidate("https://example.com/careers", 300, ["generated path probe"]),
            candidate("https://www.example.com/jobs", 480, ["generated path probe"]),
            candidate("https://example.com/jobs", 290, ["generated path probe"]),
        ]

        scheduled, trace = schedule(agent, candidates)
        scheduled_urls = [item.url for item in scheduled]

        self.assertEqual(
            scheduled_urls[:2],
            ["https://example.com/careers", "https://example.com/jobs"],
        )
        self.assertCountEqual(scheduled_urls, [item.url for item in candidates])
        self.assertEqual(trace["deferred_alias_count"], 3)

    def test_generated_root_represents_route_family_before_locale_variant(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        scheduled, _trace = schedule(
            agent,
            [
                candidate(
                    "https://example.com/en-us/careers",
                    600,
                    ["generated path probe", "localized career section"],
                ),
                candidate(
                    "https://example.com/careers",
                    200,
                    ["generated path probe", "concise career root path"],
                ),
            ]
        )

        self.assertEqual(scheduled[0].url, "https://example.com/careers")
        self.assertEqual(len(scheduled), 2)

    def test_target_region_generated_path_represents_family_inside_fetch_window(self):
        localized = candidate(
            "https://example.com/en-us/careers",
            200,
            [
                "generated path probe",
                "matches target location region 'us'",
            ],
            origin="path_probe",
        )
        locale_free = candidate(
            "https://example.com/careers",
            600,
            ["generated path probe"],
            origin="path_probe",
        )

        scheduled, trace = schedule_career_candidates(
            [locale_free, localized],
            fetch_limit=1,
        )

        self.assertEqual(scheduled[0].url, localized.url)
        self.assertEqual(trace["roles_by_url"][localized.url], "representative")
        self.assertEqual(trace["roles_by_url"][locale_free.url], "locale_alias")

    def test_speculative_truncation_does_not_report_fetch_budget_exhaustion(self):
        fetcher = RecordingFailureFetcher()
        agent = JobSourceAgent(
            fetcher,
            max_candidates=4,
            max_career_candidate_fetches=2,
        )
        trace = {"candidate_fetch_errors": []}
        candidates = [
            candidate("https://www.example.com/careers", 500, ["generated path probe"]),
            candidate("https://example.com/en/careers", 490, ["generated path probe"]),
            candidate("https://example.com/careers", 300, ["generated path probe"]),
            candidate("https://example.com/jobs", 290, ["generated path probe"]),
        ]

        selected = agent._select_verified_career_candidate(candidates, trace)

        self.assertIsNone(selected)
        self.assertEqual(
            fetcher.calls,
            ["https://example.com/careers", "https://example.com/jobs"],
        )
        self.assertNotIn("candidate_fetch_budget_exhausted", trace)
        self.assertEqual(trace["candidate_schedule"]["bounded_count"], 2)

    def test_evidence_backed_candidate_outside_fetch_set_reports_exhaustion(self):
        fetcher = RecordingFailureFetcher()
        agent = JobSourceAgent(
            fetcher,
            max_candidates=6,
            max_career_candidate_fetches=1,
        )
        trace = {"candidate_fetch_errors": []}
        candidates = [
            candidate(
                "https://example.com/careers",
                500,
                ["generated path probe"],
            ),
            candidate(
                "https://example.com/team",
                100,
                [
                    "homepage navigation link",
                    "homepage team link requiring employment evidence",
                ],
                text="Team",
                origin="page_link",
            ),
            candidate(
                "https://example.com/jobs-from-sitemap",
                90,
                [],
                origin="sitemap",
            ),
        ]

        selected = agent._select_verified_career_candidate(candidates, trace)

        self.assertIsNone(selected)
        self.assertEqual(fetcher.calls, ["https://example.com/team"])
        self.assertEqual(
            trace["candidate_fetch_budget_exhausted"],
            {
                "limit": 1,
                "remaining_candidates": 2,
                "remaining_bounded_candidates": 0,
                "untried_evidence_backed_count": 1,
            },
        )

    def test_later_speculative_schedule_preserves_untried_evidence_exhaustion(self):
        fetcher = RecordingFailureFetcher()
        agent = JobSourceAgent(
            fetcher,
            max_candidates=6,
            max_career_candidate_fetches=1,
        )
        trace = {"candidate_fetch_errors": []}
        evidence_candidates = [
            candidate(
                f"https://example.com/careers-{index}",
                100 - index,
                ["career keyword 'careers'"],
                text="Careers",
                origin="page_link",
            )
            for index in range(2)
        ]

        agent._select_verified_career_candidate(evidence_candidates, trace)
        self.assertIn("candidate_fetch_budget_exhausted", trace)

        agent._select_verified_career_candidate(
            [candidate("https://example.com/jobs", 100, ["generated path probe"])],
            trace,
        )

        self.assertEqual(
            trace["candidate_fetch_budget_exhausted"]["untried_evidence_backed_count"],
            1,
        )

    def test_generated_subdomain_probe_stays_in_speculative_tier(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        scheduled, _trace = schedule(
            agent,
            [
                candidate(
                    "https://careers.example.com",
                    800,
                    [],
                    origin="subdomain_probe",
                ),
                candidate(
                    "https://example.com/jobs-from-sitemap",
                    100,
                    [],
                    origin="sitemap",
                ),
            ]
        )

        self.assertEqual(scheduled[0].origin, "sitemap")

    def test_unrelated_homepage_navigation_does_not_outrank_career_probes(self):
        agent = JobSourceAgent(Fetcher(offline=True))

        scheduled, _trace = schedule(
            agent,
            [
                candidate(
                    "https://example.com/projects",
                    110,
                    ["homepage navigation link"],
                    origin="page_link",
                ),
                candidate(
                    "https://example.com/careers",
                    100,
                    ["generated path probe"],
                    origin="path_probe",
                ),
            ]
        )

        self.assertEqual(scheduled[0].url, "https://example.com/careers")

    def test_five_fetch_schedule_reserves_one_concrete_host_fallback(self):
        agent = JobSourceAgent(Fetcher(offline=True), max_career_candidate_fetches=5)
        candidates = []
        for index, route in enumerate(("careers", "career", "jobs", "open-positions", "opportunities")):
            score = 500 - index
            candidates.extend(
                [
                    candidate(f"https://example.com/{route}", score, ["generated path probe"]),
                    candidate(f"https://www.example.com/{route}", score - 1, ["generated path probe"]),
                ]
            )

        scheduled, trace = schedule(agent, candidates)

        self.assertEqual(scheduled[4].url, "https://www.example.com/careers")
        self.assertEqual(trace["reserved_host_fallback"], scheduled[4].url)
        self.assertEqual(trace["roles_by_url"][scheduled[4].url], "reserved_host_fallback")

    def test_five_fetch_schedule_reserves_career_subdomain_after_path_guesses(self):
        guesses = [
            candidate(
                f"https://www.lacoste.com/us/{route}",
                500 - index,
                ["generated path probe"],
                origin="path_probe",
            )
            for index, route in enumerate(
                ("company/careers", "careers", "about/careers", "careers/jobs", "jobs")
            )
        ]
        careers_subdomain = candidate(
            "https://careers.lacoste.com",
            195,
            ["career keyword 'careers'"],
            origin="subdomain_probe",
            source_url="https://www.lacoste.com/us/",
        )

        scheduled, trace = schedule_career_candidates(
            guesses + [careers_subdomain],
            fetch_limit=5,
        )

        self.assertIn(careers_subdomain.url, [item.url for item in scheduled[:5]])
        self.assertEqual(trace["reserved_subdomain_probe"], careers_subdomain.url)
        self.assertEqual(
            trace["roles_by_url"][careers_subdomain.url],
            "reserved_subdomain_probe",
        )

    def test_target_region_same_site_gateway_keeps_one_bounded_traversal_slot(self):
        gateway = candidate(
            "https://us.caudalie.com/",
            50,
            ["matches target location region 'us'"],
            text="United States",
            origin="page_link",
            source_url="https://caudalie.com/en-fr",
        )
        guesses = [
            candidate(
                f"https://caudalie.com/{route}",
                500 - index,
                ["generated path probe"],
                origin="path_probe",
            )
            for index, route in enumerate(("careers", "jobs", "open-positions"))
        ]

        scheduled, trace = schedule_career_candidates(
            guesses + [gateway],
            fetch_limit=3,
        )

        self.assertIn(gateway.url, [item.url for item in scheduled[:3]])
        self.assertEqual(trace["reserved_regional_gateway"], gateway.url)
        self.assertEqual(trace["roles_by_url"][gateway.url], "reserved_regional_gateway")
        self.assertGreater(candidate_evidence_tier(gateway), 1)

    def test_target_region_gateway_is_marked_when_already_inside_fetch_window(self):
        gateway = candidate(
            "https://us.caudalie.com/",
            230,
            ["matches target location region 'us'"],
            text="United States",
            origin="page_link",
            source_url="https://caudalie.com/en-fr",
        )

        scheduled, trace = schedule_career_candidates(
            [
                gateway,
                candidate(
                    "https://caudalie.com/careers",
                    100,
                    ["generated path probe"],
                    origin="path_probe",
                ),
            ],
            fetch_limit=3,
        )

        self.assertIn(gateway.url, [item.url for item in scheduled[:3]])
        self.assertEqual(
            trace["roles_by_url"][gateway.url],
            "reserved_regional_gateway",
        )

    def test_regional_gateway_reservation_rejects_cross_site_conflicting_or_selector_links(self):
        guesses = [
            candidate(
                f"https://caudalie.com/{route}",
                500 - index,
                ["generated path probe"],
                origin="path_probe",
            )
            for index, route in enumerate(("careers", "jobs", "open-positions"))
        ]
        ineligible = [
            candidate(
                "https://us.unrelated.example/",
                50,
                ["matches target location region 'us'"],
                origin="page_link",
                source_url="https://caudalie.com/en-fr",
            ),
            candidate(
                "https://fr.caudalie.com/",
                50,
                [
                    "matches target location region 'us'",
                    "conflicts with target location region 'us': 'fr'",
                ],
                origin="page_link",
                source_url="https://caudalie.com/en-fr",
            ),
            candidate(
                "https://caudalie.com/country-selector",
                50,
                ["matches target location region 'us'"],
                origin="page_link",
                source_url="https://caudalie.com/en-fr",
            ),
            candidate(
                "https://us.caudalie.com/",
                50,
                [],
                origin="page_link",
                source_url="https://caudalie.com/en-fr",
            ),
        ]

        scheduled, trace = schedule_career_candidates(
            guesses + ineligible,
            fetch_limit=3,
        )

        self.assertEqual([item.url for item in scheduled[:3]], [item.url for item in guesses])
        self.assertIsNone(trace["reserved_regional_gateway"])

    def test_two_letter_product_route_is_not_treated_as_locale(self):
        agent = JobSourceAgent(Fetcher(offline=True))
        product_route = candidate(
            "https://example.com/go/jobs",
            200,
            ["generated path probe"],
        )

        self.assertEqual(candidate_route_family(product_route), "go/jobs")
        self.assertIsNone(candidate_locale_key(product_route.url))

    def test_host_family_normalizes_case_trailing_dot_and_idna(self):
        international = candidate(
            "https://WWW.Example.COM./careers",
            200,
            ["generated path probe"],
        )

        self.assertEqual(candidate_concrete_host(international.url), "www.example.com")
        self.assertEqual(candidate_host_family(international), "example.com")


if __name__ == "__main__":
    unittest.main()
