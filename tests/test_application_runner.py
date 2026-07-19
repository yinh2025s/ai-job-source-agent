import tempfile
import unittest
from unittest.mock import patch

from job_source_agent.application_runner import ApplicationRunner
from job_source_agent.checkpoint_prefix import (
    CheckpointPrefixError,
    CheckpointPrefixInspection,
    inspect_finalized_checkpoint_prefix,
    inspect_checkpoint_prefix,
)
from job_source_agent.contracts import PipelineContext, StageExecution
from job_source_agent.models import (
    PIPELINE_STAGES,
    STAGE_CAREER_DISCOVERY,
    STAGE_HIRING_IDENTITY_RESOLUTION,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
    STAGE_RESULT_VALIDATION,
    STAGE_LINKEDIN_DISCOVERY,
    STAGE_WEBSITE_RESOLUTION,
    CompanyInput,
    StageResult,
)
from job_source_agent.stage_checkpoint import FilesystemCheckpointStore
from job_source_agent.identity_continuity import (
    HiringIdentityEvidence,
    OpeningIdentity,
    ProviderIdentity,
)


REQUIRED_OUTPUTS = {
    STAGE_WEBSITE_RESOLUTION: ("company_website_url", "https://acme.example"),
    STAGE_CAREER_DISCOVERY: ("career_page_url", "https://acme.example/careers"),
    STAGE_JOB_BOARD_DISCOVERY: ("job_list_page_url", "https://jobs.acme.example"),
    STAGE_OPENING_MATCH: ("open_position_url", "https://jobs.acme.example/123"),
}


def authoritative_execution(stage, *, status=None, updates=None):
    if status is None:
        status = "not_applicable" if stage == STAGE_LINKEDIN_DISCOVERY else "success"
    if updates is None:
        required = REQUIRED_OUTPUTS.get(stage)
        updates = {required[0]: required[1]} if required else {}
        if stage == STAGE_HIRING_IDENTITY_RESOLUTION:
            updates["hiring_identity_evidence"] = HiringIdentityEvidence(
                source_company_name="Acme",
                hiring_entity_name="Acme",
                relationship_type="same_entity",
                verification_method="same_entity",
                verified=True,
                evidence_url="https://acme.example/careers",
            )
        elif stage == STAGE_JOB_BOARD_DISCOVERY:
            updates["provider_identity"] = ProviderIdentity(
                hiring_entity_name="Acme",
                provider="generic",
                tenant="url:https://jobs.acme.example",
                canonical_board_url="https://jobs.acme.example",
                evidence_url="https://jobs.acme.example",
                verification_method="first_party_same_site",
                relationship_verified=True,
            )
        elif stage == STAGE_OPENING_MATCH:
            updates["opening_identity"] = OpeningIdentity(
                hiring_entity_name="Acme",
                provider="generic",
                tenant="url:https://jobs.acme.example",
                canonical_board_url="https://jobs.acme.example",
                canonical_opening_url="https://jobs.acme.example/123",
            )
    return StageExecution(
        StageResult(stage=stage, status=status),
        updates=updates,
        trace={"authoritative": True},
    )


def seed_prefix(store, fingerprint, boundary):
    for stage in PIPELINE_STAGES[: PIPELINE_STAGES.index(boundary)]:
        store.executions[(fingerprint, stage)] = authoritative_execution(stage)


class RecordingStage:
    def __init__(self, name, calls, updates=None):
        self.name = name
        self.calls = calls
        self.updates = updates or {}

    def run(self, context):
        self.calls.append(self.name)
        return StageExecution(
            StageResult(stage=self.name, status="success"),
            updates=self.updates,
            trace={"executed": True},
        )


class MemoryCheckpointStore:
    def __init__(self):
        self.executions = {}
        self.loaded = []
        self.saved = []
        self.invalidated = []

    def load(self, input_fingerprint, stage):
        self.loaded.append((input_fingerprint, stage))
        return self.executions.get((input_fingerprint, stage))

    def save(self, input_fingerprint, execution):
        self.saved.append((input_fingerprint, execution))
        self.executions[(input_fingerprint, execution.result.stage)] = execution

    def invalidate_from(self, input_fingerprint, stage):
        self.invalidated.append((input_fingerprint, stage))
        start_index = PIPELINE_STAGES.index(stage)
        for invalidated_stage in PIPELINE_STAGES[start_index:]:
            self.executions.pop((input_fingerprint, invalidated_stage), None)


class ApplicationRunnerTests(unittest.TestCase):
    def test_same_attempt_continuation_preserves_finalized_negative_prefix(self):
        fingerprint = "input-v1"
        store = MemoryCheckpointStore()
        store.executions[(fingerprint, STAGE_LINKEDIN_DISCOVERY)] = (
            authoritative_execution(STAGE_LINKEDIN_DISCOVERY)
        )
        store.executions[(fingerprint, STAGE_WEBSITE_RESOLUTION)] = StageExecution(
            StageResult(
                stage=STAGE_WEBSITE_RESOLUTION,
                status="failed",
                reason_code="WEBSITE_NOT_RESOLVED",
            ),
            updates={"company_website_url": ""},
        )
        store.executions[(fingerprint, STAGE_HIRING_IDENTITY_RESOLUTION)] = StageExecution(
            StageResult(stage=STAGE_HIRING_IDENTITY_RESOLUTION, status="not_run")
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))

        normal = inspect_checkpoint_prefix(
            store,
            fingerprint,
            context,
            STAGE_CAREER_DISCOVERY,
        )
        continuation = inspect_finalized_checkpoint_prefix(
            store,
            fingerprint,
            context,
            STAGE_CAREER_DISCOVERY,
        )

        self.assertEqual(normal.effective_start, STAGE_WEBSITE_RESOLUTION)
        self.assertEqual(continuation.effective_start, STAGE_CAREER_DISCOVERY)
        self.assertEqual(continuation.defects, ())

        calls = []
        ApplicationRunner(
            [RecordingStage(STAGE_CAREER_DISCOVERY, calls)],
            checkpoint_store=store,
        ).run(
            context,
            start_at=STAGE_CAREER_DISCOVERY,
            stop_after=STAGE_CAREER_DISCOVERY,
            input_fingerprint=fingerprint,
            same_attempt_continuation=True,
        )

        self.assertEqual(calls, [STAGE_CAREER_DISCOVERY])
        self.assertEqual(
            [result.status for result in context.stage_results[:3]],
            ["not_applicable", "failed", "not_run"],
        )

    def test_reports_whether_checkpointing_is_enabled(self):
        self.assertFalse(ApplicationRunner([]).checkpointing_enabled)
        self.assertTrue(
            ApplicationRunner(
                [], checkpoint_store=MemoryCheckpointStore()
            ).checkpointing_enabled
        )

    def test_read_only_prefix_inspection_returns_typed_contiguous_plan(self):
        fingerprint = "input-v1"
        store = MemoryCheckpointStore()
        store.executions[(fingerprint, STAGE_LINKEDIN_DISCOVERY)] = authoritative_execution(
            STAGE_LINKEDIN_DISCOVERY
        )
        store.executions[(fingerprint, STAGE_HIRING_IDENTITY_RESOLUTION)] = (
            authoritative_execution(STAGE_HIRING_IDENTITY_RESOLUTION)
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.company_website_url = "https://hydrated.example"
        context.career_page_url = "https://hydrated.example/careers"

        plan = inspect_checkpoint_prefix(
            store,
            fingerprint,
            context,
            STAGE_CAREER_DISCOVERY,
        )

        self.assertIsInstance(plan, CheckpointPrefixInspection)
        self.assertEqual(plan.requested_start, STAGE_CAREER_DISCOVERY)
        self.assertEqual(plan.effective_start, STAGE_WEBSITE_RESOLUTION)
        self.assertEqual(
            [execution.result.stage for execution in plan.executions],
            [STAGE_LINKEDIN_DISCOVERY],
        )
        self.assertEqual(plan.defects[0].defect_class, "missing_corrupt_or_incompatible")
        self.assertEqual(store.invalidated, [])
        self.assertEqual(context.career_page_url, "https://hydrated.example/careers")
        self.assertEqual(context.stage_results, [])

    def test_executes_in_canonical_order_and_fills_missing_stages(self):
        calls = []
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        runner = ApplicationRunner(
            [
                RecordingStage(STAGE_OPENING_MATCH, calls),
                RecordingStage(STAGE_WEBSITE_RESOLUTION, calls),
                RecordingStage(STAGE_CAREER_DISCOVERY, calls),
            ]
        )

        returned = runner.run(context)

        self.assertIs(returned, context)
        self.assertEqual(
            calls,
            [STAGE_WEBSITE_RESOLUTION, STAGE_CAREER_DISCOVERY, STAGE_OPENING_MATCH],
        )
        self.assertEqual([result.stage for result in context.stage_results], list(PIPELINE_STAGES))
        statuses = {result.stage: result.status for result in context.stage_results}
        self.assertEqual(statuses[STAGE_WEBSITE_RESOLUTION], "success")
        self.assertEqual(statuses[STAGE_JOB_BOARD_DISCOVERY], "not_run")
        self.assertEqual(
            context.trace["stages"][STAGE_JOB_BOARD_DISCOVERY]["scheduler"]["reason"],
            "implementation_not_supplied",
        )

    def test_start_at_reuses_upstream_results_and_recomputes_selected_range(self):
        calls = []
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.company_website_url = "https://hydrated.example"
        website_result = StageResult(stage=STAGE_WEBSITE_RESOLUTION, status="success")
        context.stage_results = [website_result]
        context.trace["stages"] = {
            STAGE_WEBSITE_RESOLUTION: {"checkpoint": True},
        }
        runner = ApplicationRunner(
            [
                RecordingStage(
                    STAGE_CAREER_DISCOVERY,
                    calls,
                    updates={"career_page_url": "https://acme.example/careers"},
                ),
                RecordingStage(STAGE_JOB_BOARD_DISCOVERY, calls),
            ]
        )

        runner.run(
            context,
            start_at=STAGE_CAREER_DISCOVERY,
            stop_after=STAGE_JOB_BOARD_DISCOVERY,
        )

        self.assertEqual(calls, [STAGE_CAREER_DISCOVERY, STAGE_JOB_BOARD_DISCOVERY])
        self.assertIs(
            next(
                result
                for result in context.stage_results
                if result.stage == STAGE_WEBSITE_RESOLUTION
            ),
            website_result,
        )
        self.assertEqual(
            context.trace["stages"][STAGE_WEBSITE_RESOLUTION],
            {"checkpoint": True},
        )
        self.assertEqual(context.career_page_url, "https://acme.example/careers")
        self.assertEqual(context.company_website_url, "https://hydrated.example")
        self.assertEqual(
            context.trace["stages"][STAGE_RESULT_VALIDATION]["scheduler"]["reason"],
            "after_stop_after",
        )

    def test_start_at_without_checkpoint_marks_preceding_stages_not_run(self):
        calls = []
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))

        ApplicationRunner([RecordingStage(STAGE_OPENING_MATCH, calls)]).run(
            context,
            start_at=STAGE_OPENING_MATCH,
            stop_after=STAGE_OPENING_MATCH,
        )

        self.assertEqual(calls, [STAGE_OPENING_MATCH])
        self.assertEqual(
            context.trace["stages"][STAGE_HIRING_IDENTITY_RESOLUTION]["scheduler"]["reason"],
            "before_start_at",
        )
        self.assertEqual(
            context.trace["stages"][STAGE_RESULT_VALIDATION]["scheduler"]["reason"],
            "after_stop_after",
        )

    def test_selected_range_replaces_stale_results_and_downstream_results(self):
        calls = []
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.stage_results = [
            StageResult(stage=STAGE_CAREER_DISCOVERY, status="failed"),
            StageResult(stage=STAGE_OPENING_MATCH, status="success"),
        ]
        runner = ApplicationRunner([RecordingStage(STAGE_CAREER_DISCOVERY, calls)])

        runner.run(
            context,
            start_at=STAGE_CAREER_DISCOVERY,
            stop_after=STAGE_CAREER_DISCOVERY,
        )

        results = {result.stage: result for result in context.stage_results}
        self.assertEqual(results[STAGE_CAREER_DISCOVERY].status, "success")
        self.assertEqual(results[STAGE_OPENING_MATCH].status, "not_run")

    def test_rejects_invalid_configuration_and_invalid_stage_results(self):
        calls = []
        website = RecordingStage(STAGE_WEBSITE_RESOLUTION, calls)
        with self.assertRaisesRegex(ValueError, "Duplicate pipeline stage"):
            ApplicationRunner([website, website])
        with self.assertRaisesRegex(ValueError, "Unknown pipeline stage"):
            ApplicationRunner([RecordingStage("custom", calls)])

        runner = ApplicationRunner([website])
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        with self.assertRaisesRegex(ValueError, "comes after"):
            runner.run(
                context,
                start_at=STAGE_OPENING_MATCH,
                stop_after=STAGE_CAREER_DISCOVERY,
            )
        with self.assertRaisesRegex(ValueError, "Unknown start_at"):
            runner.run(context, start_at="custom")
        checkpoint_store = MemoryCheckpointStore()
        with self.assertRaisesRegex(ValueError, "comes after"):
            ApplicationRunner([], checkpoint_store=checkpoint_store).run(
                context,
                start_at=STAGE_OPENING_MATCH,
                stop_after=STAGE_CAREER_DISCOVERY,
                input_fingerprint="input-v1",
            )
        self.assertEqual(checkpoint_store.invalidated, [])

        class WrongResultStage:
            name = STAGE_WEBSITE_RESOLUTION

            def run(self, context):
                return StageExecution(
                    StageResult(stage=STAGE_CAREER_DISCOVERY, status="success")
                )

        with self.assertRaisesRegex(ValueError, "returned a result"):
            ApplicationRunner([WrongResultStage()]).run(context)

    def test_valid_full_prefix_restores_in_order_and_hydrates_outputs(self):
        calls = []
        fingerprint = "input-v1"
        store = MemoryCheckpointStore()
        seed_prefix(store, fingerprint, STAGE_RESULT_VALIDATION)
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))

        ApplicationRunner(
            [RecordingStage(STAGE_RESULT_VALIDATION, calls)],
            checkpoint_store=store,
        ).run(
            context,
            start_at=STAGE_RESULT_VALIDATION,
            input_fingerprint=fingerprint,
        )

        self.assertEqual(context.company_website_url, "https://acme.example")
        self.assertEqual(context.career_page_url, "https://acme.example/careers")
        self.assertEqual(context.job_list_page_url, "https://jobs.acme.example")
        self.assertEqual(context.open_position_url, "https://jobs.acme.example/123")
        self.assertEqual(
            context.trace["stages"][STAGE_WEBSITE_RESOLUTION],
            {"authoritative": True},
        )
        self.assertEqual(
            context.trace["checkpoint_prefix"]["effective_start"],
            STAGE_RESULT_VALIDATION,
        )
        self.assertEqual(
            [
                event["stage"]
                for event in context.trace["checkpoint_events"]
                if event["action"] == "restore"
            ],
            list(PIPELINE_STAGES[:-1]),
        )

    def test_missing_middle_falls_back_and_never_restores_post_gap_checkpoint(self):
        calls = []
        fingerprint = "input-v1"
        store = MemoryCheckpointStore()
        store.executions[(fingerprint, STAGE_LINKEDIN_DISCOVERY)] = authoritative_execution(
            STAGE_LINKEDIN_DISCOVERY
        )
        for stage in (
            STAGE_HIRING_IDENTITY_RESOLUTION,
            STAGE_CAREER_DISCOVERY,
            STAGE_JOB_BOARD_DISCOVERY,
        ):
            store.executions[(fingerprint, stage)] = authoritative_execution(stage)
        stages = [
            RecordingStage(
                stage,
                calls,
                updates=(
                    dict([REQUIRED_OUTPUTS[stage]])
                    if stage in REQUIRED_OUTPUTS
                    else None
                ),
            )
            for stage in PIPELINE_STAGES[1:6]
        ]

        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        ApplicationRunner(stages, checkpoint_store=store).run(
            context,
            start_at=STAGE_OPENING_MATCH,
            stop_after=STAGE_OPENING_MATCH,
            input_fingerprint=fingerprint,
        )

        self.assertEqual(context.trace["checkpoint_prefix"]["requested_start"], STAGE_OPENING_MATCH)
        self.assertEqual(
            context.trace["checkpoint_prefix"]["effective_start"],
            STAGE_WEBSITE_RESOLUTION,
        )
        self.assertEqual(
            context.trace["checkpoint_prefix"]["defect_class"],
            "missing_corrupt_or_incompatible",
        )
        self.assertEqual(
            context.trace["checkpoint_prefix"]["invalidated_suffix"],
            list(PIPELINE_STAGES[1:]),
        )
        self.assertEqual(store.invalidated, [(fingerprint, STAGE_WEBSITE_RESOLUTION)])
        self.assertNotIn(
            {"stage": STAGE_HIRING_IDENTITY_RESOLUTION, "action": "restore"},
            context.trace["checkpoint_events"],
        )

    def test_executed_stages_are_saved_and_failures_are_not_saved(self):
        calls = []
        store = MemoryCheckpointStore()
        runner = ApplicationRunner(
            [RecordingStage(STAGE_WEBSITE_RESOLUTION, calls)],
            checkpoint_store=store,
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        runner.run(
            context,
            stop_after=STAGE_WEBSITE_RESOLUTION,
            input_fingerprint="input-v1",
        )

        self.assertEqual(len(store.saved), 1)
        self.assertEqual(store.saved[0][0], "input-v1")
        self.assertEqual(store.saved[0][1].result.stage, STAGE_WEBSITE_RESOLUTION)
        self.assertIn(
            {"stage": STAGE_WEBSITE_RESOLUTION, "action": "save"},
            context.trace["checkpoint_events"],
        )

        class RaisingStage:
            name = STAGE_WEBSITE_RESOLUTION

            def run(self, context):
                raise RuntimeError("stage failed")

        failing_store = MemoryCheckpointStore()
        with self.assertRaisesRegex(RuntimeError, "stage failed"):
            ApplicationRunner(
                [RaisingStage()], checkpoint_store=failing_store
            ).run(
                PipelineContext.from_company(CompanyInput(company_name="Acme")),
                input_fingerprint="input-v1",
            )
        self.assertEqual(failing_store.saved, [])

    def test_rerun_invalidates_selected_stage_downstream_and_recomputes(self):
        calls = []
        fingerprint = "input-v1"
        store = MemoryCheckpointStore()
        for stage in PIPELINE_STAGES:
            store.executions[(fingerprint, stage)] = authoritative_execution(stage)
        runner = ApplicationRunner(
            [
                RecordingStage(STAGE_CAREER_DISCOVERY, calls),
                RecordingStage(STAGE_JOB_BOARD_DISCOVERY, calls),
            ],
            checkpoint_store=store,
        )

        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        runner.run(
            context,
            rerun_from=STAGE_CAREER_DISCOVERY,
            stop_after=STAGE_JOB_BOARD_DISCOVERY,
            input_fingerprint=fingerprint,
        )

        self.assertEqual(
            store.invalidated,
            [(fingerprint, STAGE_CAREER_DISCOVERY)],
        )
        self.assertEqual(
            context.trace["checkpoint_events"][0],
            {"stage": STAGE_CAREER_DISCOVERY, "action": "invalidate_from"},
        )
        self.assertEqual(calls, [STAGE_CAREER_DISCOVERY, STAGE_JOB_BOARD_DISCOVERY])
        self.assertEqual(
            [execution.result.stage for _, execution in store.saved],
            [STAGE_CAREER_DISCOVERY, STAGE_JOB_BOARD_DISCOVERY],
        )
        self.assertIn((fingerprint, STAGE_WEBSITE_RESOLUTION), store.executions)
        self.assertNotIn((fingerprint, STAGE_OPENING_MATCH), store.executions)

    def test_non_authoritative_and_invalid_success_outputs_fall_back(self):
        cases = (
            (
                StageResult(stage=STAGE_WEBSITE_RESOLUTION, status="partial"),
                {"company_website_url": "https://acme.example"},
                "non_authoritative_status",
            ),
            (
                StageResult(stage=STAGE_WEBSITE_RESOLUTION, status="success"),
                {},
                "missing_required_output",
            ),
            (
                StageResult(stage=STAGE_WEBSITE_RESOLUTION, status="success"),
                {"company_website_url": "https://user:secret@acme.example"},
                "unsafe_url_update",
            ),
            (
                StageResult(stage=STAGE_WEBSITE_RESOLUTION, status="success"),
                {"company_website_url": "not a url"},
                "unsafe_url_update",
            ),
        )
        for result, updates, expected_defect in cases:
            with self.subTest(expected_defect=expected_defect, updates=updates):
                store = MemoryCheckpointStore()
                fingerprint = "input-v1"
                store.executions[(fingerprint, STAGE_LINKEDIN_DISCOVERY)] = (
                    authoritative_execution(STAGE_LINKEDIN_DISCOVERY)
                )
                store.executions[(fingerprint, STAGE_WEBSITE_RESOLUTION)] = (
                    StageExecution(result, updates=updates)
                )
                context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
                ApplicationRunner([], checkpoint_store=store).run(
                    context,
                    start_at=STAGE_HIRING_IDENTITY_RESOLUTION,
                    stop_after=STAGE_HIRING_IDENTITY_RESOLUTION,
                    input_fingerprint=fingerprint,
                )
                self.assertEqual(
                    context.trace["checkpoint_prefix"]["effective_start"],
                    STAGE_WEBSITE_RESOLUTION,
                )
                self.assertEqual(
                    context.trace["checkpoint_prefix"]["defect_class"],
                    expected_defect,
                )

    def test_all_url_updates_require_safe_normalized_public_values(self):
        unsafe_values = (
            "http://acme.example:443/path",
            "https://acme.example:80/path",
            "https://acme.example/%0Ahidden",
            "https://user%3Asecret@acme.example/path",
        )
        for field_name in (
            "company_website_url",
            "career_root_url",
            "career_page_url",
            "job_list_page_url",
            "open_position_url",
        ):
            for unsafe_value in unsafe_values:
                with self.subTest(field_name=field_name, unsafe_value=unsafe_value):
                    updates = {"company_website_url": "https://acme.example"}
                    updates[field_name] = unsafe_value
                    store = MemoryCheckpointStore()
                    fingerprint = "input-v1"
                    store.executions[(fingerprint, STAGE_LINKEDIN_DISCOVERY)] = (
                        authoritative_execution(STAGE_LINKEDIN_DISCOVERY)
                    )
                    store.executions[(fingerprint, STAGE_WEBSITE_RESOLUTION)] = (
                        authoritative_execution(
                            STAGE_WEBSITE_RESOLUTION,
                            updates=updates,
                        )
                    )

                    inspection = inspect_checkpoint_prefix(
                        store,
                        fingerprint,
                        PipelineContext.from_company(CompanyInput(company_name="Acme")),
                        STAGE_HIRING_IDENTITY_RESOLUTION,
                    )

                    self.assertEqual(
                        inspection.defects[0].defect_class,
                        "unsafe_url_update",
                    )
                    self.assertNotIn(unsafe_value, inspection.defects[0].detail)

    def test_nullable_url_updates_may_be_absent_or_none(self):
        fingerprint = "input-v1"
        store = MemoryCheckpointStore()
        store.executions[(fingerprint, STAGE_LINKEDIN_DISCOVERY)] = (
            authoritative_execution(STAGE_LINKEDIN_DISCOVERY)
        )
        store.executions[(fingerprint, STAGE_WEBSITE_RESOLUTION)] = (
            authoritative_execution(
                STAGE_WEBSITE_RESOLUTION,
                updates={
                    "company_website_url": "https://acme.example",
                    "career_root_url": None,
                    "career_page_url": None,
                    "job_list_page_url": None,
                    "open_position_url": None,
                },
            )
        )

        inspection = inspect_checkpoint_prefix(
            store,
            fingerprint,
            PipelineContext.from_company(CompanyInput(company_name="Acme")),
            STAGE_HIRING_IDENTITY_RESOLUTION,
        )

        self.assertEqual(inspection.defects, ())

    def test_scheme_default_ports_are_accepted(self):
        fingerprint = "input-v1"
        store = MemoryCheckpointStore()
        store.executions[(fingerprint, STAGE_LINKEDIN_DISCOVERY)] = (
            authoritative_execution(STAGE_LINKEDIN_DISCOVERY)
        )
        store.executions[(fingerprint, STAGE_WEBSITE_RESOLUTION)] = (
            authoritative_execution(
                STAGE_WEBSITE_RESOLUTION,
                updates={
                    "company_website_url": "http://acme.example:80",
                    "career_root_url": "https://acme.example:443/careers",
                },
            )
        )

        inspection = inspect_checkpoint_prefix(
            store,
            fingerprint,
            PipelineContext.from_company(CompanyInput(company_name="Acme")),
            STAGE_HIRING_IDENTITY_RESOLUTION,
        )

        self.assertEqual(inspection.defects, ())

    def test_sequential_apply_failure_establishes_prefix_gap(self):
        fingerprint = "input-v1"
        store = MemoryCheckpointStore()
        seed_prefix(store, fingerprint, STAGE_CAREER_DISCOVERY)
        store.executions[(fingerprint, STAGE_HIRING_IDENTITY_RESOLUTION)] = StageExecution(
            StageResult(stage=STAGE_HIRING_IDENTITY_RESOLUTION, status="success"),
            updates={
                "unsupported_field": "value",
                "hiring_identity_evidence": HiringIdentityEvidence(
                    source_company_name="Acme",
                    hiring_entity_name="Acme",
                    relationship_type="same_entity",
                    verification_method="same_entity",
                    verified=True,
                ),
            },
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))

        ApplicationRunner([], checkpoint_store=store).run(
            context,
            start_at=STAGE_CAREER_DISCOVERY,
            stop_after=STAGE_CAREER_DISCOVERY,
            input_fingerprint=fingerprint,
        )

        self.assertEqual(
            context.trace["checkpoint_prefix"]["effective_start"],
            STAGE_HIRING_IDENTITY_RESOLUTION,
        )
        self.assertEqual(context.trace["checkpoint_prefix"]["defect_class"], "context_apply_failed")
        self.assertEqual(
            context.trace["checkpoint_prefix"]["defects"][0]["detail"],
            "Checkpoint updates could not be applied in sequence.",
        )

    def test_unexpected_context_apply_error_propagates(self):
        fingerprint = "input-v1"
        store = MemoryCheckpointStore()
        store.executions[(fingerprint, STAGE_LINKEDIN_DISCOVERY)] = (
            authoritative_execution(STAGE_LINKEDIN_DISCOVERY)
        )

        with patch.object(
            PipelineContext,
            "apply",
            side_effect=RuntimeError("private failure detail"),
        ):
            with self.assertRaisesRegex(RuntimeError, "private failure detail"):
                inspect_checkpoint_prefix(
                    store,
                    fingerprint,
                    PipelineContext.from_company(CompanyInput(company_name="Acme")),
                    STAGE_WEBSITE_RESOLUTION,
                )

    def test_rerun_prefix_error_happens_before_context_or_store_mutation(self):
        fingerprint = "input-v1"
        store = MemoryCheckpointStore()
        store.executions[(fingerprint, STAGE_LINKEDIN_DISCOVERY)] = (
            authoritative_execution(STAGE_LINKEDIN_DISCOVERY)
        )
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.career_page_url = "https://hydrated.example/careers"
        original_trace = dict(context.trace)

        with self.assertRaises(CheckpointPrefixError) as raised:
            ApplicationRunner([], checkpoint_store=store).run(
                context,
                rerun_from=STAGE_CAREER_DISCOVERY,
                stop_after=STAGE_CAREER_DISCOVERY,
                input_fingerprint=fingerprint,
            )

        self.assertEqual(raised.exception.inspection.effective_start, STAGE_WEBSITE_RESOLUTION)
        self.assertEqual(store.invalidated, [])
        self.assertEqual(context.career_page_url, "https://hydrated.example/careers")
        self.assertEqual(context.trace, original_trace)

    def test_stop_after_removes_stale_checkpoint_suffix(self):
        fingerprint = "input-v1"
        store = MemoryCheckpointStore()
        for stage in PIPELINE_STAGES:
            store.executions[(fingerprint, stage)] = authoritative_execution(stage)
        calls = []
        runner = ApplicationRunner(
            [
                RecordingStage(STAGE_HIRING_IDENTITY_RESOLUTION, calls),
                RecordingStage(
                    STAGE_CAREER_DISCOVERY,
                    calls,
                    updates={"career_page_url": "https://acme.example/careers"},
                ),
            ],
            checkpoint_store=store,
        )

        runner.run(
            PipelineContext.from_company(CompanyInput(company_name="Acme")),
            start_at=STAGE_HIRING_IDENTITY_RESOLUTION,
            stop_after=STAGE_CAREER_DISCOVERY,
            input_fingerprint=fingerprint,
        )

        self.assertNotIn((fingerprint, STAGE_JOB_BOARD_DISCOVERY), store.executions)
        self.assertNotIn((fingerprint, STAGE_OPENING_MATCH), store.executions)
        self.assertNotIn((fingerprint, STAGE_RESULT_VALIDATION), store.executions)

    def test_filesystem_store_stop_after_removes_stale_checkpoint_suffix(self):
        fingerprint = "input-v1"
        with tempfile.TemporaryDirectory() as directory:
            store = FilesystemCheckpointStore(directory)
            for stage in PIPELINE_STAGES:
                store.save(fingerprint, authoritative_execution(stage))
            runner = ApplicationRunner(
                [
                    RecordingStage(STAGE_HIRING_IDENTITY_RESOLUTION, []),
                    RecordingStage(
                        STAGE_CAREER_DISCOVERY,
                        [],
                        updates={"career_page_url": "https://acme.example/careers"},
                    ),
                ],
                checkpoint_store=store,
            )

            runner.run(
                PipelineContext.from_company(CompanyInput(company_name="Acme")),
                start_at=STAGE_HIRING_IDENTITY_RESOLUTION,
                stop_after=STAGE_CAREER_DISCOVERY,
                input_fingerprint=fingerprint,
            )

            self.assertIsNone(store.load(fingerprint, STAGE_JOB_BOARD_DISCOVERY))
            self.assertIsNone(store.load(fingerprint, STAGE_OPENING_MATCH))
            self.assertIsNone(store.load(fingerprint, STAGE_RESULT_VALIDATION))

    def test_checkpoint_configuration_and_rerun_boundaries_are_validated(self):
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        with self.assertRaisesRegex(ValueError, "requires a checkpoint_store"):
            ApplicationRunner([]).run(context, input_fingerprint="input-v1")
        with self.assertRaisesRegex(ValueError, "must be a non-empty string"):
            ApplicationRunner([], checkpoint_store=MemoryCheckpointStore()).run(context)
        with self.assertRaisesRegex(ValueError, "must identify the same stage"):
            ApplicationRunner([], checkpoint_store=MemoryCheckpointStore()).run(
                context,
                start_at=STAGE_WEBSITE_RESOLUTION,
                rerun_from=STAGE_CAREER_DISCOVERY,
                input_fingerprint="input-v1",
            )
        with self.assertRaisesRegex(ValueError, "Unknown rerun_from"):
            ApplicationRunner([], checkpoint_store=MemoryCheckpointStore()).run(
                context,
                rerun_from="custom",
                input_fingerprint="input-v1",
            )
        store = MemoryCheckpointStore()
        with self.assertRaisesRegex(ValueError, "Unknown stop_after"):
            ApplicationRunner([], checkpoint_store=store).run(
                context,
                rerun_from=STAGE_CAREER_DISCOVERY,
                stop_after="custom",
                input_fingerprint="input-v1",
            )
        self.assertEqual(store.invalidated, [])


if __name__ == "__main__":
    unittest.main()
