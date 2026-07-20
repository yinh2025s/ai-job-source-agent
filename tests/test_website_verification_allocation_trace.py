import unittest

from job_source_agent.website_resolver import (
    WebsiteCandidate,
    _allocate_verification_slots,
)


def _candidate(domain: str, score: int) -> WebsiteCandidate:
    return WebsiteCandidate(f"https://{domain}/", score)


class WebsiteVerificationAllocationTraceTests(unittest.TestCase):
    def test_source_reservations_preserve_existing_selection_order(self):
        preferred = _candidate("preferred.example", 10)
        official = _candidate("official.example", 95)
        search = _candidate("search.example", 90)
        slug = _candidate("slug.example", 85)
        score_fill = _candidate("score-fill.example", 80)
        scored = [official, search, slug, score_fill, preferred]
        sources = {
            "preferred.example": {"preferred_input"},
            "official.example": {"linkedin_official_website"},
            "search.example": {"search_evidence"},
            "slug.example": {"linkedin_slug"},
        }
        trace: list[dict] = []

        selected = _allocate_verification_slots(
            scored,
            5,
            sources,
            decision_trace=trace,
            phase="contract",
        )

        self.assertEqual(
            [candidate.url for candidate in selected],
            [
                preferred.url,
                official.url,
                search.url,
                slug.url,
                score_fill.url,
            ],
        )
        self.assertEqual(
            [entry["reason"] for entry in trace[0]["selected"]],
            [
                "source_reservation:preferred_input",
                "source_reservation:linkedin_official_website",
                "source_reservation:search_evidence",
                "source_reservation:linkedin_slug",
                "score_fill",
            ],
        )

    def test_trace_records_selected_and_bounded_excluded_candidates(self):
        selected = _candidate("selected.example", 100)
        excluded = [_candidate(f"excluded-{index}.example", 99 - index) for index in range(51)]
        trace: list[dict] = []

        result = _allocate_verification_slots(
            [selected, *excluded],
            1,
            {"selected.example": {"preferred_input"}},
            decision_trace=trace,
            phase="candidate_portfolio",
        )

        self.assertEqual(result, [selected])
        self.assertEqual(len(trace), 1)
        allocation = trace[0]
        self.assertEqual(allocation["phase"], "candidate_portfolio")
        self.assertEqual(allocation["verify_limit"], 1)
        self.assertEqual(allocation["candidate_count"], 52)
        self.assertEqual(allocation["selected"][0]["url"], selected.url)
        self.assertEqual(
            allocation["selected"][0]["reason"],
            "source_reservation:preferred_input",
        )
        self.assertEqual(len(allocation["excluded"]), 50)
        self.assertEqual(allocation["excluded_count"], 51)
        self.assertTrue(allocation["excluded_truncated"])
        self.assertTrue(
            all(item["reason"] == "slot_limit_reached" for item in allocation["excluded"])
        )

    def test_zero_verify_limit_has_no_selection_and_records_disabled_candidates(self):
        candidate = _candidate("preferred.example", 100)
        trace: list[dict] = []

        selected = _allocate_verification_slots(
            [candidate],
            0,
            {"preferred.example": {"preferred_input"}},
            decision_trace=trace,
        )

        self.assertEqual(selected, [])
        self.assertEqual(trace[0]["verify_limit"], 0)
        self.assertEqual(trace[0]["selected"], [])
        self.assertEqual(trace[0]["excluded_count"], 1)
        self.assertEqual(trace[0]["excluded"][0]["reason"], "verification_disabled")

    def test_low_score_unrelated_candidate_does_not_displace_existing_reservations(self):
        preferred = _candidate("preferred.example", 10)
        search = _candidate("search.example", 90)
        unrelated = _candidate("unrelated.example", 1)
        trace: list[dict] = []

        selected = _allocate_verification_slots(
            [search, preferred, unrelated],
            2,
            {
                "preferred.example": {"preferred_input"},
                "search.example": {"search_evidence"},
            },
            decision_trace=trace,
        )

        self.assertEqual([candidate.url for candidate in selected], [preferred.url, search.url])
        self.assertEqual(trace[0]["excluded_count"], 1)
        self.assertEqual(trace[0]["excluded"][0]["url"], unrelated.url)
        self.assertEqual(trace[0]["excluded"][0]["reason"], "slot_limit_reached")


if __name__ == "__main__":
    unittest.main()
