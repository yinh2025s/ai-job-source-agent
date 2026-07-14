import dataclasses
import json
import unittest

from job_source_agent.evidence_scope import (
    EMPTY_RECORDS_SHA256,
    EvidenceScopeRef,
    StageEvidenceLineage,
)
from job_source_agent.replay_record_plan import build_replay_record_plans


EXECUTION_A = "a" * 64
EXECUTION_B = "b" * 64


def _lineage(
    stage="linkedin_discovery",
    execution_fingerprint=EXECUTION_A,
    scoped=True,
):
    scope = None
    if scoped:
        scope = EvidenceScopeRef(
            snapshot_store_id="snapshot-store-0001",
            scope_id="c" * 64,
            capture_attempt_id="capture-attempt-0001",
            execution_fingerprint=execution_fingerprint,
            stage=stage,
            request_count=0,
            records_sha256=EMPTY_RECORDS_SHA256,
        )
    return dataclasses.asdict(
        StageEvidenceLineage(
            stage=stage,
            execution_fingerprint=execution_fingerprint,
            producer_attempt_id="capture-attempt-0001",
            snapshot_scope=scope,
        )
    )


def _replay(company_name="Example Corp", **overrides):
    return {
        "company_name": company_name,
        "company_website_url": "https://example.test/private-path",
        "job_title": "AI Engineer",
        **overrides,
    }


class ReplayRecordPlanTests(unittest.TestCase):
    def test_duplicate_inputs_have_isolated_occurrence_ids(self):
        replay = _replay()

        plans = build_replay_record_plans([{}, {}], [replay, dict(replay)])

        self.assertEqual([plan.source_ordinal for plan in plans], [1, 2])
        self.assertNotEqual(plans[0].record_id, plans[1].record_id)
        self.assertEqual({plan.evidence_mode for plan in plans}, {"legacy_global_latest"})

    def test_reversed_source_order_changes_execution_bound_ids(self):
        sources = [
            {"execution_fingerprint": EXECUTION_A},
            {"execution_fingerprint": EXECUTION_B},
        ]
        replay_records = [_replay(), _replay()]

        forward = build_replay_record_plans(sources, replay_records)
        reversed_plans = build_replay_record_plans(list(reversed(sources)), replay_records)

        self.assertNotEqual(
            [plan.record_id for plan in forward],
            [plan.record_id for plan in reversed_plans],
        )

    def test_extracts_scoped_lineage_from_top_level_and_nested_trace(self):
        first = _lineage()
        second = _lineage(stage="website_resolution")
        sources = [
            {"stage_evidence_lineage": [first, second]},
            {"trace": {"stage_evidence_lineage": [first]}},
        ]

        plans = build_replay_record_plans(sources, [_replay("First"), _replay("Second")])

        self.assertEqual([plan.evidence_mode for plan in plans], ["scoped_outcome_tape"] * 2)
        self.assertEqual(
            [item.stage for item in plans[0].stage_evidence_lineage],
            ["linkedin_discovery", "website_resolution"],
        )
        self.assertEqual(
            plans[0].scope_for_stage("website_resolution"),
            plans[0].stage_evidence_lineage[1].snapshot_scope,
        )
        self.assertIsNone(plans[1].scope_for_stage("opening_match"))

    def test_absent_empty_and_all_null_lineage_are_legacy(self):
        sources = [
            {},
            {"stage_evidence_lineage": []},
            {"stage_evidence_lineage": [_lineage(scoped=False)]},
        ]

        plans = build_replay_record_plans(
            sources,
            [_replay("Absent"), _replay("Empty"), _replay("Null")],
        )

        self.assertEqual({plan.evidence_mode for plan in plans}, {"legacy_global_latest"})

    def test_rejects_mixed_and_partially_scoped_selection(self):
        with self.assertRaisesRegex(ValueError, "cannot be mixed"):
            build_replay_record_plans(
                [{"stage_evidence_lineage": [_lineage()]}, {}],
                [_replay("Scoped"), _replay("Legacy")],
            )

        with self.assertRaisesRegex(ValueError, "partially scoped"):
            build_replay_record_plans(
                [{
                    "stage_evidence_lineage": [
                        _lineage(),
                        _lineage("website_resolution", scoped=False),
                    ]
                }],
                [_replay()],
            )

    def test_rejects_wrong_order_duplicates_unknown_fields_and_multiple_executions(self):
        cases = [
            [_lineage("website_resolution"), _lineage("linkedin_discovery")],
            [_lineage(), _lineage()],
            [{**_lineage(), "raw_html": "private"}],
            [_lineage(), _lineage("website_resolution", EXECUTION_B)],
        ]
        for lineage in cases:
            with self.subTest(lineage=lineage), self.assertRaises(ValueError):
                build_replay_record_plans(
                    [{"stage_evidence_lineage": lineage}],
                    [_replay()],
                )

    def test_rejects_source_and_lineage_execution_fingerprint_conflict(self):
        with self.assertRaisesRegex(ValueError, "fingerprints do not match"):
            build_replay_record_plans(
                [{
                    "execution_fingerprint": EXECUTION_B,
                    "stage_evidence_lineage": [_lineage()],
                }],
                [_replay()],
            )

    def test_rejects_count_mismatch(self):
        with self.assertRaisesRegex(ValueError, "counts do not match"):
            build_replay_record_plans([{}], [])

    def test_record_ids_are_stable_and_bind_normalized_replay_identity(self):
        source = {"execution_fingerprint": EXECUTION_A}
        first = _replay(company_name="  Example   Corp ")
        normalized = _replay(company_name="Example Corp")

        first_plan = build_replay_record_plans([source], [first])[0]
        repeated_plan = build_replay_record_plans([source], [first])[0]
        normalized_plan = build_replay_record_plans([source], [normalized])[0]
        changed_plan = build_replay_record_plans([source], [_replay("Changed Corp")])[0]

        self.assertEqual(first_plan.record_id, repeated_plan.record_id)
        self.assertEqual(first_plan.record_id, normalized_plan.record_id)
        self.assertNotEqual(first_plan.record_id, changed_plan.record_id)
        self.assertRegex(first_plan.record_id, r"^[0-9a-f]{64}$")

    def test_record_plan_output_is_privacy_safe(self):
        replay = _replay(
            linkedin_job_url="https://linkedin.example/jobs/private-123",
            source_trace={"cookies": "secret-cookie", "html": "<main>private</main>"},
        )
        source = {
            "execution_fingerprint": EXECUTION_A,
            "trace": {"diagnostic": "authenticated private trace"},
        }

        plan = build_replay_record_plans([source], [replay])[0]
        serialized = json.dumps(dataclasses.asdict(plan))

        self.assertEqual(set(dataclasses.asdict(plan)), {
            "source_ordinal",
            "record_id",
            "evidence_mode",
            "stage_evidence_lineage",
        })
        for private_value in ("private-123", "secret-cookie", "<main>", "authenticated"):
            self.assertNotIn(private_value, serialized)

    def test_plan_is_immutable_and_scope_lookup_rejects_unknown_stage(self):
        plan = build_replay_record_plans(
            [{"stage_evidence_lineage": [_lineage()]}],
            [_replay()],
        )[0]

        with self.assertRaises(dataclasses.FrozenInstanceError):
            plan.record_id = "0" * 64
        with self.assertRaises(ValueError):
            plan.scope_for_stage("unknown")


if __name__ == "__main__":
    unittest.main()
