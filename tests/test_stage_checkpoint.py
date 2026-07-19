import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from job_source_agent.checkpoint import (
    ADAPTER_VERSION,
    CHECKPOINT_SCHEMA_VERSION,
    input_fingerprint,
)
from job_source_agent.contracts import CheckpointStore, StageExecution
from job_source_agent.evidence_scope import StageEvidenceLineage
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard, JobBoardPortfolio
from job_source_agent.homepage_navigation import HomepageNavigationEvidence
from job_source_agent.identity_continuity import (
    HiringIdentityEvidence,
    OpeningIdentity,
    OpeningSelectionEvidence,
    ProviderIdentity,
)
from job_source_agent.models import PIPELINE_STAGES, StageResult
from job_source_agent.stage_checkpoint import FilesystemCheckpointStore


class FilesystemCheckpointStoreTests(unittest.TestCase):
    def test_company_evidence_revision_changes_input_fingerprint(self):
        base = {
            "company_name": "Acme",
            "linkedin_company_url": "https://www.linkedin.com/company/acme",
            "source_trace": {
                "company_discovery_evidence_revision": "a" * 64,
            },
        }
        changed = {
            **base,
            "source_trace": {
                "company_discovery_evidence_revision": "b" * 64,
            },
        }

        self.assertNotEqual(input_fingerprint(base), input_fingerprint(changed))

    def test_invalid_company_evidence_revision_is_not_fingerprinted(self):
        base = {"company_name": "Acme", "source_trace": {}}
        malformed = {
            "company_name": "Acme",
            "source_trace": {"company_discovery_evidence_revision": "private-path"},
        }

        self.assertEqual(input_fingerprint(base), input_fingerprint(malformed))

    def test_identity_contracts_round_trip_as_typed_context_updates(self):
        hiring = HiringIdentityEvidence(
            source_company_name="Acme",
            hiring_entity_name="Acme",
            relationship_type="same_entity",
            verification_method="same_entity",
            verified=True,
            evidence_url="https://acme.example/careers",
        )
        provider = ProviderIdentity(
            hiring_entity_name="Acme",
            provider="lever",
            tenant="acme",
            canonical_board_url="https://jobs.lever.co/acme",
            evidence_url="https://jobs.lever.co/acme",
            verification_method="tenant_name_match",
            relationship_verified=True,
        )
        opening = OpeningIdentity(
            hiring_entity_name="Acme",
            provider="lever",
            tenant="acme",
            canonical_board_url="https://jobs.lever.co/acme",
            canonical_opening_url="https://jobs.lever.co/acme/role-123",
        )
        selection = OpeningSelectionEvidence(
            provider="lever",
            tenant="acme",
            canonical_board_url="https://jobs.lever.co/acme",
            canonical_opening_url="https://jobs.lever.co/acme/role-123",
            title="AI Engineer",
            location="Remote",
            inventory_scope="full",
            inventory_complete=True,
            candidate_count=2,
        )
        execution = StageExecution(
            result=StageResult(stage="opening_match", status="success"),
            updates={
                "hiring_identity_evidence": hiring,
                "provider_identity": provider,
                "opening_identity": opening,
                "opening_selection_evidence": selection,
            },
        )

        with tempfile.TemporaryDirectory() as directory:
            store = FilesystemCheckpointStore(directory)
            store.save("fingerprint", execution)
            restored = store.load("fingerprint", "opening_match")

        self.assertIsNotNone(restored)
        self.assertEqual(restored.updates["hiring_identity_evidence"], hiring)
        self.assertEqual(restored.updates["provider_identity"], provider)
        self.assertEqual(restored.updates["opening_identity"], opening)
        self.assertEqual(restored.updates["opening_selection_evidence"], selection)

    def test_homepage_navigation_round_trips_as_typed_context_update(self):
        evidence = HomepageNavigationEvidence(
            homepage_url="https://company.example/",
            candidate_urls=("https://company.example/careers",),
        )
        execution = StageExecution(
            result=StageResult(stage="website_resolution", status="success"),
            updates={"homepage_navigation_evidence": evidence},
        )

        with tempfile.TemporaryDirectory() as directory:
            store = FilesystemCheckpointStore(directory)
            store.save("fingerprint", execution)
            restored = store.load("fingerprint", "website_resolution")

        self.assertIsNotNone(restored)
        self.assertEqual(restored.updates["homepage_navigation_evidence"], evidence)

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = FilesystemCheckpointStore(self.root)
        self.fingerprint = "a" * 64

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_satisfies_checkpoint_contract_and_round_trips_dataclasses(self):
        lineage = StageEvidenceLineage(
            stage="career_discovery",
            execution_fingerprint=self.fingerprint,
            producer_attempt_id="capture-attempt-0001",
        )
        execution = StageExecution(
            result=StageResult(
                stage="career_discovery",
                status="success",
                provider="greenhouse",
                output_count=1,
                evidence=[{"url": "https://example.test/careers"}],
            ),
            updates={"career_page_url": "https://example.test/careers"},
            trace={"candidates": [{"score": 10}]},
            evidence_lineage=lineage,
        )

        self.assertIsInstance(self.store, CheckpointStore)
        self.store.save(self.fingerprint, execution)

        self.assertEqual(self.store.load(self.fingerprint, "career_discovery"), execution)
        payload = json.loads(next(self.root.rglob("career_discovery.json")).read_text())
        self.assertEqual(payload["checkpoint_schema_version"], CHECKPOINT_SCHEMA_VERSION)
        self.assertEqual(payload["adapter_version"], ADAPTER_VERSION)
        self.assertEqual(payload["execution_fingerprint"], self.fingerprint)
        self.assertEqual(
            payload["execution"]["evidence_lineage"]["producer_attempt_id"],
            "capture-attempt-0001",
        )

    def test_mismatched_or_unknown_lineage_is_a_safe_cache_miss(self):
        execution = StageExecution(
            StageResult(stage="career_discovery", status="success"),
            evidence_lineage=StageEvidenceLineage(
                stage="career_discovery",
                execution_fingerprint=self.fingerprint,
                producer_attempt_id="capture-attempt-0001",
            ),
        )
        invalid_changes = (
            ("execution_fingerprint", "b" * 64),
            ("stage", "job_board_discovery"),
        )
        for field, value in invalid_changes:
            with self.subTest(field=field):
                self.store.save(self.fingerprint, execution)
                path = next(self.root.rglob("career_discovery.json"))
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["execution"]["evidence_lineage"][field] = value
                path.write_text(json.dumps(payload), encoding="utf-8")
                self.assertIsNone(self.store.load(self.fingerprint, "career_discovery"))

        self.store.save(self.fingerprint, execution)
        path = next(self.root.rglob("career_discovery.json"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["execution"]["evidence_lineage"]["raw_html"] = "secret"
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertIsNone(self.store.load(self.fingerprint, "career_discovery"))

    def test_missing_and_corrupt_checkpoints_are_safe_cache_misses(self):
        self.assertIsNone(self.store.load(self.fingerprint, "career_discovery"))

        execution = StageExecution(StageResult(stage="career_discovery", status="success"))
        self.store.save(self.fingerprint, execution)
        path = next(self.root.rglob("career_discovery.json"))
        path.write_text("{truncated", encoding="utf-8")

        self.assertIsNone(self.store.load(self.fingerprint, "career_discovery"))

    def test_replay_safe_job_board_round_trips_as_typed_context_update(self):
        discovered = DiscoveredJobBoard(
            board=JobBoard(
                url="https://jobs.example.test/search-results",
                provider="phenom",
                identifier="PUBLIC-TENANT",
                replay_safe=True,
            ),
            detection_method="page_evidence",
            evidence_url="https://jobs.example.test/search-results",
        )
        execution = StageExecution(
            StageResult(stage="job_board_discovery", status="success"),
            updates={
                "job_list_page_url": discovered.board.url,
                "provider": discovered.board.provider,
                "discovered_job_board": discovered,
            },
        )

        self.store.save(self.fingerprint, execution)

        restored = self.store.load(self.fingerprint, "job_board_discovery")
        self.assertEqual(restored, execution)
        self.assertIsInstance(restored.updates["discovered_job_board"], DiscoveredJobBoard)

    def test_runtime_only_job_board_identifier_is_omitted_from_checkpoint(self):
        discovered = DiscoveredJobBoard(
            board=JobBoard(
                url="https://jobs.example.test/careers",
                provider="ceipal",
                identifier='{"api_key":"do-not-persist"}',
            ),
            detection_method="page_evidence",
            evidence_url="https://jobs.example.test/careers",
        )
        execution = StageExecution(
            StageResult(stage="job_board_discovery", status="success"),
            updates={
                "job_list_page_url": discovered.board.url,
                "provider": discovered.board.provider,
                "discovered_job_board": discovered,
            },
        )

        self.store.save(self.fingerprint, execution)

        payload_text = next(self.root.rglob("job_board_discovery.json")).read_text()
        restored = self.store.load(self.fingerprint, "job_board_discovery")
        self.assertNotIn("do-not-persist", payload_text)
        self.assertNotIn("discovered_job_board", restored.updates)

    def test_replay_safe_job_board_portfolio_round_trips_as_typed_context_update(self):
        portfolio = JobBoardPortfolio(
            boards=(
                DiscoveredJobBoard(
                    board=JobBoard(
                        url="https://jobs.example.test/search-results",
                        provider="phenom",
                        identifier="PRIMARY-TENANT",
                        replay_safe=True,
                    ),
                    detection_method="page_evidence",
                    evidence_url="https://jobs.example.test/search-results",
                ),
                DiscoveredJobBoard(
                    board=JobBoard(
                        url="https://jobs.example.test/general/search-results",
                        provider="phenom",
                        identifier="GENERAL-TENANT",
                        replay_safe=True,
                    ),
                    detection_method="page_evidence",
                    evidence_url="https://jobs.example.test/general/search-results",
                ),
            ),
            eligible_set_complete=True,
        )
        execution = StageExecution(
            StageResult(stage="job_board_discovery", status="success"),
            updates={"job_board_portfolio": portfolio},
        )

        self.store.save(self.fingerprint, execution)

        restored = self.store.load(self.fingerprint, "job_board_discovery")
        self.assertEqual(restored, execution)
        self.assertIsInstance(restored.updates["job_board_portfolio"], JobBoardPortfolio)

    def test_runtime_only_suffix_saves_safe_primary_without_secret(self):
        portfolio = JobBoardPortfolio(
            boards=(
                DiscoveredJobBoard(
                    board=JobBoard(
                        url="https://jobs.example.test/search-results",
                        provider="phenom",
                        identifier="PUBLIC-TENANT",
                        replay_safe=True,
                    ),
                    detection_method="page_evidence",
                    evidence_url="https://jobs.example.test/search-results",
                ),
                DiscoveredJobBoard(
                    board=JobBoard(
                        url="https://jobs.example.test/runtime",
                        provider="ceipal",
                        identifier='{"api_key":"portfolio-do-not-persist"}',
                    ),
                    detection_method="page_evidence",
                    evidence_url="https://jobs.example.test/runtime",
                ),
            ),
            eligible_set_complete=False,
        )
        execution = StageExecution(
            StageResult(stage="job_board_discovery", status="success"),
            updates={"job_board_portfolio": portfolio},
        )

        self.store.save(self.fingerprint, execution)

        restored = self.store.load(self.fingerprint, "job_board_discovery")
        self.assertIsNotNone(restored)
        restored_portfolio = restored.updates["job_board_portfolio"]
        self.assertEqual(restored_portfolio.boards, (portfolio.primary,))
        self.assertFalse(restored_portfolio.eligible_set_complete)
        checkpoint = next(self.root.rglob("job_board_discovery.json"))
        self.assertNotIn("portfolio-do-not-persist", checkpoint.read_text(encoding="utf-8"))

    def test_invalid_job_board_portfolio_update_type_is_rejected(self):
        execution = StageExecution(
            StageResult(stage="job_board_discovery", status="success"),
            updates={"job_board_portfolio": {"boards": []}},
        )

        with self.assertRaisesRegex(TypeError, "job_board_portfolio.*invalid type"):
            self.store.save(self.fingerprint, execution)

        self.assertEqual(list(self.root.rglob("job_board_discovery.json")), [])

    def test_corrupt_job_board_portfolio_payload_is_a_safe_cache_miss(self):
        portfolio = JobBoardPortfolio(
            boards=(
                DiscoveredJobBoard(
                    board=JobBoard(
                        url="https://jobs.example.test/search-results",
                        provider="phenom",
                        identifier="PUBLIC-TENANT",
                        replay_safe=True,
                    ),
                    detection_method="page_evidence",
                    evidence_url="https://jobs.example.test/search-results",
                ),
            ),
            eligible_set_complete=True,
        )
        execution = StageExecution(
            StageResult(stage="job_board_discovery", status="success"),
            updates={"job_board_portfolio": portfolio},
        )
        corrupt_payloads = (
            {"boards": []},
            {
                "schema_version": "1.0",
                "boards": "not-a-list",
                "eligible_set_complete": True,
            },
            {
                **portfolio.to_checkpoint_payload(),
                "raw_html": "<html>secret</html>",
            },
        )

        for corrupt in corrupt_payloads:
            with self.subTest(corrupt=corrupt):
                self.store.save(self.fingerprint, execution)
                path = next(self.root.rglob("job_board_discovery.json"))
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["execution"]["updates"]["job_board_portfolio"] = corrupt
                path.write_text(json.dumps(payload), encoding="utf-8")

                self.assertIsNone(
                    self.store.load(self.fingerprint, "job_board_discovery")
                )

    def test_incompatible_or_mismatched_metadata_is_not_loaded(self):
        execution = StageExecution(StageResult(stage="career_discovery", status="success"))
        incompatible_fields = {
            "checkpoint_schema_version": "old",
            "adapter_version": "old",
            "execution_fingerprint": "wrong",
            "stage": "job_board_discovery",
        }

        for field, value in incompatible_fields.items():
            with self.subTest(field=field):
                self.store.save(self.fingerprint, execution)
                path = next(self.root.rglob("career_discovery.json"))
                payload = json.loads(path.read_text())
                payload[field] = value
                path.write_text(json.dumps(payload), encoding="utf-8")
                self.assertIsNone(self.store.load(self.fingerprint, "career_discovery"))

    def test_invalid_homepage_navigation_payload_is_a_safe_cache_miss(self):
        evidence = HomepageNavigationEvidence(
            homepage_url="https://company.example/",
            candidate_urls=("https://company.example/careers",),
        )
        execution = StageExecution(
            result=StageResult(stage="website_resolution", status="success"),
            updates={"homepage_navigation_evidence": evidence},
        )
        invalid_payloads = [
            {
                "schema_version": 1,
                "homepage_url": "https://company.example/",
                "candidate_urls": ["https://company.example/careers?token=secret"],
            },
            {
                "schema_version": 1,
                "homepage_url": "https://company.example/",
                "candidate_urls": ["https://company.example/careers"],
                "raw_html": "<html>secret</html>",
            },
        ]

        for invalid in invalid_payloads:
            with self.subTest(invalid=invalid):
                self.store.save(self.fingerprint, execution)
                path = next(self.root.rglob("website_resolution.json"))
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["execution"]["updates"]["homepage_navigation_evidence"] = invalid
                path.write_text(json.dumps(payload), encoding="utf-8")

                self.assertIsNone(
                    self.store.load(self.fingerprint, "website_resolution")
                )

    def test_invalidate_from_removes_selected_and_downstream_only(self):
        for stage in PIPELINE_STAGES:
            self.store.save(self.fingerprint, StageExecution(StageResult(stage=stage, status="success")))

        self.store.invalidate_from(self.fingerprint, "job_board_discovery")

        for stage in PIPELINE_STAGES[:4]:
            self.assertIsNotNone(self.store.load(self.fingerprint, stage))
        for stage in PIPELINE_STAGES[4:]:
            self.assertIsNone(self.store.load(self.fingerprint, stage))

    def test_invalid_stage_and_empty_fingerprint_are_rejected(self):
        execution = StageExecution(StageResult(stage="unknown", status="success"))
        with self.assertRaisesRegex(ValueError, "Unknown pipeline stage"):
            self.store.save(self.fingerprint, execution)
        with self.assertRaisesRegex(ValueError, "Unknown pipeline stage"):
            self.store.invalidate_from(self.fingerprint, "unknown")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.store.load("", "career_discovery")

    def test_fingerprint_cannot_escape_store_root(self):
        execution = StageExecution(StageResult(stage="career_discovery", status="success"))
        self.store.save("../../outside", execution)

        paths = list(self.root.rglob("career_discovery.json"))
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].is_relative_to(self.root))
        self.assertEqual(self.store.load("../../outside", "career_discovery"), execution)

    def test_concurrent_saves_publish_one_complete_execution(self):
        executions = [
            StageExecution(
                StageResult(stage="career_discovery", status="success", output_count=index),
                updates={"writer": index, "payload": "x" * 4096},
            )
            for index in range(12)
        ]
        barrier = Barrier(len(executions))

        def save(execution):
            barrier.wait()
            self.store.save(self.fingerprint, execution)

        with ThreadPoolExecutor(max_workers=len(executions)) as executor:
            list(executor.map(save, executions))

        self.assertIn(self.store.load(self.fingerprint, "career_discovery"), executions)
        self.assertEqual(list(self.root.rglob(".career_discovery.*.tmp")), [])

    def test_failed_atomic_replace_and_next_save_clean_temporary_files(self):
        execution = StageExecution(StageResult(stage="career_discovery", status="success"))
        with patch("job_source_agent.stage_checkpoint.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                self.store.save(self.fingerprint, execution)
        self.assertEqual(list(self.root.rglob(".career_discovery.*.tmp")), [])

        directory = self.store._fingerprint_directory(self.fingerprint)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ".career_discovery.crashed.tmp").write_text("partial", encoding="utf-8")
        self.store.save(self.fingerprint, execution)
        self.assertEqual(list(self.root.rglob(".career_discovery.*.tmp")), [])

    def test_parallel_first_saves_tolerate_shared_parent_directory_race(self):
        first = "parent-race-0"
        prefix = hashlib.sha256(first.encode()).hexdigest()[:2]
        second = next(
            f"parent-race-{index}"
            for index in range(1, 10_000)
            if hashlib.sha256(f"parent-race-{index}".encode()).hexdigest()[:2] == prefix
        )
        barrier = Barrier(2)

        def save(fingerprint):
            barrier.wait()
            execution = StageExecution(
                StageResult(stage="career_discovery", status="success"),
                updates={"fingerprint": fingerprint},
            )
            self.store.save(fingerprint, execution)
            return execution

        with ThreadPoolExecutor(max_workers=2) as executor:
            expected = list(executor.map(save, (first, second)))

        self.assertEqual(self.store.load(first, "career_discovery"), expected[0])
        self.assertEqual(self.store.load(second, "career_discovery"), expected[1])

    def test_load_save_and_invalidate_race_returns_only_complete_value_or_miss(self):
        old = StageExecution(
            StageResult(stage="career_discovery", status="success", output_count=1),
            updates={"version": "old"},
        )
        new = StageExecution(
            StageResult(stage="career_discovery", status="success", output_count=2),
            updates={"version": "new"},
        )
        self.store.save(self.fingerprint, old)
        barrier = Barrier(3)

        def load():
            barrier.wait()
            return self.store.load(self.fingerprint, "career_discovery")

        def save():
            barrier.wait()
            self.store.save(self.fingerprint, new)

        def invalidate():
            barrier.wait()
            self.store.invalidate_from(self.fingerprint, "career_discovery")

        with ThreadPoolExecutor(max_workers=3) as executor:
            load_future = executor.submit(load)
            save_future = executor.submit(save)
            invalidate_future = executor.submit(invalidate)
            observed = load_future.result()
            save_future.result()
            invalidate_future.result()

        self.assertIn(observed, (None, old, new))
        self.assertIn(self.store.load(self.fingerprint, "career_discovery"), (None, new))
        self.assertEqual(list(self.root.rglob(".career_discovery.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
