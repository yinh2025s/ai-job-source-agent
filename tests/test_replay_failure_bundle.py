import contextlib
import io
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from job_source_agent.composition import (
    FetcherConfig,
    LINKEDIN_EVIDENCE_CACHE_FILENAME,
    build_application,
)
from job_source_agent.identity_evidence import FilesystemLinkedInWebsiteEvidenceStore
from job_source_agent.company_discovery_evidence import (
    VerifiedCareerEvidence,
    VerifiedCompanyDiscoveryEvidence,
    VerifiedProviderBoardEvidence,
    VerifiedWebsiteEvidence,
)
from job_source_agent.company_discovery_evidence_store import (
    FilesystemCompanyDiscoveryEvidenceStore,
)
from job_source_agent.contracts import PipelineContext
from job_source_agent.identity_continuity import ProviderIdentity
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard
from job_source_agent.snapshot import SnapshotStore
from job_source_agent.models import PIPELINE_STAGES, CompanyInput, dataclass_to_dict
from job_source_agent.run_configuration import AgentConfig, DeterministicRunConfig
from job_source_agent.stages import OpeningMatchStage
from job_source_agent.web import Page
from scripts.replay_failure_bundle import (
    FailureReplayError,
    _RedactionHydratingScopedFetcher,
    _build_outcome_gate,
    _build_record_integrity,
    _authoritative_upstream_executions,
    _effective_replay_resume_stage,
    _export_replay_records_with_sources,
    _hydrate_redacted_json_credentials,
    _normalize_identity_contract,
    _remove_derived_hiring_entity_inputs,
    _replay_resume_stage,
    _restore_stored_provider_inputs,
    _scoped_execution_boundary_errors,
    _scoped_execution_company,
    _scoped_job_board_portfolio,
    _seed_scoped_replay_producer_state,
    main,
    replay_failure_bundle,
)


class FailureReplayBundleTests(unittest.TestCase):
    def test_scoped_replay_restores_explicit_stored_provider_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            store = FilesystemCompanyDiscoveryEvidenceStore(path)
            company = CompanyInput(
                company_name="Century Communities",
                linkedin_company_url="https://www.linkedin.com/company/century",
            )
            store.save(
                company.company_name,
                company.linkedin_company_url,
                website=VerifiedWebsiteEvidence(
                    url="https://century.example",
                    source="linkedin_official_website",
                    evidence_url=company.linkedin_company_url,
                    observed_at=time.time(),
                ),
                career=VerifiedCareerEvidence(
                    url="https://century.example/careers",
                    website_url="https://century.example",
                    source="first_party_navigation",
                    evidence_url="https://century.example/careers",
                    observed_at=time.time(),
                ),
            )
            source = self._stored_provider_source_record(
                board_url="https://job-boards.greenhouse.io/century",
                tenant="century",
            )

            restored = _restore_stored_provider_inputs(store, [company], [source])
            evidence = store.load(company.company_name, company.linkedin_company_url)

        self.assertEqual(restored, 1)
        self.assertEqual(len(evidence.provider_boards), 1)
        self.assertEqual(evidence.provider_boards[0].provider, "greenhouse")
        self.assertEqual(evidence.provider_boards[0].tenant, "century")

    def test_scoped_replay_rejects_nonstored_or_cross_tenant_provider_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            store = FilesystemCompanyDiscoveryEvidenceStore(path)
            company = CompanyInput(
                company_name="Century Communities",
                linkedin_company_url="https://www.linkedin.com/company/century",
            )
            observed_at = time.time()
            store.save(
                company.company_name,
                company.linkedin_company_url,
                website=VerifiedWebsiteEvidence(
                    url="https://century.example",
                    source="linkedin_official_website",
                    evidence_url=company.linkedin_company_url,
                    observed_at=observed_at,
                ),
                career=VerifiedCareerEvidence(
                    url="https://century.example/careers",
                    website_url="https://century.example",
                    source="first_party_navigation",
                    evidence_url="https://century.example/careers",
                    observed_at=observed_at,
                ),
            )
            targeted_search = self._stored_provider_source_record(
                board_url="https://job-boards.greenhouse.io/century",
                tenant="century",
            )
            targeted_search["trace"]["stages"]["job_board_discovery"]["selected"][
                "source_kind"
            ] = "targeted_board_search"
            cross_tenant = self._stored_provider_source_record(
                board_url="https://job-boards.greenhouse.io/century",
                tenant="other-tenant",
            )

            restored_search = _restore_stored_provider_inputs(
                store,
                [company],
                [targeted_search],
            )
            restored_tenant = _restore_stored_provider_inputs(
                store,
                [company],
                [cross_tenant],
            )
            evidence = store.load(company.company_name, company.linkedin_company_url)

        self.assertEqual(restored_search, 0)
        self.assertEqual(restored_tenant, 0)
        self.assertEqual(evidence.provider_boards, ())

    @staticmethod
    def _stored_provider_source_record(*, board_url: str, tenant: str) -> dict:
        return {
            "trace": {
                "stages": {
                    "job_board_discovery": {
                        "selected": {
                            "url": board_url,
                            "source_kind": "stored_verified_provider_board",
                        }
                    }
                }
            },
            "identity_assertion": {
                "verdict": "verified",
                "hiring": {"verified": True},
                "provider": {
                    "provider": "greenhouse",
                    "tenant": tenant,
                    "canonical_board_url": board_url,
                    "evidence_url": "https://century.example/careers",
                    "verification_method": (
                        "stored_handoff_revalidated_provider_inventory"
                    ),
                    "relationship_verified": True,
                },
            },
        }

    def test_identity_comparison_redacts_secret_inside_structured_tenant(self):
        def assertion(api_key):
            return {
                "verdict": "verified",
                "normalized_chain": {
                    "provider": {
                        "provider": "ceipal",
                        "tenant": json.dumps(
                            {
                                "api_key": api_key,
                                "career_portal_id": "portal-one",
                                "origin": "https://careers.example.com",
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    }
                },
            }

        live = _normalize_identity_contract(assertion("runtime-secret"))
        replay = _normalize_identity_contract(assertion("[redacted]"))

        self.assertEqual(live, replay)
        self.assertNotIn("runtime-secret", json.dumps(live))
        self.assertIn("portal-one", json.dumps(live))

    def test_replay_does_not_promote_stored_provider_hiring_output_to_input(self):
        class EvidenceStore:
            def __init__(self, record):
                self.record = record

            def load(self, company_name, linkedin_company_url):
                return self.record

        class CompleteInventoryService:
            def match_discovered_board(self, discovered, *args):
                return None, discovered.board.url, {
                    "provider_api": {
                        "provider": "ashby",
                        "inventory": {
                            "source": "native_adapter",
                            "status": "verified_filtered_empty",
                            "complete": True,
                        },
                        "adapter_trace": {
                            "tenant_identity_conflict": False,
                            "errors": [],
                        },
                    }
                }

        linkedin_url = "https://www.linkedin.com/company/haystack"
        board = JobBoard(
            provider="ashby",
            identifier="deepsetai",
            url="https://jobs.ashbyhq.com/deepsetai",
        )
        discovered = DiscoveredJobBoard(
            board=board,
            detection_method="stored_verified_provider_board",
            evidence_url=board.url,
        )
        evidence = VerifiedCompanyDiscoveryEvidence(
            company_name="Haystack",
            linkedin_company_url=linkedin_url,
            website=VerifiedWebsiteEvidence(
                url="https://www.deepset.ai",
                source="verified_resolver",
                evidence_url=linkedin_url,
                observed_at=1.0,
            ),
            career=VerifiedCareerEvidence(
                url="https://www.deepset.ai/careers",
                website_url="https://www.deepset.ai",
                source="first_party_navigation",
                evidence_url="https://www.deepset.ai",
                observed_at=1.0,
            ),
            provider_boards=(
                VerifiedProviderBoardEvidence(
                    provider="ashby",
                    tenant="deepsetai",
                    canonical_board_url=board.url,
                    relationship_evidence_url="https://www.deepset.ai/careers",
                    verification_method="verified_first_party_handoff",
                    source="first_party_handoff",
                    observed_at=1.0,
                ),
            ),
        )

        def resolve_stored_identity(company):
            context = PipelineContext.from_company(company)
            context.job_list_page_url = board.url
            context.discovered_job_board = discovered
            context.provider_identity = ProviderIdentity(
                hiring_entity_name=company.company_name,
                provider="ashby",
                tenant="deepsetai",
                canonical_board_url=board.url,
                evidence_url=board.url,
                verification_method="linked_url_only",
                relationship_verified=False,
            )
            context.trace["stages"] = {
                "job_board_discovery": {
                    "selected": {"source_kind": "stored_verified_provider_board"}
                }
            }
            execution = OpeningMatchStage(
                CompleteInventoryService(),
                company_discovery_evidence_store=EvidenceStore(evidence),
            ).run(context)
            return execution.updates["hiring_identity_evidence"]

        live_identity = resolve_stored_identity(
            CompanyInput(company_name="Haystack", linkedin_company_url=linkedin_url)
        )
        replay_input = {
            "company_name": "Haystack",
            "linkedin_company_url": linkedin_url,
            "hiring_entity_name": "deepsetai",
            "source": "replay_input",
        }
        _remove_derived_hiring_entity_inputs([replay_input])
        replay_identity = resolve_stored_identity(CompanyInput(**replay_input))
        checkpointed_prefix = _authoritative_upstream_executions(
            {
                "company_website_url": "https://www.deepset.ai",
                "hiring_entity_name": "deepsetai",
                "stages": [
                    {"stage": "linkedin_discovery", "status": "success"},
                    {"stage": "website_resolution", "status": "success"},
                    {"stage": "hiring_identity_resolution", "status": "success"},
                ],
            },
            "career_discovery",
        )

        self.assertEqual(live_identity.relationship_type, "brand_parent")
        self.assertEqual(replay_identity.relationship_type, "brand_parent")
        self.assertEqual(replay_identity.hiring_entity_name, "deepsetai")
        self.assertNotIn("hiring_entity_name", replay_input)
        self.assertEqual(
            checkpointed_prefix[-1].updates["hiring_entity_name"],
            "deepsetai",
        )

    def test_redaction_hydrating_fetcher_forwards_forced_render_capability(self):
        controller = SimpleNamespace(supports_forced_render=True)

        fetcher = _RedactionHydratingScopedFetcher(controller)

        self.assertTrue(fetcher.supports_forced_render)
        controller.supports_forced_render = False
        self.assertFalse(fetcher.supports_forced_render)

    def test_scoped_replay_hydrates_only_redacted_json_credentials(self):
        hydrated = json.loads(_hydrate_redacted_json_credentials(json.dumps({
            "myJobsToken": "[REDACTED]",
            "nested": {"authorization": "[REDACTED]"},
            "label": "[REDACTED]",
        })))

        self.assertEqual(
            hydrated["myJobsToken"],
            "offline-replay-redacted-credential",
        )
        self.assertEqual(
            hydrated["nested"]["authorization"],
            "offline-replay-redacted-credential",
        )
        self.assertEqual(hydrated["label"], "[REDACTED]")

    def test_scoped_replay_does_not_rewrite_non_json_redactions(self):
        body = '<meta name="token" content="[REDACTED]">'

        self.assertEqual(_hydrate_redacted_json_credentials(body), body)

    def test_scoped_s5_seed_uses_stage_board_before_s6_changes_final_board(self):
        board_a = "https://careers.example.test/jobs?search=china"
        board_b = "https://careers.example.test/jobs?search=account+executive"
        source_record = {
            "company_website_url": "https://example.test",
            "career_page_url": "https://careers.example.test/jobs?search=china",
            "job_list_page_url": board_b,
            "stages": [
                {"stage": "linkedin_discovery", "status": "not_applicable"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {"stage": "career_discovery", "status": "success"},
                {
                    "stage": "job_board_discovery",
                    "status": "success",
                    "evidence": [
                        {"field": "job_list_page_url", "url": board_a},
                    ],
                },
                {"stage": "opening_match", "status": "success"},
            ],
            "trace": {
                "stages": {
                    "job_board_discovery": {"job_list_page_url": board_a},
                    "opening_match": {"job_list_page_url": board_b},
                }
            },
        }

        executions = _authoritative_upstream_executions(
            source_record,
            "opening_match",
            scoped_stage_evidence=True,
        )

        self.assertIsNotNone(executions)
        job_board_execution = next(
            execution
            for execution in executions
            if execution.result.stage == "job_board_discovery"
        )
        self.assertEqual(job_board_execution.updates["job_list_page_url"], board_a)

    def test_scoped_s5_seed_restores_captured_multi_board_portfolio(self):
        boards = (
            "https://recruiting.adp.com/srccar/public/nghome.guid?c=1181515&d=Corporate",
            "https://recruiting.adp.com/srccar/public/nghome.guid?c=1181515&d=Retail",
        )
        source_record = {
            "company_name": "Example",
            "company_website_url": "https://example.test",
            "career_page_url": "https://example.test/careers",
            "job_list_page_url": boards[0],
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {"stage": "career_discovery", "status": "success"},
                {"stage": "job_board_discovery", "status": "success", "provider": "adp"},
                {"stage": "opening_match", "status": "partial"},
            ],
            "trace": {"stages": {
                "job_board_discovery": {
                    "job_list_page_url": boards[0],
                    "job_board_portfolio": {
                        "eligible_count": 2,
                        "eligible_set_complete": False,
                        "primary_provider": "adp",
                        "primary_url": boards[0],
                    },
                    "provider_detection": {
                        "method": "linked_url_evidence",
                        "provider": "adp",
                        "url": boards[0],
                    },
                },
                "opening_match": {"board_portfolio": {"attempts": [
                    {
                        "board_url": board,
                        "provider": "adp",
                        "trace": {"provider_api": {"provider_detection": {
                            "source_method": "linked_url_evidence",
                        }}},
                    }
                    for board in boards
                ]}},
            }},
        }

        executions = _authoritative_upstream_executions(
            source_record,
            "opening_match",
            scoped_stage_evidence=True,
        )

        self.assertIsNotNone(executions)
        job_board_execution = next(
            execution
            for execution in executions
            if execution.result.stage == "job_board_discovery"
        )
        portfolio = job_board_execution.updates["job_board_portfolio"]
        self.assertEqual(
            tuple(item.board.url for item in portfolio.boards),
            boards,
        )
        self.assertFalse(portfolio.eligible_set_complete)

    def test_scoped_s5_seed_keeps_generic_singleton_url_only_without_detection(self):
        board_url = "https://careers.example.test/jobs"
        source_record = {
            "company_name": "Example",
            "company_website_url": "https://example.test",
            "career_page_url": "https://example.test/careers",
            "job_list_page_url": board_url,
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {"stage": "career_discovery", "status": "success"},
                {
                    "stage": "job_board_discovery",
                    "status": "success",
                    "evidence": [{
                        "field": "job_list_page_url",
                        "url": board_url,
                    }],
                },
                {"stage": "opening_match", "status": "partial"},
            ],
            "trace": {"stages": {"job_board_discovery": {
                "job_list_page_url": board_url,
                "job_board_portfolio": {
                    "eligible_count": 1,
                    "eligible_set_complete": True,
                    "primary_provider": "generic",
                    "primary_url": board_url,
                },
            }}},
        }

        executions = _authoritative_upstream_executions(
            source_record,
            "opening_match",
            scoped_stage_evidence=True,
        )

        self.assertIsNotNone(executions)
        job_board_execution = next(
            execution
            for execution in executions
            if execution.result.stage == "job_board_discovery"
        )
        self.assertEqual(job_board_execution.updates["job_list_page_url"], board_url)
        self.assertNotIn("discovered_job_board", job_board_execution.updates)
        self.assertNotIn("job_board_portfolio", job_board_execution.updates)

    def test_scoped_s5_seed_restores_replay_safe_singleton_board(self):
        board_url = (
            "https://recruiting.adp.com/srccar/public/nghome.guid"
            "?c=1181515&d=Corporate"
        )
        source_record = {
            "company_name": "Example",
            "company_website_url": "https://example.test",
            "career_page_url": "https://example.test/careers",
            "job_list_page_url": board_url,
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {"stage": "career_discovery", "status": "success"},
                {"stage": "job_board_discovery", "status": "success", "provider": "adp"},
                {"stage": "opening_match", "status": "partial"},
            ],
            "trace": {"stages": {"job_board_discovery": {
                "job_list_page_url": board_url,
                "job_board_portfolio": {
                    "eligible_count": 1,
                    "eligible_set_complete": True,
                    "primary_provider": "adp",
                    "primary_url": board_url,
                },
                "provider_detection": {
                    "method": "linked_url_evidence",
                    "provider": "adp",
                    "url": board_url,
                },
            }}},
        }

        executions = _authoritative_upstream_executions(
            source_record,
            "opening_match",
            scoped_stage_evidence=True,
        )

        job_board_execution = next(
            execution
            for execution in executions
            if execution.result.stage == "job_board_discovery"
        )
        self.assertEqual(
            job_board_execution.updates["discovered_job_board"].detection_method,
            "linked_url_evidence",
        )
        self.assertTrue(
            job_board_execution.updates["job_board_portfolio"].eligible_set_complete
        )

    def test_scoped_s5_seed_restores_verified_dynamic_board_identity(self):
        board_url = "https://careers.example.test/en/annonces"
        identifier = json.dumps(
            {
                "api_base": "https://api.digitalrecruiters.com/public/v1",
                "board_url": board_url,
                "locale": "en",
                "tenant": "careers.example.test",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        source_record = {
            "identity_assertion": {
                "verdict": "verified",
                "provider": {
                    "canonical_board_url": board_url,
                    "evidence_url": "https://careers.example.test/en",
                    "hiring_entity_name": "Example",
                    "provider": "digitalrecruiters",
                    "relationship_verified": True,
                    "schema_version": "1.1",
                    "tenant": identifier,
                    "verification_method": "verified_first_party_provider_page",
                },
            },
            "stages": [{
                "stage": "job_board_discovery",
                "status": "success",
                "provider": "digitalrecruiters",
            }],
            "trace": {"stages": {"job_board_discovery": {
                "job_board_portfolio": {
                    "eligible_count": 1,
                    "eligible_set_complete": True,
                    "primary_provider": "digitalrecruiters",
                    "primary_url": board_url,
                },
                "provider_detection": {
                    "method": "page_evidence",
                    "provider": "digitalrecruiters",
                    "url": board_url,
                },
            }}},
        }

        portfolio = _scoped_job_board_portfolio(source_record)

        self.assertIsNotNone(portfolio)
        self.assertEqual(portfolio.primary.board.identifier, identifier)
        self.assertTrue(portfolio.primary.board.replay_safe)

    def test_scoped_s5_seed_normalizes_embedded_provider_trace_method(self):
        board_url = "https://example-search.app.loxo.co/example-search"
        identifier = (
            '{"path":"/example-search","tenant":"example-search","v":1}'
        )
        source_record = {
            "identity_assertion": {
                "verdict": "verified",
                "provider": {
                    "canonical_board_url": board_url,
                    "evidence_url": "https://example.test/careers",
                    "hiring_entity_name": "Example",
                    "provider": "loxo",
                    "relationship_verified": True,
                    "schema_version": "1.1",
                    "tenant": identifier,
                    "verification_method": "verified_first_party_handoff",
                },
            },
            "stages": [{
                "stage": "job_board_discovery",
                "status": "success",
                "provider": "loxo",
            }],
            "trace": {"stages": {"job_board_discovery": {
                "job_board_portfolio": {
                    "eligible_count": 1,
                    "eligible_set_complete": True,
                    "primary_provider": "loxo",
                    "primary_url": board_url,
                },
                "provider_detection": {
                    "method": "embedded_provider_url_evidence",
                    "provider": "loxo",
                    "url": board_url,
                },
            }}},
        }

        portfolio = _scoped_job_board_portfolio(source_record)

        self.assertIsNotNone(portfolio)
        assert portfolio is not None
        self.assertEqual(
            portfolio.primary.detection_method,
            "linked_url_evidence",
        )
        self.assertEqual(portfolio.primary.board.identifier, identifier)
        self.assertTrue(portfolio.primary.board.replay_safe)

    def test_scoped_s5_seed_preserves_custom_singleton_checkpoint_boundary(self):
        board_url = "https://careers.example.test/search/"
        source_record = {
            "company_name": "Example",
            "company_website_url": "https://example.test",
            "career_page_url": "https://example.test/careers",
            "job_list_page_url": board_url,
            "identity_assertion": {"provider": {
                "canonical_board_url": board_url.rstrip("/"),
                "evidence_url": "https://example.test/careers",
                "hiring_entity_name": "Example",
                "provider": "successfactors",
                "relationship_verified": True,
                "schema_version": "1.1",
                "tenant": "custom:example",
                "verification_method": "verified_first_party_provider_page",
            }},
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {"stage": "career_discovery", "status": "success"},
                {
                    "stage": "job_board_discovery",
                    "status": "success",
                    "provider": "successfactors",
                },
                {"stage": "opening_match", "status": "partial"},
            ],
            "trace": {"stages": {"job_board_discovery": {
                "job_list_page_url": board_url,
                "job_board_portfolio": {
                    "eligible_count": 1,
                    "eligible_set_complete": True,
                    "primary_provider": "successfactors",
                    "primary_url": board_url,
                },
                "provider_detection": {
                    "method": "page_evidence",
                    "provider": "successfactors",
                    "url": board_url,
                },
            }}},
        }

        executions = _authoritative_upstream_executions(
            source_record,
            "opening_match",
            scoped_stage_evidence=True,
        )

        job_board_execution = next(
            execution
            for execution in executions
            if execution.result.stage == "job_board_discovery"
        )
        self.assertNotIn("discovered_job_board", job_board_execution.updates)
        self.assertNotIn("job_board_portfolio", job_board_execution.updates)
        self.assertEqual(
            job_board_execution.updates["provider_identity"].tenant,
            "custom:example",
        )

    def test_scoped_singleton_seed_rejects_conflicting_provider_detection(self):
        board_url = "https://careers.example.test/search/"
        source_record = {
            "stages": [{
                "stage": "job_board_discovery",
                "status": "success",
                "provider": "successfactors",
            }],
            "trace": {"stages": {"job_board_discovery": {
                "job_board_portfolio": {
                    "eligible_count": 1,
                    "eligible_set_complete": True,
                    "primary_provider": "successfactors",
                    "primary_url": board_url,
                },
                "provider_detection": {
                    "method": "page_evidence",
                    "provider": "greenhouse",
                    "url": board_url,
                },
            }}},
        }

        with self.assertRaisesRegex(ValueError, "metadata is inconsistent"):
            _scoped_job_board_portfolio(source_record)

    def test_scoped_preflight_rejects_ambiguous_s5_board_evidence(self):
        source_record = {
            "company_name": "Example",
            "stages": [
                {"stage": "linkedin_discovery", "status": "not_applicable"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {"stage": "career_discovery", "status": "success"},
                {
                    "stage": "job_board_discovery",
                    "status": "success",
                    "evidence": [{
                        "field": "job_list_page_url",
                        "url": "https://careers.example.test/jobs?search=china",
                    }],
                },
                {"stage": "opening_match", "status": "partial"},
            ],
            "trace": {"stages": {"job_board_discovery": {
                "job_list_page_url": (
                    "https://careers.example.test/jobs?search=account+executive"
                ),
            }}},
        }
        replay_record = {
            "source_trace": {"replay": {"first_non_success_stage": {
                "stage": "opening_match",
            }}},
        }
        plan = SimpleNamespace(
            evidence_mode="scoped_outcome_tape",
            record_id="a" * 64,
            stage_evidence_lineage=tuple(
                SimpleNamespace(stage=stage)
                for stage in PIPELINE_STAGES[:6]
            ),
        )

        errors = _scoped_execution_boundary_errors(
            [source_record],
            [replay_record],
            (plan,),
        )

        self.assertEqual(errors[0]["reason_code"], "scoped_stage_seed_ambiguous")
        self.assertIn("conflicting", errors[0]["detail"])

    def test_scoped_replay_restores_cache_backed_website_producer_state(self):
        company = CompanyInput(
            company_name="Example Electric",
            linkedin_company_url="https://www.linkedin.com/company/example-electric",
        )
        source_record = {
            "trace": {
                "stages": {
                    "website_resolution": {
                        "linkedin_official_evidence_source": "cache",
                        "candidates": [
                            {
                                "url": "https://unrelated.example.test",
                                "reasons": ["candidate source: speculative_guess"],
                            },
                        ],
                        "selected": {
                            "url": "https://www.example-electric.test",
                            "reasons": [
                                "candidate source: linkedin_cached_official_website"
                            ],
                        },
                    }
                }
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            checkpoint_root = Path(directory)
            _seed_scoped_replay_producer_state(
                checkpoint_root,
                company,
                source_record,
            )
            store = FilesystemLinkedInWebsiteEvidenceStore(
                checkpoint_root / LINKEDIN_EVIDENCE_CACHE_FILENAME
            )

            self.assertEqual(
                store.load(company.company_name, company.linkedin_company_url),
                ("https://www.example-electric.test",),
            )

    def test_scoped_replay_consumes_downstream_tape_after_cache_backed_website(self):
        company = CompanyInput(
            company_name="Example Electric",
            linkedin_company_url="https://www.linkedin.com/company/example-electric",
            linkedin_job_url=(
                "https://www.linkedin.com/jobs/view/engineer-at-example-electric-123"
            ),
            job_title="Engineer",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_checkpoint_root = root / "source-checkpoints"
            FilesystemLinkedInWebsiteEvidenceStore(
                source_checkpoint_root / LINKEDIN_EVIDENCE_CACHE_FILENAME
            ).save(
                company.company_name,
                company.linkedin_company_url,
                ("https://www.example-electric.test",),
            )
            application = build_application(
                FetcherConfig(
                    fixtures_dir=(
                        Path(__file__).resolve().parents[1] / "samples" / "sites"
                    ),
                    offline=True,
                    snapshot_dir=root / "snapshots",
                ),
                checkpoint_dir=source_checkpoint_root,
            )
            source = application.pipeline.discover(
                company,
                capture_attempt_id="capture-cache-backed-producer",
            )
            website_trace = source.trace["stages"]["website_resolution"]
            self.assertEqual(
                website_trace["linkedin_official_evidence_source"],
                "cache",
            )
            self.assertEqual(
                source.company_website_url,
                "https://www.example-electric.test",
            )
            (root / "results.json").write_text(
                json.dumps([dataclass_to_dict(source.trace_record())]),
                encoding="utf-8",
            )

            manifest = replay_failure_bundle(
                self._args(
                    root,
                    pipeline_status=None,
                    stage=None,
                    stage_status=None,
                    reason_code=None,
                    legacy_run_config=None,
                )
            )

        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["outcome_gate"]["status"], "passed")

    def test_scoped_execution_restores_authoritative_original_source_semantics(self):
        company = CompanyInput(
            company_name="Example",
            career_root_url="https://jobs.example.test",
            source="replay_input",
            source_trace={
                "linkedin_posting": {"availability": "listed"},
                "replay": {"record_id": "record-1"},
            },
        )
        source_record = {
            "trace": {
                "stages": {
                    "linkedin_discovery": {"source": "fixed_input"},
                    "website_resolution": {
                        "preferred_url": "https://example.test"
                    },
                    "hiring_identity_resolution": {"matched_rule": None},
                    "career_discovery": {
                        "preferred_root_validation": "trusted_provenance"
                    },
                }
            }
        }

        execution_company = _scoped_execution_company(company, source_record)

        self.assertEqual(execution_company.source, "fixed_input")
        self.assertEqual(execution_company.company_website_url, "https://example.test")
        self.assertEqual(
            execution_company.career_root_url,
            "https://jobs.example.test",
        )
        self.assertEqual(
            execution_company.source_trace,
            {"linkedin_posting": {"availability": "listed"}},
        )
        self.assertEqual(company.source, "replay_input")
        self.assertIn("replay", company.source_trace)

    def test_scoped_execution_does_not_promote_discovered_output_to_input_root(self):
        company = CompanyInput(
            company_name="Example",
            company_website_url="https://example.test",
            career_root_url="https://jobs.example.test",
            source="replay_input",
            source_trace={"replay": {"record_id": "record-1"}},
        )
        source_record = {
            "trace": {
                "stages": {
                    "linkedin_discovery": {"source": "input"},
                    "website_resolution": {
                        "preferred_url": "https://example.test"
                    },
                    "hiring_identity_resolution": {"matched_rule": None},
                    "career_discovery": {
                        "selected_from": "verified_homepage_navigation"
                    },
                }
            }
        }

        execution_company = _scoped_execution_company(company, source_record)

        self.assertEqual(execution_company.company_website_url, "https://example.test")
        self.assertIsNone(execution_company.career_root_url)

    def test_scoped_execution_unknown_source_fails_closed_as_replay_input(self):
        company = CompanyInput(
            company_name="Example",
            career_root_url="https://stale.example.test",
            source="replay_input",
            source_trace={"replay": {"record_id": "record-1"}},
        )
        source_record = {
            "trace": {
                "stages": {
                    "linkedin_discovery": {"source": "untrusted_custom_source"}
                }
            }
        }

        execution_company = _scoped_execution_company(company, source_record)

        self.assertIs(execution_company, company)
        self.assertEqual(execution_company.source, "replay_input")
        self.assertIn("replay", execution_company.source_trace)

    def test_scoped_execution_replay_input_clears_derived_website(self):
        company = CompanyInput(
            company_name="SKIMS",
            company_website_url="https://skims.com/",
            source="replay_input",
            source_trace={"replay": {"record_id": "record-1"}},
        )
        source_record = {
            "trace": {
                "stages": {
                    "linkedin_discovery": {"source": "replay_input"},
                    "website_resolution": {"preferred_url": None},
                    "hiring_identity_resolution": {"matched_rule": None},
                    "career_discovery": {},
                }
            }
        }

        execution_company = _scoped_execution_company(company, source_record)

        self.assertEqual(execution_company.source, "replay_input")
        self.assertEqual(execution_company.company_website_url, "")
        self.assertNotIn("replay", execution_company.source_trace)

    def test_scoped_execution_normalizes_blind_holdout_source_without_derived_website(self):
        company = CompanyInput(
            company_name="Example",
            company_website_url="https://derived.example.test",
            source="replay_input",
            source_trace={"replay": {"record_id": "record-1"}},
        )
        source_record = {
            "trace": {
                "stages": {
                    "linkedin_discovery": {
                        "source": "linkedin_public_jobs_blind_holdout"
                    },
                    "website_resolution": {"preferred_url": ""},
                    "hiring_identity_resolution": {"matched_rule": None},
                    "career_discovery": {},
                }
            }
        }

        execution_company = _scoped_execution_company(company, source_record)

        self.assertEqual(
            execution_company.source,
            "linkedin_public_jobs_blind_holdout",
        )
        self.assertEqual(execution_company.company_website_url, "")
        self.assertNotIn("replay", execution_company.source_trace)

    def test_scoped_execution_normalizes_observed_development_source_without_derived_website(self):
        company = CompanyInput(
            company_name="Example",
            company_website_url="https://derived.example.test",
            source="replay_input",
            source_trace={"replay": {"record_id": "record-1"}},
        )
        source_record = {
            "trace": {
                "stages": {
                    "linkedin_discovery": {
                        "source": "linkedin_public_jobs_observed_development"
                    },
                    "website_resolution": {"preferred_url": ""},
                    "hiring_identity_resolution": {"matched_rule": None},
                    "career_discovery": {},
                }
            }
        }

        execution_company = _scoped_execution_company(company, source_record)

        self.assertEqual(
            execution_company.source,
            "linkedin_public_jobs_observed_development",
        )
        self.assertEqual(execution_company.company_website_url, "")
        self.assertNotIn("replay", execution_company.source_trace)

    def _args(self, root: Path, **overrides):
        values = {
            "results": str(root / "results.json"),
            "snapshot_dir": str(root / "snapshots"),
            "output_dir": str(root / "bundle"),
            "pipeline_status": ["partial"],
            "stage": "opening_match",
            "stage_status": ["partial"],
            "reason_code": ["OPENING_NOT_FOUND"],
            "provider": None,
            "limit": None,
            "include_missing_website": False,
            "legacy_run_config": "composition-defaults",
            "company_discovery_evidence_store": None,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _write_inputs(self, root: Path):
        board_url = "https://jobs.example.test/jobs"
        results = [
            {
                "company_name": "Example Data",
                "company_website_url": "https://example.test",
                "career_root_url": board_url,
                "career_page_url": board_url,
                "job_list_page_url": board_url,
                "linkedin_job_title": "Data Analyst",
                "pipeline_status": "partial",
                "stages": [
                    {
                        "stage": "opening_match",
                        "status": "partial",
                        "reason_code": "OPENING_NOT_FOUND",
                    }
                ],
            }
        ]
        (root / "results.json").write_text(json.dumps(results), encoding="utf-8")
        homepage_url = "https://example.test"
        SnapshotStore(root / "snapshots").write_page(
            Page(
                url=homepage_url,
                final_url=homepage_url,
                html=(
                    f'<html><title>Example Data</title>'
                    f'<a href="{board_url}">Careers</a></html>'
                ),
                source="live",
            ),
            request_url=homepage_url,
        )
        for search_url in (
            "https://www.bing.com/search?q=Example+Data+official+website&format=rss&setlang=en-us&cc=us",
            "https://www.bing.com/search?q=Example+Data+official+website&setlang=en-us&cc=us",
            "https://html.duckduckgo.com/html/?q=Example+Data+official+website&setlang=en-us&cc=us",
        ):
            SnapshotStore(root / "snapshots").write_page(
                Page(
                    url=search_url,
                    final_url=search_url,
                    html="<html><body>No results</body></html>",
                    source="live",
                ),
                request_url=search_url,
            )
        SnapshotStore(root / "snapshots").write_page(
            Page(
                url=board_url,
                final_url=board_url,
                html=(
                    '<html><body><a href="/jobs/123-data-analyst">'
                    "Data Analyst</a></body></html>"
                ),
                source="live",
            ),
            request_url=board_url,
        )
        detail_url = "https://jobs.example.test/jobs/123-data-analyst"
        SnapshotStore(root / "snapshots").write_page(
            Page(
                url=detail_url,
                final_url=detail_url,
                html="<html><h1>Data Analyst</h1><p>Example Data</p></html>",
                source="live",
            ),
            request_url=detail_url,
        )
        for query_url in (
            f"{board_url}?q=Missing+Role",
            f"{board_url}?query=Missing+Role",
            f"{board_url}?search=Missing+Role",
        ):
            SnapshotStore(root / "snapshots").write_page(
                Page(
                    url=query_url,
                    final_url=query_url,
                    html=(
                        '<html><body><a href="/jobs/123-data-analyst">'
                        "Data Analyst</a></body></html>"
                    ),
                    source="live",
                ),
                request_url=query_url,
            )

    def test_reproduced_failure_passes_outcome_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            results_path = root / "results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results[0]["linkedin_job_title"] = "Missing Role"
            results[0]["stages"][0]["reason_code"] = (
                "OPENING_DISCOVERY_INCOMPLETE"
            )
            results_path.write_text(json.dumps(results), encoding="utf-8")

            manifest = replay_failure_bundle(
                self._args(
                    root,
                    reason_code=["OPENING_DISCOVERY_INCOMPLETE"],
                )
            )
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest["summary"]["total"], 1)
        self.assertEqual(
            manifest["summary"]["run_configuration_digest"],
            manifest["run_configuration_digest"],
        )
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["summary"]["checkpoint_action_counts"]["save"], 7)
        self.assertIsNone(replay_results[0]["open_position_url"])
        self.assertNotIn(str(root), json.dumps(manifest))
        self.assertEqual(manifest["paths"]["fixtures"], "offline/sites")
        self.assertEqual(manifest["outcome_gate"]["status"], "passed")
        self.assertEqual(
            manifest["outcome_gate"]["classification_counts"],
            {
                "reproduced": 1,
                "expected_transition": 0,
                "budget_recovery": 0,
                "fixture_gap": 0,
                "mismatch": 0,
            },
        )
        comparison = manifest["outcome_gate"]["records"][0]
        self.assertEqual(comparison["classification"], "reproduced")
        self.assertEqual(comparison["original_outcome"], comparison["replay_outcome"])
        self.assertEqual(manifest["run_configuration_provenance"], "legacy_defaulted")

    def test_scoped_capture_replays_as_isolated_bundle_v7(self):
        company = CompanyInput(
            company_name="Aurora Data",
            company_website_url="https://aurora-data.example",
            job_title="AI Algorithm Engineer Intern",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = build_application(
                FetcherConfig(
                    fixtures_dir=Path(__file__).resolve().parents[1] / "samples" / "sites",
                    offline=True,
                    snapshot_dir=root / "snapshots",
                )
            )
            source = application.pipeline.discover(
                company,
                capture_attempt_id="capture-attempt-bundle-v6",
            )
            (root / "results.json").write_text(
                json.dumps([dataclass_to_dict(source.trace_record())]),
                encoding="utf-8",
            )
            manifest = replay_failure_bundle(
                self._args(
                    root,
                    pipeline_status=None,
                    stage=None,
                    stage_status=None,
                    reason_code=None,
                    legacy_run_config=None,
                )
            )
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )
            replay_trace = json.loads(
                (root / "bundle" / "replay-trace.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest["bundle_schema_version"], 7)
        self.assertEqual(manifest["evidence_mode"], "scoped_outcome_tape")
        self.assertEqual(manifest["outcome_gate"]["status"], "passed")
        self.assertEqual(manifest["record_integrity"]["status"], "passed")
        self.assertEqual(manifest["snapshot_summary"]["scope_count"], 7)
        self.assertEqual(replay_results[0]["open_position_url"], source.open_position_url)
        self.assertEqual(
            manifest["record_plans"][0]["record_id"],
            replay_trace[0]["trace"]["source_trace"]["replay"]["record_id"],
        )
        self.assertNotIn("fixtures", manifest["paths"])
        self.assertEqual(manifest["paths"]["tapes"], "offline/tapes")

    def test_scoped_replay_freezes_selected_company_discovery_evidence(self):
        linkedin_company_url = "https://www.linkedin.com/company/aurora-data"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_store_path = root / "source-company-evidence.json"
            source_store = FilesystemCompanyDiscoveryEvidenceStore(source_store_path)
            observed_at = time.time()
            source_store.save(
                "Aurora Data",
                linkedin_company_url,
                website=VerifiedWebsiteEvidence(
                    url="https://aurora-data.example",
                    source="linkedin_official_website",
                    evidence_url=linkedin_company_url,
                    observed_at=observed_at,
                ),
            )
            source_store.save(
                "Unselected Company",
                "https://www.linkedin.com/company/unselected-company",
                website=VerifiedWebsiteEvidence(
                    url="https://unselected.example",
                    source="provided_website",
                    evidence_url="https://unselected.example",
                    observed_at=observed_at,
                ),
            )
            application = build_application(
                FetcherConfig(
                    fixtures_dir=Path(__file__).resolve().parents[1] / "samples" / "sites",
                    offline=True,
                    snapshot_dir=root / "snapshots",
                ),
                company_discovery_evidence_path=source_store_path,
            )
            source = application.pipeline.discover(
                CompanyInput(
                    company_name="Aurora Data",
                    linkedin_company_url=linkedin_company_url,
                    job_title="AI Algorithm Engineer Intern",
                    source="fixed_input",
                ),
                capture_attempt_id="capture-company-evidence-replay",
            )
            (root / "results.json").write_text(
                json.dumps([dataclass_to_dict(source.trace_record())]),
                encoding="utf-8",
            )

            manifest = replay_failure_bundle(
                self._args(
                    root,
                    pipeline_status=None,
                    stage=None,
                    stage_status=None,
                    reason_code=None,
                    legacy_run_config=None,
                    company_discovery_evidence_store=str(source_store_path),
                )
            )
            frozen = json.loads(
                (root / "bundle" / "company-discovery-evidence.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["outcome_gate"]["status"], "passed")
        evidence_provenance = manifest["company_discovery_evidence"]
        self.assertEqual(evidence_provenance["status"], "frozen")
        self.assertTrue(evidence_provenance["source_configured"])
        self.assertEqual(len(evidence_provenance["source_path_sha256"]), 64)
        self.assertEqual(evidence_provenance["source_status"], "available")
        self.assertEqual(evidence_provenance["selected_identity_count"], 1)
        self.assertEqual(evidence_provenance["frozen_record_count"], 1)
        self.assertEqual(evidence_provenance["bundle_path"], "company-discovery-evidence.json")
        self.assertEqual(
            manifest["paths"]["company_discovery_evidence"],
            "company-discovery-evidence.json",
        )
        self.assertEqual(len(frozen["records"]), 1)
        frozen_record = next(iter(frozen["records"].values()))
        self.assertEqual(frozen_record["company_name"], "aurora data")
        self.assertNotIn("open_position_url", json.dumps(frozen))
        self.assertNotIn("inventory", json.dumps(frozen))

    def test_corrupt_company_discovery_evidence_store_is_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            source_store_path = root / "corrupt-company-evidence.json"
            source_store_path.write_text("{not json", encoding="utf-8")

            manifest = replay_failure_bundle(
                self._args(
                    root,
                    company_discovery_evidence_store=str(source_store_path),
                )
            )

        self.assertEqual(manifest["company_discovery_evidence"]["status"], "omitted")
        self.assertEqual(manifest["company_discovery_evidence"]["source_status"], "corrupt")
        self.assertNotIn("company_discovery_evidence", manifest["paths"])

    def test_missing_or_unmatched_company_discovery_evidence_is_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            missing_manifest = replay_failure_bundle(
                self._args(
                    root,
                    company_discovery_evidence_store=str(root / "missing-store.json"),
                )
            )
            unmatched_store_path = root / "unmatched-store.json"
            FilesystemCompanyDiscoveryEvidenceStore(unmatched_store_path).save(
                "Different Company",
                "https://www.linkedin.com/company/different-company",
                website=VerifiedWebsiteEvidence(
                    url="https://different.example",
                    source="provided_website",
                    evidence_url="https://different.example",
                    observed_at=time.time(),
                ),
            )
            unmatched_manifest = replay_failure_bundle(
                self._args(
                    root,
                    output_dir=str(root / "unmatched-bundle"),
                    company_discovery_evidence_store=str(unmatched_store_path),
                )
            )

        self.assertEqual(missing_manifest["company_discovery_evidence"]["status"], "omitted")
        self.assertEqual(missing_manifest["company_discovery_evidence"]["source_status"], "missing")
        self.assertNotIn("company_discovery_evidence", missing_manifest["paths"])
        self.assertEqual(unmatched_manifest["company_discovery_evidence"]["status"], "omitted")
        self.assertEqual(
            unmatched_manifest["company_discovery_evidence"]["source_status"],
            "available_no_selected_evidence",
        )
        self.assertNotIn("company_discovery_evidence", unmatched_manifest["paths"])

    def test_scoped_capture_replays_upstream_terminal_boundary_without_website(self):
        company = CompanyInput(
            company_name="Missing Identity Example",
            linkedin_job_url="fixed-input-marker",
            job_title="AI Engineer",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = build_application(
                FetcherConfig(
                    fixtures_dir=Path(__file__).resolve().parents[1] / "samples" / "sites",
                    offline=True,
                    snapshot_dir=root / "snapshots",
                )
            )
            source = application.pipeline.discover(
                company,
                stop_after="hiring_identity_resolution",
                capture_attempt_id="capture-upstream-terminal",
            )
            self.assertFalse(source.company_website_url)
            self.assertEqual(source.stage_status("website_resolution"), "failed")
            (root / "results.json").write_text(
                json.dumps([dataclass_to_dict(source.trace_record())]),
                encoding="utf-8",
            )

            manifest = replay_failure_bundle(
                self._args(
                    root,
                    pipeline_status=None,
                    stage=None,
                    stage_status=None,
                    reason_code=None,
                    legacy_run_config=None,
                )
            )

        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["summary"]["total"], 1)
        self.assertEqual(manifest["record_integrity"]["status"], "passed")
        self.assertEqual(manifest["outcome_gate"]["status"], "passed")
        self.assertEqual(
            manifest["outcome_gate"]["classification_counts"]["reproduced"],
            1,
        )
        self.assertEqual(manifest["snapshot_summary"]["scope_count"], 3)

    def test_scoped_career_replay_executes_website_evidence_producer(self):
        company = CompanyInput(
            company_name="Localized",
            company_website_url="https://localized.example",
            job_title="Engineer",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = build_application(
                FetcherConfig(
                    fixtures_dir=Path(__file__).resolve().parents[1] / "samples" / "sites",
                    offline=True,
                    snapshot_dir=root / "snapshots",
                )
            )
            source = application.pipeline.discover(
                company,
                capture_attempt_id="capture-career-producer-replay",
            )
            self.assertEqual(source.stage_status("website_resolution"), "success")
            self.assertEqual(source.stage_status("career_discovery"), "failed")
            (root / "results.json").write_text(
                json.dumps([dataclass_to_dict(source.trace_record())]),
                encoding="utf-8",
            )

            manifest = replay_failure_bundle(
                self._args(
                    root,
                    pipeline_status=None,
                    stage=None,
                    stage_status=None,
                    reason_code=None,
                    legacy_run_config=None,
                )
            )
            replay_trace = json.loads(
                (root / "bundle" / "replay-trace.json").read_text(encoding="utf-8")
            )[0]

        checkpoint_events = replay_trace["trace"]["checkpoint_events"]
        website_actions = [
            event["action"]
            for event in checkpoint_events
            if event["stage"] == "website_resolution"
        ]
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["outcome_gate"]["status"], "passed")
        self.assertEqual(
            manifest["outcome_gate"]["classification_counts"]["reproduced"],
            1,
        )
        self.assertIn("save", website_actions)
        self.assertNotIn("restore", website_actions)

    def test_scoped_timeout_without_failure_stage_boundary_writes_failed_manifest(self):
        company = CompanyInput(
            company_name="Aurora Data",
            company_website_url="https://aurora-data.example",
            job_title="AI Algorithm Engineer Intern",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = build_application(
                FetcherConfig(
                    fixtures_dir=Path(__file__).resolve().parents[1] / "samples" / "sites",
                    offline=True,
                    snapshot_dir=root / "snapshots",
                )
            )
            source = application.pipeline.discover(
                company,
                capture_attempt_id="capture-killed-during-s2",
            )
            record = dataclass_to_dict(source.trace_record())
            record["pipeline_status"] = "failed"
            record["status"] = "failed"
            for stage in record["stages"]:
                if stage["stage"] == "website_resolution":
                    stage.update(
                        {
                            "status": "failed",
                            "reason_code": "COMPANY_TIME_BUDGET_EXHAUSTED",
                            "retryable": True,
                        }
                    )
                elif stage["stage"] not in {"linkedin_discovery", "result_validation"}:
                    stage.update({"status": "not_run", "reason_code": None})
            lineage = record["trace"]["stage_evidence_lineage"]
            record["trace"]["stage_evidence_lineage"] = lineage[:1]
            (root / "results.json").write_text(
                json.dumps([record]),
                encoding="utf-8",
            )

            manifest = replay_failure_bundle(
                self._args(
                    root,
                    pipeline_status=["failed"],
                    stage=None,
                    stage_status=None,
                    reason_code=["COMPANY_TIME_BUDGET_EXHAUSTED"],
                    include_missing_website=True,
                    legacy_run_config=None,
                ),
                allow_empty=True,
            )
            written = json.loads(
                (root / "bundle" / "bundle-manifest.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(manifest, written)
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["reason"], "replay_plan_integrity_failed")
        self.assertEqual(manifest["outcome_gate"]["status"], "failed")
        self.assertEqual(
            manifest["record_integrity"]["counts"]["boundary_invalid_count"],
            1,
        )
        boundary_reason = next(
            reason
            for reason in manifest["record_integrity"]["reasons"]
            if reason["code"] == "captured_execution_boundary_missing"
        )
        self.assertEqual(boundary_reason["records"][0]["start_stage"], "website_resolution")
        self.assertEqual(boundary_reason["records"][0]["missing_stages"], ["website_resolution"])
        self.assertFalse((root / "bundle" / "replay-input.json").exists())

    def test_scoped_duplicate_inputs_use_independent_tape_cursors_and_checkpoints(self):
        company = CompanyInput(
            company_name="Aurora Data",
            company_website_url="https://aurora-data.example",
            job_title="AI Algorithm Engineer Intern",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = build_application(
                FetcherConfig(
                    fixtures_dir=Path(__file__).resolve().parents[1] / "samples" / "sites",
                    offline=True,
                    snapshot_dir=root / "snapshots",
                )
            )
            source = dataclass_to_dict(
                application.pipeline.discover(
                    company,
                    capture_attempt_id="capture-attempt-duplicates",
                ).trace_record()
            )
            (root / "results.json").write_text(
                json.dumps([source, source]),
                encoding="utf-8",
            )

            manifest = replay_failure_bundle(
                self._args(
                    root,
                    pipeline_status=None,
                    stage=None,
                    stage_status=None,
                    reason_code=None,
                    legacy_run_config=None,
                )
            )
            checkpoint_roots = list(
                (root / "bundle" / "checkpoints" / "records").iterdir()
            )

        record_ids = [plan["record_id"] for plan in manifest["record_plans"]]
        self.assertEqual(manifest["outcome_gate"]["status"], "passed")
        self.assertEqual(manifest["summary"]["total"], 2)
        self.assertEqual(len(set(record_ids)), 2)
        self.assertEqual(len(checkpoint_roots), 2)

    def test_source_run_configuration_is_replayed_faithfully_and_not_exported_as_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            source_config = DeterministicRunConfig.from_agent_config(
                AgentConfig(
                    max_candidates=19,
                    max_job_pages=7,
                    max_career_candidate_fetches=11,
                    max_career_search_queries=2,
                    max_ats_board_fetches=3,
                    enable_sitemap_discovery=False,
                    enable_career_search=False,
                    career_search_timeout=4.5,
                )
            )
            results_path = root / "results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results[0]["linkedin_job_title"] = "Missing Role"
            results[0]["run_configuration"] = source_config.to_payload()
            results[0]["run_configuration_digest"] = source_config.digest
            results_path.write_text(json.dumps(results), encoding="utf-8")

            manifest = replay_failure_bundle(self._args(root, legacy_run_config=None))
            replay_input = json.loads(
                (root / "bundle" / "replay-input.json").read_text(encoding="utf-8")
            )
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest["bundle_schema_version"], 5)
        self.assertEqual(manifest["run_configuration"], source_config.to_payload())
        self.assertEqual(manifest["run_configuration_digest"], source_config.digest)
        self.assertEqual(manifest["run_configuration_provenance"], "source_record")
        self.assertEqual(replay_results[0]["run_configuration"], source_config.to_payload())
        self.assertNotIn("run_configuration", replay_input[0])
        self.assertNotIn("run_configuration_digest", replay_input[0])

    def test_legacy_versioned_run_configuration_preserves_checkpoint_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            current = DeterministicRunConfig.from_agent_config(
                AgentConfig(enable_sitemap_discovery=False)
            ).to_payload()
            legacy_payload = {
                "schema_version": "1.0",
                "agent": {
                    key: value
                    for key, value in current["agent"].items()
                    if key not in {
                        "max_career_discovery_transport_calls",
                        "max_job_board_attempts",
                        "enable_parallel_candidate_discovery",
                        "evaluate_all_candidate_routes",
                    }
                },
            }
            legacy_config = DeterministicRunConfig.from_payload(legacy_payload)
            results_path = root / "results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results[0]["linkedin_job_title"] = "Missing Role"
            results[0]["run_configuration"] = legacy_payload
            results[0]["run_configuration_digest"] = legacy_config.digest
            results_path.write_text(json.dumps(results), encoding="utf-8")

            manifest = replay_failure_bundle(self._args(root, legacy_run_config=None))
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest["run_configuration"], legacy_payload)
        self.assertEqual(manifest["run_configuration_digest"], legacy_config.digest)
        self.assertEqual(replay_results[0]["run_configuration"], legacy_payload)
        self.assertEqual(
            replay_results[0]["run_configuration_digest"],
            legacy_config.digest,
        )

    def test_legacy_source_requires_explicit_configuration_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)

            with self.assertRaisesRegex(FailureReplayError, "legacy-run-config"):
                replay_failure_bundle(self._args(root, legacy_run_config=None))

    def test_reusing_bundle_output_removes_stale_checkpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            results_path = root / "results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results[0]["linkedin_job_title"] = "Missing Role"
            results_path.write_text(json.dumps(results), encoding="utf-8")
            args = self._args(root)
            replay_failure_bundle(args)
            stale = root / "bundle" / "checkpoints" / "stale.txt"
            stale.write_text("stale", encoding="utf-8")

            replay_failure_bundle(args)

            self.assertFalse(stale.exists())

    def test_replay_restores_successful_upstream_handoffs_before_first_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board_url = "https://jobs.example.test/jobs"
            current = DeterministicRunConfig.from_agent_config(
                AgentConfig(enable_sitemap_discovery=False)
            ).to_payload()
            legacy_payload = {
                "schema_version": "1.0",
                "agent": {
                    key: value
                    for key, value in current["agent"].items()
                    if key not in {
                        "max_career_discovery_transport_calls",
                        "max_job_board_attempts",
                        "enable_parallel_candidate_discovery",
                        "evaluate_all_candidate_routes",
                    }
                },
            }
            legacy_config = DeterministicRunConfig.from_payload(legacy_payload)
            results = [{
                "company_name": "Shared Name",
                "company_website_url": "https://authoritative.example.test",
                "hiring_entity_name": "Authoritative Hiring Entity",
                "career_root_url": "https://authoritative.example.test/careers",
                "career_page_url": "https://authoritative.example.test/careers",
                "job_list_page_url": board_url,
                "linkedin_job_title": "Missing Role",
                "pipeline_status": "partial",
                "run_configuration": legacy_payload,
                "run_configuration_digest": legacy_config.digest,
                "trace": {"stages": {"website_resolution": {"private": "do-not-copy"}}},
                "stages": [
                    {"stage": "linkedin_discovery", "status": "not_applicable"},
                    {"stage": "website_resolution", "status": "success"},
                    {"stage": "hiring_identity_resolution", "status": "success"},
                    {"stage": "career_discovery", "status": "success"},
                    {"stage": "job_board_discovery", "status": "success"},
                    {
                        "stage": "opening_match",
                        "status": "partial",
                        "reason_code": "OPENING_NOT_FOUND",
                    },
                    {"stage": "result_validation", "status": "success"},
                ],
            }]
            (root / "results.json").write_text(json.dumps(results), encoding="utf-8")
            SnapshotStore(root / "snapshots").write_page(
                Page(
                    url=board_url,
                    final_url=board_url,
                    html="<html><body><p>No matching role.</p></body></html>",
                    source="live",
                ),
                request_url=board_url,
            )

            with patch(
                "job_source_agent.stages.upstream.WebsiteResolutionStage.run",
                side_effect=AssertionError("website resolution must not rerun"),
            ), patch(
                "job_source_agent.stages.upstream.HiringIdentityResolutionStage.run",
                side_effect=AssertionError("entity resolution must not rerun"),
            ):
                manifest = replay_failure_bundle(self._args(root))
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )
            replay_trace = json.loads(
                (root / "bundle" / "replay-trace.json").read_text(encoding="utf-8")
            )
            checkpoint_text = "".join(
                path.read_text(encoding="utf-8")
                for path in (root / "bundle" / "checkpoints").rglob("*.json")
            )

        self.assertEqual(
            replay_results[0]["company_website_url"],
            "https://authoritative.example.test",
        )
        self.assertEqual(
            replay_results[0]["hiring_entity_name"],
            "Authoritative Hiring Entity",
        )
        self.assertEqual(
            [
                event["stage"]
                for event in replay_trace[0]["trace"]["checkpoint_events"]
                if event["action"] == "restore"
            ],
            [
                "linkedin_discovery",
                "website_resolution",
                "hiring_identity_resolution",
                "career_discovery",
                "job_board_discovery",
            ],
        )
        self.assertEqual(manifest["summary"]["checkpoint_action_counts"]["save"], 2)
        self.assertEqual(manifest["summary"]["checkpoint_action_counts"]["restore"], 5)
        self.assertEqual(manifest["run_configuration"], legacy_payload)
        self.assertEqual(manifest["run_configuration_digest"], legacy_config.digest)
        self.assertEqual(replay_results[0]["run_configuration"], legacy_payload)
        self.assertEqual(
            replay_results[0]["run_configuration_digest"],
            legacy_config.digest,
        )
        self.assertNotIn("do-not-copy", checkpoint_text)

    def test_results_only_page_aware_provider_reruns_job_board_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board_url = "https://careers.example.com/global/en/search-results"
            search_url = board_url + "?keywords=Missing+Role"

            def phenom_html(*, total_hits: int) -> str:
                config = {
                    "cdnUrl": "https://cdn.phenompeople.com/CareerConnectResources",
                    "pageName": "search-results",
                    "refNum": "ACMEGLOBAL",
                    "baseUrl": "https://careers.example.com/global/en/",
                }
                ddo = {
                    "eagerLoadRefineSearch": {
                        "hits": 0,
                        "totalHits": total_hits,
                        "data": {"jobs": []},
                    }
                }
                return (
                    "<html><body><script>"
                    f"var phApp = {json.dumps(config)};"
                    f"phApp.ddo = {json.dumps(ddo)};"
                    "</script></body></html>"
                )

            results = [{
                "company_name": "Page Aware Example",
                "company_website_url": "https://example.com",
                "career_root_url": board_url,
                "career_page_url": board_url,
                "job_list_page_url": board_url,
                "linkedin_job_title": "Missing Role",
                "pipeline_status": "partial",
                "stages": [
                    {"stage": "linkedin_discovery", "status": "not_applicable"},
                    {"stage": "website_resolution", "status": "success"},
                    {"stage": "hiring_identity_resolution", "status": "success"},
                    {"stage": "career_discovery", "status": "success"},
                    {
                        "stage": "job_board_discovery",
                        "status": "success",
                        "provider": "phenom",
                    },
                    {
                        "stage": "opening_match",
                        "status": "partial",
                        "reason_code": "OPENING_NOT_FOUND",
                    },
                    {"stage": "result_validation", "status": "success"},
                ],
            }]
            (root / "results.json").write_text(json.dumps(results), encoding="utf-8")
            snapshots = SnapshotStore(root / "snapshots")
            snapshots.write_page(
                Page(url=board_url, html=phenom_html(total_hits=0), source="live"),
                request_url=board_url,
            )
            snapshots.write_page(
                Page(url=search_url, html=phenom_html(total_hits=0), source="live"),
                request_url=search_url,
            )

            manifest = replay_failure_bundle(self._args(root))
            replay_input = json.loads(
                (root / "bundle" / "replay-input.json").read_text(encoding="utf-8")
            )
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )
            replay_trace = json.loads(
                (root / "bundle" / "replay-trace.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest["outcome_gate"]["status"], "passed")
        self.assertEqual(
            manifest["outcome_gate"]["records"][0]["classification"],
            "reproduced",
        )
        self.assertEqual(replay_results[0]["pipeline_status"], "partial")
        self.assertEqual(
            next(
                stage["reason_code"]
                for stage in replay_results[0]["stages"]
                if stage["stage"] == "opening_match"
            ),
            "OPENING_NOT_FOUND",
        )
        self.assertNotIn("OFFLINE_FIXTURE_MISSING", json.dumps(replay_results))
        self.assertNotIn("provider_detection", json.dumps(replay_input))
        self.assertEqual(
            replay_trace[0]["trace"]["stages"]["opening_match"]["provider_api"]
            ["provider_detection"],
            {
                "method": "typed_stage_handoff",
                "source_method": "page_evidence",
                "provider": "phenom",
                "url": board_url,
                "evidence_url": board_url,
            },
        )
        checkpoint_events = replay_trace[0]["trace"]["checkpoint_events"]
        self.assertIn(
            {"stage": "job_board_discovery", "action": "save"},
            [
                {"stage": event["stage"], "action": event["action"]}
                for event in checkpoint_events
            ],
        )
        self.assertNotIn(
            {"stage": "job_board_discovery", "action": "restore"},
            [
                {"stage": event["stage"], "action": event["action"]}
                for event in checkpoint_events
            ],
        )

    def test_trace_page_derived_methods_resume_at_job_board_discovery(self):
        for method in ("page_evidence", "page_probe"):
            with self.subTest(method=method):
                source_record = {
                    "trace": {"stages": {"job_board_discovery": {
                        "provider_detection": {"method": method},
                    }}},
                }

                self.assertEqual(
                    _replay_resume_stage(source_record, "opening_match"),
                    "job_board_discovery",
                )

    def test_generic_page_inventory_resumes_at_job_board_discovery(self):
        source_record = {
            "job_list_page_url": "https://careers.example.com/search-jobs",
            "stages": [{
                "stage": "job_board_discovery",
                "status": "success",
                "provider": "careers.example.com",
            }],
            "trace": {"stages": {"job_board_discovery": {
                "pages_visited": [{
                    "url": "https://careers.example.com/search-jobs",
                    "source": "live|snapshot:sites/careers.example.com/index.html",
                }],
                "selected_page_source": (
                    "live|snapshot:sites/careers.example.com/index.html"
                ),
            }}},
        }

        self.assertEqual(
            _replay_resume_stage(source_record, "opening_match"),
            "job_board_discovery",
        )

    def test_results_only_url_native_provider_resumes_at_opening_match(self):
        source_record = {
            "job_list_page_url": "https://boards.greenhouse.io/example/jobs/123",
            "stages": [{
                "stage": "job_board_discovery",
                "status": "success",
                "provider": "greenhouse",
            }],
        }

        self.assertEqual(
            _replay_resume_stage(source_record, "opening_match"),
            "opening_match",
        )

    def test_result_validation_rejection_replays_opening_when_s7_cleared_output(self):
        source_record = {
            "open_position_url": None,
            "stages": [
                {"stage": "opening_match", "status": "success"},
                {
                    "stage": "result_validation",
                    "status": "failed",
                    "reason_code": "RESULT_IDENTITY_MISMATCH",
                },
            ],
        }

        self.assertEqual(
            _replay_resume_stage(source_record, "result_validation"),
            "opening_match",
        )

    def test_results_fallback_fails_closed_for_invalid_provider_data(self):
        records = (
            {
                "job_list_page_url": "https://careers.example.com/search-results",
                "stages": [{"stage": "job_board_discovery", "provider": "unknown"}],
            },
            {
                "job_list_page_url": "https://careers.example.com/search-results",
                "stages": [{"stage": "job_board_discovery"}],
            },
            {
                "job_list_page_url": {"url": "https://careers.example.com"},
                "stages": [{"stage": "job_board_discovery", "provider": "phenom"}],
            },
            {
                "job_list_page_url": "not-a-url",
                "stages": [{"stage": "job_board_discovery", "provider": "phenom"}],
            },
        )

        for source_record in records:
            with self.subTest(source_record=source_record):
                self.assertEqual(
                    _replay_resume_stage(source_record, "opening_match"),
                    "opening_match",
                )

    def test_explicit_trace_method_does_not_use_results_fallback(self):
        source_record = {
            "job_list_page_url": "https://careers.example.com/search-results",
            "stages": [{"stage": "job_board_discovery", "provider": "phenom"}],
            "trace": {"stages": {"job_board_discovery": {
                "provider_detection": {"method": "linked_url_evidence"},
            }}},
        }

        self.assertEqual(
            _replay_resume_stage(source_record, "opening_match"),
            "opening_match",
        )

    def test_non_opening_failure_keeps_original_resume_stage(self):
        source_record = {
            "job_list_page_url": "https://careers.example.com/search-results",
            "stages": [{"stage": "job_board_discovery", "provider": "phenom"}],
        }

        self.assertEqual(
            _replay_resume_stage(source_record, "job_board_discovery"),
            "job_board_discovery",
        )

    def test_scoped_career_replay_resumes_at_navigation_evidence_producer(self):
        replay_record = {
            "source_trace": {"replay": {"first_non_success_stage": {
                "stage": "career_discovery",
            }}},
        }
        scoped_plan = SimpleNamespace(evidence_mode="scoped_outcome_tape")
        legacy_plan = SimpleNamespace(evidence_mode="legacy_global_latest")

        self.assertEqual(
            _effective_replay_resume_stage({}, replay_record, scoped_plan),
            "website_resolution",
        )
        self.assertEqual(
            _effective_replay_resume_stage({}, replay_record, legacy_plan),
            "career_discovery",
        )

    def test_scoped_page_derived_opening_keeps_captured_stage_boundary(self):
        source_record = {
            "job_list_page_url": "https://careers.example.com/search-jobs",
            "stages": [{
                "stage": "job_board_discovery",
                "status": "success",
                "provider": "careers.example.com",
            }],
            "trace": {"stages": {"job_board_discovery": {
                "pages_visited": [{
                    "url": "https://careers.example.com/search-jobs",
                }],
            }}},
        }
        replay_record = {
            "source_trace": {"replay": {"first_non_success_stage": {
                "stage": "opening_match",
            }}},
        }
        scoped_plan = SimpleNamespace(evidence_mode="scoped_outcome_tape")

        self.assertEqual(
            _effective_replay_resume_stage(
                source_record,
                replay_record,
                scoped_plan,
            ),
            "opening_match",
        )

    def test_scoped_opening_replay_keeps_captured_stage_boundary(self):
        source_record = {
            "job_list_page_url": "https://careers.example.test/jobs/corporate",
            "trace": {"stages": {"job_board_discovery": {
                "job_board_portfolio": {
                    "eligible_count": 2,
                    "eligible_set_complete": False,
                    "primary_url": "https://careers.example.test/jobs/corporate",
                },
            }}},
        }
        replay_record = {
            "source_trace": {"replay": {"first_non_success_stage": {
                "stage": "opening_match",
            }}},
        }
        scoped_plan = SimpleNamespace(evidence_mode="scoped_outcome_tape")
        legacy_plan = SimpleNamespace(evidence_mode="legacy_global_latest")

        self.assertEqual(
            _effective_replay_resume_stage(
                source_record,
                replay_record,
                scoped_plan,
            ),
            "opening_match",
        )
        self.assertEqual(
            _effective_replay_resume_stage(
                source_record,
                replay_record,
                legacy_plan,
            ),
            "opening_match",
        )

    def test_scoped_replay_uses_recorded_two_phase_boundary_with_failed_upstream(self):
        source_record = {
            "company_website_url": "",
            "career_page_url": "https://example.test/careers",
            "job_list_page_url": "https://example.test/careers",
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "failed"},
                {"stage": "hiring_identity_resolution", "status": "not_run"},
                {"stage": "career_discovery", "status": "success"},
                {
                    "stage": "job_board_discovery",
                    "status": "success",
                    "evidence": [{
                        "field": "job_list_page_url",
                        "url": "https://example.test/careers",
                    }],
                },
                {"stage": "opening_match", "status": "success"},
                {"stage": "result_validation", "status": "success"},
            ],
            "trace": {
                "checkpoint_events": [
                    {"stage": "opening_match", "action": "invalidate_from"},
                    *[
                        {"stage": stage, "action": "restore"}
                        for stage in PIPELINE_STAGES[:5]
                    ],
                    {"stage": "opening_match", "action": "save"},
                    {"stage": "result_validation", "action": "save"},
                ]
            },
        }
        replay_record = {
            "source_trace": {"replay": {"first_non_success_stage": {
                "stage": "website_resolution",
            }}},
        }
        scoped_plan = SimpleNamespace(evidence_mode="scoped_outcome_tape")

        self.assertEqual(
            _effective_replay_resume_stage(
                source_record,
                replay_record,
                scoped_plan,
            ),
            "opening_match",
        )
        executions = _authoritative_upstream_executions(
            source_record,
            "opening_match",
            scoped_stage_evidence=True,
        )
        self.assertIsNotNone(executions)
        self.assertEqual(
            [execution.result.status for execution in executions],
            ["success", "failed", "not_run", "success", "success"],
        )

    def test_scoped_opening_preflight_rejects_incomplete_portfolio_seed(self):
        replay_record = {
            "company_name": "Example",
            "source_trace": {"replay": {"first_non_success_stage": {
                "stage": "opening_match",
            }}},
        }
        source_record = {
            "company_name": "Example",
            "company_website_url": "https://example.test",
            "career_page_url": "https://example.test/careers",
            "job_list_page_url": "https://boards.greenhouse.io/example",
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {"stage": "career_discovery", "status": "success"},
                {
                    "stage": "job_board_discovery",
                    "status": "success",
                    "provider": "greenhouse",
                },
                {"stage": "opening_match", "status": "partial"},
            ],
            "trace": {"stages": {
                "job_board_discovery": {
                    "job_board_portfolio": {
                        "eligible_count": 2,
                        "eligible_set_complete": False,
                        "primary_provider": "greenhouse",
                        "primary_url": "https://boards.greenhouse.io/example",
                    },
                    "job_list_page_url": "https://boards.greenhouse.io/example",
                    "provider_detection": {
                        "method": "linked_url_evidence",
                        "provider": "greenhouse",
                        "url": "https://boards.greenhouse.io/example",
                    },
                },
                "opening_match": {"board_portfolio": {"attempts": []}},
            }},
        }
        plan = SimpleNamespace(
            evidence_mode="scoped_outcome_tape",
            record_id="a" * 64,
            stage_evidence_lineage=tuple(
                SimpleNamespace(stage=stage)
                for stage in PIPELINE_STAGES[5:]
            ),
        )

        errors = _scoped_execution_boundary_errors(
            [source_record],
            [replay_record],
            (plan,),
        )

        self.assertEqual(errors[0]["reason_code"], "scoped_stage_seed_ambiguous")
        self.assertIn("do not cover", errors[0]["detail"])

    def test_scoped_preflight_requires_producer_tape_for_career_replay(self):
        replay_record = {
            "company_name": "Example",
            "source_trace": {"replay": {"first_non_success_stage": {
                "stage": "career_discovery",
            }}},
        }
        source_record = {
            "company_name": "Example",
            "company_website_url": "https://example.test",
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {
                    "stage": "career_discovery",
                    "status": "failed",
                    "reason_code": "CAREER_PAGE_NOT_FOUND",
                },
            ],
        }
        plan = SimpleNamespace(
            evidence_mode="scoped_outcome_tape",
            record_id="a" * 64,
            stage_evidence_lineage=(
                SimpleNamespace(stage="hiring_identity_resolution"),
                SimpleNamespace(stage="career_discovery"),
            ),
        )

        errors = _scoped_execution_boundary_errors(
            [source_record],
            [replay_record],
            (plan,),
        )

        self.assertEqual(errors[0]["start_stage"], "website_resolution")
        self.assertEqual(errors[0]["missing_stages"], ["website_resolution"])

    def test_improved_replay_is_mismatch_and_cli_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            args = self._args(root)

            manifest = replay_failure_bundle(args)
            written = json.loads(
                (root / "bundle" / "bundle-manifest.json").read_text(encoding="utf-8")
            )
            cli_args = [
                "--results", args.results,
                "--snapshot-dir", args.snapshot_dir,
                "--output-dir", str(root / "cli-bundle"),
                "--pipeline-status", "partial",
                "--stage", "opening_match",
                "--stage-status", "partial",
                "--reason-code", "OPENING_NOT_FOUND",
                "--legacy-run-config", "composition-defaults",
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "1 outcome mismatch"):
                    main(cli_args)

        self.assertEqual(manifest, written)
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["outcome_gate"]["status"], "failed")
        comparison = manifest["outcome_gate"]["records"][0]
        self.assertEqual(comparison["classification"], "mismatch")
        self.assertEqual(comparison["reason"], "outcome_changed")
        self.assertEqual(comparison["replay_outcome"]["pipeline_status"], "success")

    def test_offline_fixture_failure_is_classified_as_fixture_gap(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "job_title": "Data Analyst",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "linkedin_job_title": "Data Analyst",
            "pipeline_status": "failed",
            "stages": [{
                "stage": "opening_match",
                "status": "failed",
                "reason_code": "OFFLINE_FIXTURE_MISSING",
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "incomplete")
        self.assertEqual(
            gate["classification_counts"],
            {
                "reproduced": 0,
                "expected_transition": 0,
                "budget_recovery": 0,
                "fixture_gap": 1,
                "mismatch": 0,
            },
        )
        self.assertEqual(gate["records"][0]["classification"], "fixture_gap")
        self.assertEqual(gate["records"][0]["reason"], "offline_fixture_missing")

    def test_nested_offline_fixture_reason_in_result_or_trace_is_fixture_gap(self):
        replay_inputs = [{
            "company_name": "Adobe",
            "source_trace": {"replay": {
                "pipeline_status": "failed",
                "first_non_success_stage": {
                    "stage": "career_discovery",
                    "status": "failed",
                    "reason_code": "CAREER_PAGE_NOT_FOUND",
                },
            }},
        }]
        replay_results = [{
            "company_name": "Adobe",
            "pipeline_status": "failed",
            "stages": [{
                "stage": "career_discovery",
                "status": "failed",
                "reason_code": "CAREER_PAGE_NOT_FOUND",
            }],
        }]
        nested_fixture_reason = {
            "trace": {
                "attempts": [[{
                    "reason_code": "OFFLINE_FIXTURE_MISSING",
                }]],
            },
        }

        for location in ("result", "trace"):
            with self.subTest(location=location):
                result = {
                    **replay_results[0],
                    **(nested_fixture_reason if location == "result" else {}),
                }
                traces = [nested_fixture_reason] if location == "trace" else None
                gate = _build_outcome_gate(
                    replay_inputs,
                    [result],
                    trace_records=traces,
                )

                self.assertEqual(gate["status"], "incomplete")
                self.assertEqual(gate["classification_counts"]["fixture_gap"], 1)
                self.assertEqual(gate["classification_counts"]["reproduced"], 0)
                self.assertEqual(gate["records"][0]["classification"], "fixture_gap")

    def test_equal_success_identity_ignores_unused_fixture_probe_gap(self):
        source = {
            "company_name": "Example Streaming",
            "company_website_url": "https://example.test",
            "career_page_url": "https://jobs.example.test",
            "job_list_page_url": "https://jobs.example.test/careers",
            "open_position_url": "https://jobs.example.test/careers/job/123",
            "pipeline_status": "success",
            "stages": [
                {"stage": stage, "status": "success"}
                for stage in PIPELINE_STAGES
            ],
        }
        replay_trace = {
            "trace": {
                "unused_probe": {
                    "reason_code": "OFFLINE_FIXTURE_MISSING",
                },
            },
        }

        gate = _build_outcome_gate(
            [{"company_name": "Example Streaming"}],
            [source],
            trace_records=[replay_trace],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["reproduced"], 1)
        self.assertEqual(gate["classification_counts"]["fixture_gap"], 0)

    def test_success_identity_drift_does_not_hide_fixture_gap(self):
        source = {
            "company_name": "Example Streaming",
            "company_website_url": "https://example.test",
            "career_page_url": "https://jobs.example.test",
            "job_list_page_url": "https://jobs.example.test/careers",
            "open_position_url": "https://jobs.example.test/careers/job/123",
            "pipeline_status": "success",
            "stages": [
                {"stage": stage, "status": "success"}
                for stage in PIPELINE_STAGES
            ],
        }
        replayed = {
            **source,
            "career_page_url": "https://jobs.example.test/search",
        }
        replay_trace = {
            "trace": {
                "probe": {
                    "reason_code": "OFFLINE_FIXTURE_MISSING",
                },
            },
        }

        gate = _build_outcome_gate(
            [{"company_name": "Example Streaming"}],
            [replayed],
            trace_records=[replay_trace],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "incomplete")
        self.assertEqual(gate["classification_counts"]["fixture_gap"], 1)

    def test_provider_declared_board_routes_have_equal_replay_identity(self):
        stages = [
            {
                "stage": stage,
                "status": "success",
                **(
                    {"provider": "google_careers"}
                    if stage in {"job_board_discovery", "opening_match"}
                    else {}
                ),
            }
            for stage in PIPELINE_STAGES
        ]
        source = {
            "company_name": "Example Search",
            "company_website_url": "https://www.google.com",
            "career_page_url": "https://www.google.com/about/careers/applications/",
            "job_list_page_url": "https://www.google.com/about/careers/applications/",
            "open_position_url": (
                "https://www.google.com/about/careers/applications/jobs/results/"
                "123-product-manager"
            ),
            "pipeline_status": "success",
            "stages": stages,
        }
        replayed = {
            **source,
            "career_page_url": (
                "https://www.google.com/about/careers/applications/jobs/results/"
            ),
            "job_list_page_url": (
                "https://www.google.com/about/careers/applications/jobs/results/"
            ),
        }

        gate = _build_outcome_gate(
            [{"company_name": "Example Search"}],
            [replayed],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["reproduced"], 1)

    def test_offline_fixture_text_without_typed_reason_is_not_fixture_gap(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "source_trace": {"replay": {
                "pipeline_status": "failed",
                "first_non_success_stage": {
                    "stage": "career_discovery",
                    "status": "failed",
                    "reason_code": "CAREER_PAGE_NOT_FOUND",
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "pipeline_status": "failed",
            "stages": [{
                "stage": "career_discovery",
                "status": "failed",
                "reason_code": "CAREER_PAGE_NOT_FOUND",
            }],
        }]
        replay_traces = [{
            "trace": {
                "error": "OFFLINE_FIXTURE_MISSING: no fixture found",
                "detail": ["reason_code=OFFLINE_FIXTURE_MISSING"],
            },
        }]

        gate = _build_outcome_gate(
            replay_inputs,
            replay_results,
            trace_records=replay_traces,
        )

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["fixture_gap"], 0)
        self.assertEqual(gate["classification_counts"]["reproduced"], 1)

    def test_cli_exits_nonzero_for_fixture_gap(self):
        manifest = {
            "summary": {"total": 1},
            "outcome_gate": {
                "status": "incomplete",
                "classification_counts": {"mismatch": 0, "fixture_gap": 1},
            },
        }
        cli_args = [
            "--results", "results.json",
            "--snapshot-dir", "snapshots",
            "--output-dir", "bundle",
        ]
        with patch(
            "scripts.replay_failure_bundle.replay_failure_bundle",
            return_value=manifest,
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "1 fixture gap"):
                    main(cli_args)

    def test_explicit_expected_transition_is_the_only_allowed_outcome_change(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                "expected_transition": {
                    "pipeline_status": "success",
                    "failure_stage": {
                        "stage": "opening_match",
                        "status": "success",
                        "reason_code": None,
                    },
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "pipeline_status": "success",
            "stages": [{
                "stage": "opening_match",
                "status": "success",
                "reason_code": None,
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["expected_transition"], 1)
        self.assertEqual(gate["records"][0]["classification"], "expected_transition")

    def test_expected_transition_can_move_to_a_different_failure_stage(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                "expected_transition": {
                    "pipeline_status": "failed",
                    "failure_stage": {
                        "stage": "career_discovery",
                        "status": "failed",
                        "reason_code": "CAREER_PAGE_NOT_FOUND",
                    },
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "pipeline_status": "failed",
            "stages": [{
                "stage": "career_discovery",
                "status": "failed",
                "reason_code": "CAREER_PAGE_NOT_FOUND",
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["expected_transition"], 1)

    def test_expected_transition_can_remove_the_failure_stage(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                "expected_transition": {
                    "pipeline_status": "success",
                    "failure_stage": None,
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "pipeline_status": "success",
            "stages": [{
                "stage": "opening_match",
                "status": "success",
                "reason_code": None,
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["records"][0]["replay_outcome"]["failure_stage"], None)

    def test_fixture_gap_cannot_be_declared_as_an_expected_transition(self):
        replay_inputs = [{
            "company_name": "Example Data",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                "expected_transition": {
                    "pipeline_status": "partial",
                    "failure_stage": {
                        "stage": "opening_match",
                        "status": "partial",
                        "reason_code": "OFFLINE_FIXTURE_MISSING",
                    },
                },
            }},
        }]
        replay_results = [{
            "company_name": "Example Data",
            "pipeline_status": "partial",
            "stages": [{
                "stage": "opening_match",
                "status": "partial",
                "reason_code": "OFFLINE_FIXTURE_MISSING",
            }],
        }]

        gate = _build_outcome_gate(replay_inputs, replay_results)

        self.assertEqual(gate["status"], "incomplete")
        self.assertEqual(gate["records"][0]["classification"], "fixture_gap")

    def test_company_budget_timeout_can_replay_to_later_structured_outcome(self):
        source = {
            "company_name": "Budget Example",
            "company_website_url": "https://budget.example",
            "career_root_url": "https://budget.example/career-root",
            "pipeline_status": "failed",
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {
                    "stage": "career_discovery",
                    "status": "failed",
                    "reason_code": "COMPANY_TIME_BUDGET_EXHAUSTED",
                },
                {"stage": "job_board_discovery", "status": "not_run"},
                {"stage": "opening_match", "status": "not_run"},
                {"stage": "result_validation", "status": "success"},
            ],
        }
        replayed = {
            **source,
            "pipeline_status": "partial",
            "career_page_url": "https://budget.example/careers",
            "job_list_page_url": "https://jobs.example.test/budget",
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {"stage": "career_discovery", "status": "success"},
                {
                    "stage": "job_board_discovery",
                    "status": "success",
                    "provider": "lever",
                },
                {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                {"stage": "result_validation", "status": "success"},
            ],
        }

        gate = _build_outcome_gate(
            [{"company_name": "Budget Example"}],
            [replayed],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["budget_recovery"], 1)
        comparison = gate["records"][0]
        self.assertEqual(comparison["classification"], "budget_recovery")
        self.assertEqual(comparison["reason"], "company_budget_replay_advanced")
        self.assertEqual(
            comparison["source_identity_prefix"],
            {
                "company_website_url": "https://budget.example",
                "hiring_entity_name": None,
                "career_root_url": "https://budget.example/career-root",
            },
        )
        self.assertEqual(
            comparison["replay_outcome"]["failure_stage"]["stage"],
            "opening_match",
        )
        self.assertEqual(
            comparison["replay_outcome"]["result_identity"]["job_list_page_url"],
            "https://jobs.example.test/budget",
        )

    def test_budget_recovery_rejects_established_identity_drift(self):
        source = {
            "company_name": "Budget Example",
            "company_website_url": "https://budget.example",
            "career_root_url": "https://budget.example/career-root",
            "pipeline_status": "failed",
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {
                    "stage": "career_discovery",
                    "status": "failed",
                    "reason_code": "COMPANY_TIME_BUDGET_EXHAUSTED",
                },
            ],
        }
        replayed = {
            **source,
            "career_root_url": "https://wrong.example/careers",
            "pipeline_status": "success",
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {"stage": "career_discovery", "status": "success"},
            ],
        }

        gate = _build_outcome_gate(
            [{"company_name": "Budget Example"}],
            [replayed],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["classification_counts"]["mismatch"], 1)

    def test_expected_transition_rejects_established_provider_identity_drift(self):
        replay_input = {
            "company_name": "Transition Example",
            "source_trace": {"replay": {
                "pipeline_status": "partial",
                "first_non_success_stage": {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
                "expected_transition": {
                    "pipeline_status": "success",
                    "failure_stage": None,
                },
            }},
        }
        source = {
            "company_name": "Transition Example",
            "company_website_url": "https://transition.example",
            "career_page_url": "https://transition.example/careers",
            "job_list_page_url": "https://jobs.example.test/transition",
            "pipeline_status": "partial",
            "stages": [
                {"stage": "linkedin_discovery", "status": "success"},
                {"stage": "website_resolution", "status": "success"},
                {"stage": "hiring_identity_resolution", "status": "success"},
                {"stage": "career_discovery", "status": "success"},
                {
                    "stage": "job_board_discovery",
                    "status": "success",
                    "provider": "greenhouse",
                },
                {
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                },
            ],
        }
        replayed = {
            **source,
            "pipeline_status": "success",
            "stages": [
                *source["stages"][:4],
                {
                    "stage": "job_board_discovery",
                    "status": "success",
                    "provider": "lever",
                },
                {"stage": "opening_match", "status": "success"},
            ],
        }

        gate = _build_outcome_gate(
            [replay_input],
            [replayed],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["classification_counts"]["mismatch"], 1)

    def test_replay_preserves_linkedin_native_only_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            career_url = "https://native.example/careers"
            job_url = "https://www.linkedin.com/jobs/view/808"
            results = [{
                "company_name": "Native Apply",
                "company_website_url": "https://native.example",
                "career_root_url": career_url,
                "linkedin_job_url": job_url,
                "linkedin_job_title": "AI Engineer",
                "pipeline_status": "partial",
                "stages": [{
                    "stage": "opening_match",
                    "status": "partial",
                    "reason_code": "OPENING_NOT_FOUND",
                }],
                "trace": {"source_trace": {"linkedin_posting": {
                    "availability": "active",
                    "apply_mode": "linkedin_native",
                    "evidence_source": "authenticated_detail_dom",
                    "job_url": job_url,
                    "observed_at": "2026-07-14T00:00:00Z",
                }}},
            }]
            (root / "results.json").write_text(json.dumps(results), encoding="utf-8")
            SnapshotStore(root / "snapshots").write_page(
                Page(
                    url=career_url,
                    final_url=career_url,
                    html=(
                        "<html><head><title>Careers - Native Apply</title></head>"
                        "<body><h1>Careers</h1>"
                        "<p>Join our team. Explore career opportunities.</p>"
                        "</body></html>"
                    ),
                    source="live",
                ),
                request_url=career_url,
            )

            replay_failure_bundle(self._args(root))
            replay_input = json.loads(
                (root / "bundle" / "replay-input.json").read_text(encoding="utf-8")
            )
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        job_board_stage = next(
            stage for stage in replay_results[0]["stages"]
            if stage["stage"] == "job_board_discovery"
        )
        self.assertEqual(job_board_stage["reason_code"], "LINKEDIN_NATIVE_ONLY")
        self.assertEqual(
            replay_input[0]["source_trace"]["linkedin_posting"]["apply_mode"],
            "linkedin_native",
        )
        self.assertNotIn("observed_at", replay_input[0]["source_trace"]["linkedin_posting"])

    def test_replay_preserves_explicitly_closed_posting_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            results_path = root / "results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results[0]["linkedin_job_title"] = "Missing Role"
            results[0]["trace"] = {
                "source_trace": {
                    "linkedin_posting": {
                        "availability": "closed",
                        "apply_mode": "unknown",
                        "evidence_source": "authenticated_detail_dom",
                        "job_url": "https://www.linkedin.com/jobs/view/909",
                    }
                }
            }
            results_path.write_text(json.dumps(results), encoding="utf-8")

            replay_failure_bundle(self._args(root))
            replay_results = json.loads(
                (root / "bundle" / "replay-results.json").read_text(encoding="utf-8")
            )

        opening_stage = next(
            stage for stage in replay_results[0]["stages"]
            if stage["stage"] == "opening_match"
        )
        self.assertEqual(opening_stage["reason_code"], "OPENING_CLOSED")
        self.assertEqual(
            opening_stage["evidence"][0]["source_posting_status"],
            "closed",
        )

    def test_successful_replay_with_changed_url_or_provider_is_mismatch(self):
        replay_input = [{"company_name": "Example"}]
        source = {
            "company_name": "Example",
            "pipeline_status": "success",
            "company_website_url": "https://example.test",
            "hiring_entity_name": "Example Holdings",
            "career_page_url": "https://example.test/careers",
            "job_list_page_url": "https://jobs.example.test/openings",
            "open_position_url": "https://jobs.example.test/openings/123",
            "stages": [{"stage": "job_board_discovery", "status": "success", "provider": "greenhouse"}],
        }

        for changed in (
            {**source, "open_position_url": "https://jobs.example.test/openings/456"},
            {
                **source,
                "stages": [{"stage": "job_board_discovery", "status": "success", "provider": "lever"}],
            },
        ):
            with self.subTest(changed=changed):
                gate = _build_outcome_gate(
                    replay_input,
                    [changed],
                    source_records=[source],
                )

                self.assertEqual(gate["status"], "failed")
                self.assertEqual(gate["classification_counts"]["mismatch"], 1)

    def test_canonical_trailing_slash_is_equal_for_successful_replay(self):
        replay_input = [{"company_name": "Example"}]
        source = {
            "company_name": "Example",
            "pipeline_status": "success",
            "company_website_url": "https://example.test/",
            "hiring_entity_name": " Example   Holdings ",
            "career_page_url": "https://example.test/careers/",
            "job_list_page_url": "https://jobs.example.test/openings/",
            "open_position_url": "https://jobs.example.test/openings/123/",
            "stages": [{"stage": "job_board_discovery", "status": "success", "provider": "greenhouse"}],
        }
        replayed = {
            **source,
            "company_website_url": "https://example.test",
            "hiring_entity_name": "example holdings",
            "career_page_url": "https://example.test/careers",
            "job_list_page_url": "https://jobs.example.test/openings",
            "open_position_url": "https://jobs.example.test/openings/123",
        }

        gate = _build_outcome_gate(
            replay_input,
            [replayed],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["classification_counts"]["reproduced"], 1)

    def test_verified_job_list_partial_pipeline_still_uses_identity_gate(self):
        source = {
            "company_name": "Example",
            "status": "success",
            "pipeline_status": "partial",
            "company_website_url": "https://example.test",
            "career_page_url": "https://example.test/careers",
            "job_list_page_url": "https://jobs.example.test/openings",
            "open_position_url": None,
            "stages": [
                {"stage": "opening_match", "status": "partial", "reason_code": "OPENING_NOT_FOUND"}
            ],
        }
        replayed = {**source, "job_list_page_url": "https://wrong.example/jobs"}

        gate = _build_outcome_gate(
            [{"company_name": "Example"}],
            [replayed],
            source_records=[source],
        )

        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["classification_counts"]["mismatch"], 1)

    def test_rejects_empty_filter_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)

            with self.assertRaisesRegex(FailureReplayError, "No replayable records"):
                replay_failure_bundle(
                    self._args(root, reason_code=["NETWORK_TIMEOUT"])
                )

    def test_full_outcome_integrity_blocks_one_missing_selected_record(self):
        args = SimpleNamespace(
            pipeline_status=None,
            stage=None,
            stage_status=None,
            reason_code=None,
            provider=None,
            limit=30,
        )
        integrity = _build_record_integrity(
            args,
            {
                "source_result_count": 30,
                "filter_matched_count": 30,
                "selected_count": 29,
                "export_attempted_count": 30,
                "exported_count": 29,
                "replayability_dropped_count": 1,
                "limit_omitted_count": 0,
            },
            result_count=29,
            trace_count=29,
            comparison_count=29,
        )

        self.assertEqual(integrity["status"], "failed")
        self.assertTrue(integrity["full_coverage_required"])
        self.assertEqual(integrity["counts"]["source_result_count"], 30)
        self.assertEqual(integrity["counts"]["comparison_count"], 29)
        self.assertEqual(
            {reason["code"] for reason in integrity["reasons"]},
            {
                "selection_count_mismatch",
                "export_count_mismatch",
                "result_count_mismatch",
                "trace_count_mismatch",
                "comparison_count_mismatch",
                "replayability_records_dropped",
            },
        )

    def test_full_outcome_integrity_passes_with_complete_counts(self):
        args = SimpleNamespace(
            pipeline_status=None,
            stage=None,
            stage_status=None,
            reason_code=None,
            provider=None,
            limit=None,
        )
        integrity = _build_record_integrity(
            args,
            {
                "source_result_count": 30,
                "filter_matched_count": 30,
                "selected_count": 30,
                "export_attempted_count": 30,
                "exported_count": 30,
                "replayability_dropped_count": 0,
                "limit_omitted_count": 0,
            },
            result_count=30,
            trace_count=30,
            comparison_count=30,
        )

        self.assertEqual(integrity["status"], "passed")
        self.assertTrue(integrity["full_coverage_required"])
        self.assertEqual(integrity["reasons"], [])

    def test_explicit_filter_or_small_limit_does_not_require_full_coverage(self):
        base_counts = {
            "source_result_count": 30,
            "filter_matched_count": 10,
            "selected_count": 10,
            "export_attempted_count": 9,
            "exported_count": 9,
            "replayability_dropped_count": 0,
            "limit_omitted_count": 1,
        }
        explicit_filter = _build_record_integrity(
            SimpleNamespace(
                pipeline_status=["failed"],
                stage=None,
                stage_status=None,
                reason_code=None,
                provider=None,
                limit=None,
            ),
            base_counts,
            result_count=9,
            trace_count=9,
            comparison_count=9,
        )
        small_limit = _build_record_integrity(
            SimpleNamespace(
                pipeline_status=None,
                stage=None,
                stage_status=None,
                reason_code=None,
                provider=None,
                limit=9,
            ),
            base_counts,
            result_count=9,
            trace_count=9,
            comparison_count=9,
        )

        self.assertEqual(explicit_filter["status"], "passed")
        self.assertFalse(explicit_filter["full_coverage_required"])
        self.assertEqual(
            explicit_filter["reasons"], [{"code": "explicit_failure_filters"}]
        )
        self.assertEqual(small_limit["status"], "passed")
        self.assertFalse(small_limit["full_coverage_required"])
        self.assertEqual(
            small_limit["reasons"][0]["code"], "limit_below_source_count"
        )

    def test_export_counts_replayability_drop_across_thirty_source_results(self):
        records = [
            {
                "company_name": f"Company {index}",
                "company_website_url": (
                    "" if index == 29 else f"https://company-{index}.example"
                ),
                "pipeline_status": "success",
            }
            for index in range(30)
        ]
        export_args = SimpleNamespace(
            input="results.json",
            pipeline_status=None,
            stage=None,
            stage_status=None,
            reason_code=None,
            provider=None,
            limit=30,
            include_missing_website=False,
        )

        replay_records, source_records, counts = _export_replay_records_with_sources(
            records,
            export_args,
        )

        self.assertEqual(len(replay_records), 29)
        self.assertEqual(len(source_records), 29)
        self.assertEqual(
            counts,
            {
                "source_result_count": 30,
                "filter_matched_count": 30,
                "selected_count": 29,
                "export_attempted_count": 30,
                "exported_count": 29,
                "replayability_dropped_count": 1,
                "limit_omitted_count": 0,
            },
        )

    def test_full_outcome_bundle_fails_closed_before_replaying_thirty_as_twenty_nine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = [
                {
                    "company_name": f"Company {index}",
                    "company_website_url": (
                        "" if index == 29 else f"https://company-{index}.example"
                    ),
                    "pipeline_status": "success",
                }
                for index in range(30)
            ]
            (root / "results.json").write_text(
                json.dumps(records),
                encoding="utf-8",
            )
            args = self._args(
                root,
                pipeline_status=None,
                stage=None,
                stage_status=None,
                reason_code=None,
                limit=30,
            )

            manifest = replay_failure_bundle(args)
            written = json.loads(
                (root / "bundle" / "bundle-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            cli_args = [
                "--results", str(root / "results.json"),
                "--snapshot-dir", str(root / "snapshots"),
                "--output-dir", str(root / "cli-bundle"),
                "--limit", "30",
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "record integrity failed"):
                    main(cli_args)

        self.assertEqual(manifest, written)
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["reason"], "record_integrity_failed")
        self.assertEqual(manifest["outcome_gate"]["status"], "failed")
        integrity = manifest["record_integrity"]
        self.assertEqual(integrity["status"], "failed")
        self.assertEqual(integrity["counts"]["source_result_count"], 30)
        self.assertEqual(integrity["counts"]["selected_count"], 29)
        self.assertEqual(integrity["counts"]["exported_count"], 29)
        self.assertEqual(integrity["counts"]["comparison_count"], 0)
        self.assertIn(
            "replayability_records_dropped",
            {reason["code"] for reason in integrity["reasons"]},
        )
        self.assertFalse((root / "bundle" / "replay-input.json").exists())

    def test_allow_empty_writes_skipped_manifest_without_requiring_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            args = self._args(root, reason_code=["NETWORK_TIMEOUT"])

            manifest = replay_failure_bundle(args, allow_empty=True)
            written = json.loads(
                (root / "bundle" / "bundle-manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(manifest, written)
        self.assertEqual(manifest["status"], "skipped")
        self.assertEqual(manifest["reason"], "no_replayable_failure_records")
        self.assertEqual(manifest["summary"], {"total": 0})
        self.assertEqual(manifest["outcome_gate"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
