import unittest

from job_source_agent.application_runner import ApplicationRunner
from job_source_agent.contracts import PipelineContext, StageExecution
from job_source_agent.models import (
    PIPELINE_STAGES,
    STAGE_CAREER_DISCOVERY,
    STAGE_HIRING_IDENTITY_RESOLUTION,
    STAGE_JOB_BOARD_DISCOVERY,
    STAGE_OPENING_MATCH,
    STAGE_RESULT_VALIDATION,
    STAGE_WEBSITE_RESOLUTION,
    CompanyInput,
    StageResult,
)


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
    def test_reports_whether_checkpointing_is_enabled(self):
        self.assertFalse(ApplicationRunner([]).checkpointing_enabled)
        self.assertTrue(
            ApplicationRunner(
                [], checkpoint_store=MemoryCheckpointStore()
            ).checkpointing_enabled
        )

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

        class WrongResultStage:
            name = STAGE_WEBSITE_RESOLUTION

            def run(self, context):
                return StageExecution(
                    StageResult(stage=STAGE_CAREER_DISCOVERY, status="success")
                )

        with self.assertRaisesRegex(ValueError, "returned a result"):
            ApplicationRunner([WrongResultStage()]).run(context)

    def test_checkpoint_restore_takes_priority_and_hydrates_updates_and_trace(self):
        calls = []
        fingerprint = "input-v1"
        store = MemoryCheckpointStore()
        checkpoint = StageExecution(
            StageResult(stage=STAGE_WEBSITE_RESOLUTION, status="success"),
            updates={"company_website_url": "https://checkpoint.example"},
            trace={"checkpoint": True},
        )
        store.executions[(fingerprint, STAGE_WEBSITE_RESOLUTION)] = checkpoint
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        context.stage_results = [
            StageResult(stage=STAGE_WEBSITE_RESOLUTION, status="failed")
        ]
        context.trace["stages"] = {
            STAGE_WEBSITE_RESOLUTION: {"context": True},
        }

        ApplicationRunner(
            [RecordingStage(STAGE_CAREER_DISCOVERY, calls)],
            checkpoint_store=store,
        ).run(
            context,
            start_at=STAGE_CAREER_DISCOVERY,
            stop_after=STAGE_CAREER_DISCOVERY,
            input_fingerprint=fingerprint,
        )

        self.assertEqual(context.company_website_url, "https://checkpoint.example")
        self.assertEqual(
            context.trace["stages"][STAGE_WEBSITE_RESOLUTION],
            {"checkpoint": True},
        )
        restored = next(
            result
            for result in context.stage_results
            if result.stage == STAGE_WEBSITE_RESOLUTION
        )
        self.assertEqual(restored.status, "success")
        self.assertIn(
            {"stage": STAGE_WEBSITE_RESOLUTION, "action": "restore"},
            context.trace["checkpoint_events"],
        )
        self.assertEqual(
            [stage for loaded_fingerprint, stage in store.loaded if loaded_fingerprint == fingerprint],
            list(PIPELINE_STAGES[:3]),
        )

    def test_checkpoint_miss_falls_back_to_context_and_corrupt_store_miss(self):
        class CorruptTolerantStore(MemoryCheckpointStore):
            def load(self, input_fingerprint, stage):
                self.loaded.append((input_fingerprint, stage))
                return None

        store = CorruptTolerantStore()
        context = PipelineContext.from_company(CompanyInput(company_name="Acme"))
        previous = StageResult(stage=STAGE_WEBSITE_RESOLUTION, status="success")
        context.stage_results = [previous]
        context.trace["stages"] = {STAGE_WEBSITE_RESOLUTION: {"context": True}}

        ApplicationRunner([], checkpoint_store=store).run(
            context,
            start_at=STAGE_CAREER_DISCOVERY,
            stop_after=STAGE_CAREER_DISCOVERY,
            input_fingerprint="input-v1",
        )

        restored = next(
            result
            for result in context.stage_results
            if result.stage == STAGE_WEBSITE_RESOLUTION
        )
        self.assertIs(restored, previous)
        self.assertEqual(
            context.trace["stages"][STAGE_WEBSITE_RESOLUTION],
            {"context": True},
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
            store.executions[(fingerprint, stage)] = StageExecution(
                StageResult(stage=stage, status="success"),
                trace={"old": True},
            )
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
