from __future__ import annotations

import math
import unittest
from dataclasses import FrozenInstanceError

from job_source_agent.candidate_reasoning_contracts import (
    CandidateEvidence,
    CandidateRankerDecision,
    CandidateRankerRequest,
    LLMAdvisoryFailure,
    LLMDecisionKey,
    LLMDecisionRecord,
    QueryPlannerDecision,
    QueryPlannerRequest,
    RankedCandidate,
    SearchQuerySpec,
    StructuredLLMRequest,
    TokenUsage,
)


class CandidateReasoningContractsTest(unittest.TestCase):
    def test_planner_payload_is_strict_bounded_and_immutable(self):
        decision = QueryPlannerDecision.from_payload(
            {
                "schema_version": "1",
                "normalized_company_name": "Example Labs",
                "core_brand_tokens": ["Example"],
                "legal_or_descriptive_suffixes": ["Labs"],
                "possible_aliases": ["Example Research"],
                "queries": [
                    {"query": '"Example Labs" careers', "purpose": "career_site"}
                ],
                "ambiguous": False,
                "reason_codes": ["DESCRIPTIVE_SUFFIX"],
            }
        )
        self.assertIsInstance(decision.queries, tuple)
        with self.assertRaises(FrozenInstanceError):
            decision.ambiguous = True
        with self.assertRaises(ValueError):
            QueryPlannerDecision.from_payload(
                {
                    "schema_version": "1",
                    "normalized_company_name": "Example Labs",
                    "core_brand_tokens": ["Example"],
                    "legal_or_descriptive_suffixes": [],
                    "possible_aliases": [],
                    "queries": [],
                    "ambiguous": False,
                    "reason_codes": [],
                    "extra": True,
                }
            )

    def test_planner_rejects_urls_and_more_than_three_queries(self):
        with self.assertRaises(ValueError):
            SearchQuerySpec("https://example.invalid", "official_website")
        with self.assertRaises(ValueError):
            QueryPlannerDecision(
                "Example Labs",
                ("Example",),
                (),
                (),
                tuple(SearchQuerySpec(f"Example query {i}", "official_website") for i in range(4)),
                False,
                (),
            )
        with self.assertRaises(ValueError):
            QueryPlannerDecision(
                "https://company.example.invalid",
                ("Example",),
                (),
                (),
                (),
                False,
                (),
            )

    def test_ranker_rejects_unknown_duplicate_or_omitted_ids(self):
        request = self._ranker_request()
        payload = {
            "schema_version": "1",
            "ranked_candidates": [
                {
                    "candidate_id": "candidate-1",
                    "confidence_bucket": "high",
                    "evidence_ids": ["candidate-1"],
                    "reason_codes": ["BRAND_MATCH"],
                },
                {
                    "candidate_id": "candidate-2",
                    "confidence_bucket": "medium",
                    "evidence_ids": ["candidate-2"],
                    "reason_codes": ["LOCATION_MATCH"],
                },
            ],
            "ambiguous": False,
        }
        self.assertEqual(len(CandidateRankerDecision.from_payload(payload, request).ranked_candidates), 2)
        payload["ranked_candidates"][1]["candidate_id"] = "unknown"
        with self.assertRaises(ValueError):
            CandidateRankerDecision.from_payload(payload, request)
        payload["ranked_candidates"][1]["candidate_id"] = "candidate-1"
        with self.assertRaises(ValueError):
            CandidateRankerDecision.from_payload(payload, request)

    def test_candidate_evidence_is_search_only_and_ranker_is_limited(self):
        with self.assertRaises(ValueError):
            CandidateEvidence("c", "http://example.invalid", "", "", "bing", "q", 1)
        for unsafe_url in (
            "https://127.0.0.1/careers",
            "https://company.example.invalid:8443/careers",
            "https://company.example.invalid/careers?token=secret",
            "https://user:secret@company.example.invalid/careers",
        ):
            with self.subTest(unsafe_url=unsafe_url), self.assertRaises(ValueError):
                CandidateEvidence("c", unsafe_url, "", "", "bing", "q", 1)
        with self.assertRaises(ValueError):
            CandidateRankerRequest(
                "Example Labs",
                None,
                None,
                tuple(
                    CandidateEvidence(
                        f"c{i}",
                        f"https://company{i}.example.invalid",
                        "",
                        "",
                        "bing",
                        "q",
                        1,
                    )
                    for i in range(11)
                ),
            )

    def test_structured_payload_is_deeply_immutable_and_forbids_sensitive_fields(self):
        request = StructuredLLMRequest("query_plan", "planner_v1", {"company": {"name": "Example"}})
        with self.assertRaises(TypeError):
            request.payload["new"] = "value"
        with self.assertRaises(TypeError):
            request.payload["company"]["name"] = "Changed"
        with self.assertRaises(ValueError):
            StructuredLLMRequest("query_plan", "planner_v1", {"chain_of_thought": "hidden"})

    def test_planner_and_ranker_inputs_reject_sensitive_public_text(self):
        for value in (
            "contact person@example.com",
            "Authorization: Bearer secret",
            "/Users/example/private.txt",
            "call +1 (206) 555-0123",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                QueryPlannerRequest(
                    "Example Labs",
                    "example-labs",
                    value,
                    "AI Engineer",
                    "Seattle, WA",
                    "Software",
                    "Seattle, WA",
                )
            with self.subTest(value=value), self.assertRaises(ValueError):
                CandidateEvidence(
                    "candidate-1",
                    "https://one.example.invalid",
                    "Example",
                    value,
                    "bing",
                    "q1",
                    1,
                )

    def test_decision_record_validates_digests_usage_and_numbers(self):
        digest = "a" * 64
        key = LLMDecisionKey("candidate_rank", digest, digest, "provider", "model-1", "prompt-1", "1", "adapter-1")
        record = LLMDecisionRecord(
            digest,
            digest,
            key,
            {"company": "Example Labs"},
            {"candidate_ids": ["candidate-1"]},
            ("candidate-1",),
            ("q1",),
            digest,
            12.5,
            TokenUsage(3, 2, 5),
            1.0,
            "success",
            None,
        )
        self.assertEqual(record.status, "success")
        with self.assertRaises(ValueError):
            TokenUsage(1, 1, 3)
        with self.assertRaises(ValueError):
            LLMDecisionRecord(
                digest, digest, key, {}, {}, (), (), digest, math.inf,
                TokenUsage(0, 0, 0), 1.0, "failure", "TIMEOUT",
            )

    def test_advisory_failure_is_typed_and_bounded(self):
        failure = LLMAdvisoryFailure("TIMEOUT", "query_plan", "deadline reached")
        self.assertEqual(failure.code, "TIMEOUT")
        with self.assertRaises(ValueError):
            LLMAdvisoryFailure("PIPELINE_FAILURE", "query_plan")
        with self.assertRaises(ValueError):
            LLMAdvisoryFailure("TIMEOUT", "query_plan", "x" * 301)

    @staticmethod
    def _ranker_request() -> CandidateRankerRequest:
        return CandidateRankerRequest(
            "Example Labs",
            "Software",
            "Seattle, WA",
            (
                CandidateEvidence(
                    "candidate-1",
                    "https://one.example.invalid",
                    "One",
                    "One",
                    "bing",
                    "q1",
                    1,
                ),
                CandidateEvidence(
                    "candidate-2",
                    "https://two.example.invalid",
                    "Two",
                    "Two",
                    "bing",
                    "q1",
                    2,
                ),
            ),
        )


if __name__ == "__main__":
    unittest.main()
