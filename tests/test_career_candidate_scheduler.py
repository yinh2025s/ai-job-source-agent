import unittest

from job_source_agent.career_candidate_scheduler import (
    candidate_concrete_host,
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
                    ["career keyword 'careers'"],
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
        self.assertEqual(trace["version"], "2")

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
