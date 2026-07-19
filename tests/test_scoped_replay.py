import unittest

from job_source_agent.browser_interaction import JobSearchInteraction
from job_source_agent.evidence_scope import EvidenceScopeRef
from job_source_agent.outcome_tape import (
    OFFLINE_TAPE_DIVERGENCE,
    OutcomeTape,
    PageOutcomeTapeEntry,
    outcome_records_sha256,
)
from job_source_agent.request_identity import build_request_identity
from job_source_agent.scoped_replay import ScopedReplayController
from job_source_agent.web import FetchError


class ScopedReplayControllerTests(unittest.TestCase):
    execution_fingerprint = "a" * 64

    def tape(self, stage, url, *, attempt, interaction=None, count=1):
        entries = [
            PageOutcomeTapeEntry(
                snapshot_store_id="snapshot-store-replay",
                scope_id=("b" if stage == "career_discovery" else "c") * 64,
                capture_attempt_id=attempt,
                execution_fingerprint=self.execution_fingerprint,
                stage=stage,
                request_ordinal=ordinal,
                request=build_request_identity(url, interaction=interaction),
                page_url=url,
                html=f"<p>{stage}</p>",
                final_url=url,
            )
            for ordinal in range(1, count + 1)
        ]
        entry = entries[0]
        scope = EvidenceScopeRef(
            snapshot_store_id=entry.snapshot_store_id,
            scope_id=entry.scope_id,
            capture_attempt_id=attempt,
            execution_fingerprint=self.execution_fingerprint,
            stage=stage,
            request_count=count,
            records_sha256=outcome_records_sha256(entries),
            first_sequence=1,
            last_sequence=count,
        )
        return OutcomeTape(scope, entries)

    def test_forwards_forced_render_capability_for_active_tape(self):
        url = "https://www.acorns.com/career-opportunities"
        controller = ScopedReplayController(
            {
                "career_discovery": self.tape(
                    "career_discovery",
                    url,
                    attempt="capture-attempt-old",
                    count=2,
                )
            },
            execution_fingerprint=self.execution_fingerprint,
        )

        self.assertFalse(controller.supports_forced_render)
        controller.begin_stage(
            "attempt-current-001",
            self.execution_fingerprint,
            "career_discovery",
        )
        self.assertFalse(controller.supports_forced_render)
        controller.fetch(url)
        self.assertTrue(controller.supports_forced_render)
        controller.fetch(url)
        self.assertFalse(controller.supports_forced_render)
        controller.finalize()
        self.assertFalse(controller.supports_forced_render)

    def test_stage_boundaries_select_exact_tape_and_preserve_source_scope(self):
        career_url = "https://example.com/careers"
        board_url = "https://example.com/jobs"
        controller = ScopedReplayController(
            {
                "career_discovery": self.tape(
                    "career_discovery",
                    career_url,
                    attempt="capture-attempt-old",
                ),
                "job_board_discovery": self.tape(
                    "job_board_discovery",
                    board_url,
                    attempt="capture-attempt-new",
                ),
            },
            execution_fingerprint=self.execution_fingerprint,
        )

        controller.begin_stage(
            "ignored-current-attempt",
            self.execution_fingerprint,
            "career_discovery",
        )
        self.assertEqual(controller.fetch(career_url).url, career_url)
        self.assertEqual(
            controller.finalize().capture_attempt_id,
            "capture-attempt-old",
        )
        controller.begin_stage(
            "ignored-current-attempt",
            self.execution_fingerprint,
            "job_board_discovery",
        )
        self.assertEqual(controller.fetch(board_url).url, board_url)
        controller.finalize()
        controller.assert_all_consumed()

    def test_cross_stage_request_and_unconsumed_plan_fail_closed(self):
        url = "https://example.com/careers"
        controller = ScopedReplayController(
            {
                "career_discovery": self.tape(
                    "career_discovery",
                    url,
                    attempt="capture-attempt-old",
                )
            },
            execution_fingerprint=self.execution_fingerprint,
        )
        with self.assertRaises(FetchError) as raised:
            controller.fetch(url)
        self.assertEqual(raised.exception.reason_code, OFFLINE_TAPE_DIVERGENCE)
        with self.assertRaises(FetchError):
            controller.assert_all_consumed()

    def test_missing_stage_wrong_execution_and_extra_fetch_fail_closed(self):
        url = "https://example.com/careers"
        controller = ScopedReplayController(
            {
                "career_discovery": self.tape(
                    "career_discovery",
                    url,
                    attempt="capture-attempt-old",
                )
            },
            execution_fingerprint=self.execution_fingerprint,
        )
        with self.assertRaises(FetchError):
            controller.begin_stage("attempt-current-001", "d" * 64, "career_discovery")
        controller.begin_stage(
            "attempt-current-001",
            self.execution_fingerprint,
            "career_discovery",
        )
        controller.fetch(url)
        with self.assertRaises(FetchError):
            controller.fetch(url)
        controller.abort_stage()
        with self.assertRaises(FetchError):
            controller.begin_stage(
                "attempt-current-001",
                self.execution_fingerprint,
                "opening_match",
            )

    def test_forwards_interaction_to_stage_tape_identity(self):
        url = "https://example.com/careers"
        interaction = JobSearchInteraction(
            form_ordinal=0,
            query_name="q",
            target_title="Secret Staff Engineer",
            submit_text="Search",
        )
        controller = ScopedReplayController(
            {
                "career_discovery": self.tape(
                    "career_discovery",
                    url,
                    attempt="capture-attempt-old",
                    interaction=interaction,
                )
            },
            execution_fingerprint=self.execution_fingerprint,
        )
        controller.begin_stage(
            "attempt-current-001",
            self.execution_fingerprint,
            "career_discovery",
        )

        with self.assertRaises(FetchError):
            controller.fetch(url)
        self.assertEqual(
            controller.fetch(url, interaction=interaction).html,
            "<p>career_discovery</p>",
        )
        controller.finalize()


if __name__ == "__main__":
    unittest.main()
