from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import asdict, fields, replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_source_agent.checkpoint import (
    ADAPTER_VERSION,
    CHECKPOINT_SCHEMA_VERSION,
    execution_fingerprint,
)
from job_source_agent.composition import (
    AgentConfig,
    FetcherConfig,
    LINKEDIN_EVIDENCE_CACHE_FILENAME,
    build_application,
    build_application_from_fetcher,
)
from job_source_agent.contracts import StageExecution
from job_source_agent.identity_continuity import (
    HiringIdentityEvidence,
    OpeningIdentity,
    ProviderIdentity,
)
from job_source_agent.identity_evidence import FilesystemLinkedInWebsiteEvidenceStore
from job_source_agent.company_discovery_evidence_store import (
    FilesystemCompanyDiscoveryEvidenceStore,
)
from job_source_agent.company_discovery_evidence import (
    COMPANY_DISCOVERY_EVIDENCE_SCHEMA_VERSION,
    VerifiedProviderBoardEvidence,
)
from job_source_agent.job_board import DiscoveredJobBoard, JobBoard, JobBoardPortfolio
from job_source_agent.evaluation import result_provider, summarize_results
from job_source_agent.linkedin import load_company_inputs
from job_source_agent.models import (
    PIPELINE_STAGES,
    RESULT_SCHEMA_VERSION,
    StageResult,
    dataclass_to_dict,
)
from job_source_agent.outcome_tape import OutcomeTape
from job_source_agent.providers.base import (
    PageAwareProviderAdapter,
    PageProbeProviderAdapter,
)
from job_source_agent.providers.registry import DEFAULT_PROVIDER_REGISTRY
from job_source_agent.replay_record_plan import (
    ReplayRecordPlan,
    build_replay_record_plans,
)
from job_source_agent.scoped_replay import ScopedReplayController
from job_source_agent.snapshot import SENSITIVE_BODY_FIELDS
from job_source_agent.snapshot_replay import (
    SnapshotReplayError,
    load_scoped_outcome_tapes,
    replay_snapshots,
)
from job_source_agent.stage_checkpoint import FilesystemCheckpointStore
from job_source_agent.run_configuration import DeterministicRunConfig
from job_source_agent.web import FetchError, Page, normalize_url
from job_source_agent.result_identity import canonicalize_identity_url, tenant_locator
from job_source_agent.request_identity import is_sensitive_key
from scripts.export_replay_input import (
    _SCOPED_REPLAY_SOURCE_KINDS,
    _matches_filters,
    export_replay_records,
)


BUNDLE_SCHEMA_VERSION = 5
SCOPED_BUNDLE_SCHEMA_VERSION = 7
SCOPED_REPLAY_SOURCE_KINDS = _SCOPED_REPLAY_SOURCE_KINDS
SCOPED_REPLAY_PRODUCER_DEPENDENCIES = {
    "career_discovery": "website_resolution",
    "job_board_discovery": "career_discovery",
}


class _ScopedStageSeedAmbiguity(ValueError):
    pass


class FailureReplayError(ValueError):
    """Raised when a failure replay bundle cannot be built safely."""


class _RedactionHydratingScopedFetcher:
    timeout = None

    def __init__(self, controller: ScopedReplayController) -> None:
        self._controller = controller

    @property
    def supports_forced_render(self) -> bool:
        return self._controller.supports_forced_render

    def fetch(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        *,
        interaction=None,
    ) -> Page:
        page = self._controller.fetch(
            url,
            data=data,
            headers=headers,
            interaction=interaction,
        )
        hydrated = _hydrate_redacted_json_credentials(page.html)
        return page if hydrated == page.html else replace(page, html=hydrated)

    def remaining_fetch_seconds(self) -> float | None:
        return self._controller.remaining_fetch_seconds()


def _hydrate_redacted_json_credentials(body: str) -> str:
    """Restore credential shape without restoring secrets in offline JSON responses."""

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return body
    sensitive_fields = {field.casefold() for field in SENSITIVE_BODY_FIELDS}
    replacement = "offline-replay-redacted-credential"

    def hydrate(value, *, key: str | None = None):
        if (
            isinstance(value, str)
            and value == "[REDACTED]"
            and isinstance(key, str)
            and key.casefold() in sensitive_fields
        ):
            return replacement
        if isinstance(value, dict):
            return {
                item_key: hydrate(item_value, key=item_key)
                for item_key, item_value in value.items()
            }
        if isinstance(value, list):
            return [hydrate(item) for item in value]
        return value

    hydrated = hydrate(payload)
    return body if hydrated == payload else json.dumps(hydrated, sort_keys=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select failed results, validate snapshots, and replay the pipeline offline."
    )
    parser.add_argument("--results", required=True, help="Prior results.json or trace.json.")
    parser.add_argument("--snapshot-dir", required=True, help="Snapshot directory with snapshots.jsonl.")
    parser.add_argument("--output-dir", required=True, help="Directory for the self-contained replay bundle.")
    parser.add_argument("--pipeline-status", action="append")
    parser.add_argument("--stage", choices=PIPELINE_STAGES)
    parser.add_argument("--stage-status", action="append")
    parser.add_argument("--reason-code", action="append")
    parser.add_argument("--provider", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--include-missing-website", action="store_true")
    parser.add_argument(
        "--company-discovery-evidence-store",
        help=(
            "Optional public company-discovery evidence store to freeze for the "
            "selected replay records."
        ),
    )
    parser.add_argument(
        "--legacy-run-config",
        choices=("composition-defaults",),
        help="Explicitly replay legacy records that predate deterministic run metadata.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        manifest = replay_failure_bundle(args)
    except (FailureReplayError, SnapshotReplayError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"failure replay failed: {exc}") from exc
    print(json.dumps(manifest["summary"], sort_keys=True), flush=True)
    print(f"bundle: {Path(args.output_dir).resolve()}", flush=True)
    outcome_gate = manifest.get("outcome_gate")
    if isinstance(outcome_gate, dict) and outcome_gate.get("status") in {"failed", "incomplete"}:
        integrity = manifest.get("record_integrity", {})
        if integrity.get("status") == "failed":
            reason_codes = ", ".join(
                reason["code"] for reason in integrity.get("reasons", [])
            )
            raise SystemExit(
                "failure replay gate failed: record integrity failed"
                + (f" ({reason_codes})" if reason_codes else "")
            )
        counts = outcome_gate.get("classification_counts", {})
        mismatch_count = counts.get("mismatch", 0)
        fixture_gap_count = counts.get("fixture_gap", 0)
        raise SystemExit(
            "failure replay gate failed: "
            f"{mismatch_count} outcome mismatch(es), {fixture_gap_count} fixture gap(s)"
        )


def replay_failure_bundle(args: argparse.Namespace, *, allow_empty: bool = False) -> dict:
    results_path = Path(args.results).resolve()
    output_root = Path(args.output_dir).resolve()
    records = json.loads(results_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise FailureReplayError("Results input must be a JSON array")

    export_args = SimpleNamespace(
        input=results_path.name,
        pipeline_status=args.pipeline_status,
        stage=args.stage,
        stage_status=args.stage_status,
        reason_code=args.reason_code,
        provider=args.provider,
        limit=args.limit,
        include_missing_website=args.include_missing_website,
    )
    replay_records, source_records, selection_counts = (
        _export_replay_records_with_sources(records, export_args)
    )
    preflight_integrity = _build_record_integrity(
        args,
        selection_counts,
        result_count=0,
        trace_count=0,
        comparison_count=0,
    )
    if _selection_integrity_failed(preflight_integrity):
        manifest = _empty_bundle_manifest(
            args,
            status="failed",
            reason="record_integrity_failed",
            record_integrity=preflight_integrity,
        )
        _write_json_atomic(output_root / "bundle-manifest.json", manifest)
        return manifest
    if not replay_records:
        record_integrity = preflight_integrity
        if record_integrity["status"] == "failed":
            manifest = _empty_bundle_manifest(
                args,
                status="failed",
                reason="record_integrity_failed",
                record_integrity=record_integrity,
            )
            _write_json_atomic(output_root / "bundle-manifest.json", manifest)
            return manifest
        if allow_empty:
            manifest = _empty_bundle_manifest(
                args,
                record_integrity=record_integrity,
            )
            _write_json_atomic(output_root / "bundle-manifest.json", manifest)
            return manifest
        raise FailureReplayError("No replayable records matched the requested filters")

    run_configuration, run_configuration_provenance = _resolve_run_configuration(
        source_records,
        getattr(args, "legacy_run_config", None),
    )
    try:
        record_plans = build_replay_record_plans(source_records, replay_records)
    except (TypeError, ValueError) as error:
        raise FailureReplayError(f"Invalid replay evidence plan: {error}") from error
    boundary_errors = _scoped_execution_boundary_errors(
        source_records,
        replay_records,
        record_plans,
    )
    if boundary_errors:
        record_integrity = _record_integrity_with_boundary_errors(
            preflight_integrity,
            boundary_errors,
        )
        manifest = _empty_bundle_manifest(
            args,
            status="failed",
            reason="replay_plan_integrity_failed",
            record_integrity=record_integrity,
        )
        _write_json_atomic(output_root / "bundle-manifest.json", manifest)
        return manifest
    evidence_mode = record_plans[0].evidence_mode
    for replay_record, plan in zip(replay_records, record_plans, strict=True):
        replay_record.setdefault("source_trace", {}).setdefault("replay", {})[
            "record_id"
        ] = plan.record_id

    _remove_derived_hiring_entity_inputs(replay_records)

    _reset_checkpoint_output(output_root / "checkpoints")
    input_path = output_root / "replay-input.json"
    _write_json_atomic(input_path, replay_records)
    companies = load_company_inputs(input_path)
    company_discovery_evidence_path, company_discovery_evidence = (
        _freeze_company_discovery_evidence(
            args,
            output_root,
            companies,
            source_records,
        )
    )
    if evidence_mode == "scoped_outcome_tape":
        scopes_by_id = {
            lineage.snapshot_scope.scope_id: lineage.snapshot_scope
            for plan in record_plans
            for lineage in plan.stage_evidence_lineage
            if lineage.snapshot_scope is not None
        }
        tapes = load_scoped_outcome_tapes(args.snapshot_dir, scopes_by_id.values())
        scoped_manifest = _write_scoped_tapes(output_root / "offline", tapes)
        discoveries = _run_scoped_replay_records(
            companies,
            replay_records,
            source_records,
            record_plans,
            tapes,
            output_root / "checkpoints",
            run_configuration,
            company_discovery_evidence_path,
        )
        snapshot_summary = scoped_manifest["summary"]
        bundle_schema_version = SCOPED_BUNDLE_SCHEMA_VERSION
        replay_paths = {
            "tapes": "offline/tapes",
            "snapshot_manifest": "offline/scoped-replay-manifest.json",
        }
    else:
        fixture_result = replay_snapshots(args.snapshot_dir, output_root / "offline")
        discoveries = _run_legacy_replay_records(
            companies,
            replay_records,
            source_records,
            record_plans,
            output_root / "checkpoints",
            output_root / "offline" / "sites",
            run_configuration,
            company_discovery_evidence_path,
        )
        snapshot_summary = fixture_result.summary
        bundle_schema_version = BUNDLE_SCHEMA_VERSION
        replay_paths = {
            "fixtures": "offline/sites",
            "snapshot_manifest": "offline/replay-manifest.json",
            "fetch_failures": "offline/fetch-failures.json",
        }
    result_records = [result.result_record() for result in discoveries]
    trace_records = [dataclass_to_dict(result.trace_record()) for result in discoveries]
    summary = summarize_results(trace_records)
    summary["run_configuration"] = run_configuration.to_payload()
    summary["run_configuration_digest"] = run_configuration.digest
    outcome_gate = _build_outcome_gate(
        replay_records,
        result_records,
        trace_records=trace_records,
        source_records=source_records,
    )
    record_integrity = _build_record_integrity(
        args,
        selection_counts,
        result_count=len(result_records),
        trace_count=len(trace_records),
        comparison_count=len(outcome_gate["records"]),
    )
    if record_integrity["status"] == "failed":
        outcome_gate["status"] = "failed"

    _write_json_atomic(output_root / "replay-results.json", result_records)
    _write_json_atomic(output_root / "replay-trace.json", trace_records)
    _write_json_atomic(output_root / "replay-summary.json", summary)
    manifest = {
        "status": "failed" if record_integrity["status"] == "failed" else "success",
        "bundle_schema_version": bundle_schema_version,
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "run_configuration": run_configuration.to_payload(),
        "run_configuration_digest": run_configuration.digest,
        "run_configuration_provenance": run_configuration_provenance,
        "evidence_mode": evidence_mode,
        "record_plans": [
            {
                "source_ordinal": plan.source_ordinal,
                "record_id": plan.record_id,
                "evidence_mode": plan.evidence_mode,
            }
            for plan in record_plans
        ],
        "paths": {
            "input": "replay-input.json",
            "results": "replay-results.json",
            "trace": "replay-trace.json",
            "summary": "replay-summary.json",
            "checkpoints": "checkpoints",
            **replay_paths,
            **(
                {"company_discovery_evidence": str(company_discovery_evidence_path.relative_to(output_root))}
                if company_discovery_evidence_path is not None
                else {}
            ),
        },
        "filters": {
            "pipeline_status": args.pipeline_status or [],
            "stage": args.stage,
            "stage_status": args.stage_status or [],
            "reason_code": args.reason_code or [],
            "provider": args.provider or [],
            "limit": args.limit,
        },
        "snapshot_summary": snapshot_summary,
        **(
            {"company_discovery_evidence": company_discovery_evidence}
            if getattr(args, "company_discovery_evidence_store", None)
            else {}
        ),
        "summary": summary,
        "record_integrity": record_integrity,
        "outcome_gate": outcome_gate,
    }
    _write_json_atomic(output_root / "bundle-manifest.json", manifest)
    return manifest


def _remove_derived_hiring_entity_inputs(replay_records: list[dict]) -> None:
    """Keep result-only identity outputs out of reconstructed replay input."""

    for replay_record in replay_records:
        # Authoritative upstream checkpoints restore this when the recorded
        # prefix includes it; it must not instead become an execution input.
        replay_record.pop("hiring_entity_name", None)


def _empty_bundle_manifest(
    args: argparse.Namespace,
    *,
    status: str = "skipped",
    reason: str = "no_replayable_failure_records",
    record_integrity: dict | None = None,
) -> dict:
    return {
        "status": status,
        "reason": reason,
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "paths": {},
        "filters": {
            "pipeline_status": args.pipeline_status or [],
            "stage": args.stage,
            "stage_status": args.stage_status or [],
            "reason_code": args.reason_code or [],
            "provider": args.provider or [],
            "limit": args.limit,
        },
        "snapshot_summary": None,
        **(
            {"company_discovery_evidence": _empty_company_discovery_evidence_provenance(args)}
            if getattr(args, "company_discovery_evidence_store", None)
            else {}
        ),
        "summary": {"total": 0},
        "record_integrity": record_integrity,
        "outcome_gate": {
            "status": "failed" if status == "failed" else "skipped",
            "classification_counts": {
                "reproduced": 0,
                "expected_transition": 0,
                "budget_recovery": 0,
                "fixture_gap": 0,
                "mismatch": 0,
            },
            "records": [],
        },
    }


def _reset_checkpoint_output(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise FailureReplayError(f"Unsafe replay checkpoint output: {path}")
    if path.exists():
        shutil.rmtree(path)


def _freeze_company_discovery_evidence(
    args: argparse.Namespace,
    output_root: Path,
    companies: list,
    source_records: list[dict],
) -> tuple[Path | None, dict]:
    """Copy only selected, currently valid public discovery candidates into a bundle."""

    source_value = getattr(args, "company_discovery_evidence_store", None)
    provenance = _empty_company_discovery_evidence_provenance(args)
    bundle_path = output_root / "company-discovery-evidence.json"
    _reset_bundle_file_output(bundle_path)
    identities = {
        (company.company_name, company.linkedin_company_url)
        for company in companies
        if isinstance(company.company_name, str)
        and company.company_name.strip()
        and isinstance(company.linkedin_company_url, str)
        and company.linkedin_company_url.strip()
    }
    provenance["selected_identity_count"] = len(identities)
    if not source_value:
        return None, provenance

    source_path = Path(source_value)
    source_status = _company_discovery_evidence_source_status(source_path)
    provenance["source_status"] = source_status
    if source_status != "available":
        provenance["status"] = "omitted"
        return None, provenance

    source_store = FilesystemCompanyDiscoveryEvidenceStore(source_path)
    frozen_store = FilesystemCompanyDiscoveryEvidenceStore(bundle_path)
    frozen_count = 0
    for company_name, linkedin_company_url in sorted(identities):
        try:
            evidence = source_store.load(company_name, linkedin_company_url)
        except (OSError, TypeError, ValueError):
            provenance["source_status"] = "unreadable"
            provenance["status"] = "omitted"
            _reset_bundle_file_output(bundle_path)
            return None, provenance
        if evidence is None:
            continue
        try:
            frozen_store.save(
                company_name,
                linkedin_company_url,
                website=evidence.website,
                career=evidence.career,
            )
            for provider_board in evidence.provider_boards:
                frozen_store.save(
                    company_name,
                    linkedin_company_url,
                    provider_board=provider_board,
                )
        except (OSError, TypeError, ValueError) as error:
            _reset_bundle_file_output(bundle_path)
            raise FailureReplayError(
                f"Could not freeze company discovery evidence: {error}"
            ) from error
        frozen_count += 1

    restored_provider_inputs = _restore_stored_provider_inputs(
        frozen_store,
        companies,
        source_records,
    )
    provenance["restored_stored_provider_input_count"] = restored_provider_inputs

    provenance["frozen_record_count"] = frozen_count
    if not frozen_count:
        provenance["status"] = "omitted"
        provenance["source_status"] = "available_no_selected_evidence"
        return None, provenance
    provenance["status"] = "frozen"
    provenance["bundle_path"] = str(bundle_path.relative_to(output_root))
    return bundle_path, provenance


def _restore_stored_provider_inputs(
    store: FilesystemCompanyDiscoveryEvidenceStore,
    companies: list,
    source_records: list[dict],
) -> int:
    """Restore durable provider evidence that the captured S5 explicitly read.

    The live batch freezes its evidence store before workers mutate it. A later
    phase can nevertheless read evidence committed by an earlier phase or a
    recovered attempt. Scoped replay must reconstruct that producer state, but
    only when the source trace proves the candidate was stored and S7 verifies
    the same first-party relationship. The provider inventory is still replayed
    from its outcome tape; this input never authorizes an opening by itself.
    """

    restored = 0
    for company, source_record in zip(companies, source_records, strict=True):
        linkedin_url = getattr(company, "linkedin_company_url", None)
        if not isinstance(linkedin_url, str) or not linkedin_url:
            continue
        stage_trace = _source_stage_trace(source_record, "job_board_discovery")
        selected = stage_trace.get("selected") if isinstance(stage_trace, dict) else None
        if (
            not isinstance(selected, dict)
            or selected.get("source_kind") != "stored_verified_provider_board"
        ):
            continue
        assertion = source_record.get("identity_assertion")
        provider = assertion.get("provider") if isinstance(assertion, dict) else None
        hiring = assertion.get("hiring") if isinstance(assertion, dict) else None
        if (
            not isinstance(assertion, dict)
            or assertion.get("verdict") != "verified"
            or not isinstance(provider, dict)
            or provider.get("relationship_verified") is not True
            or not isinstance(hiring, dict)
            or hiring.get("verified") is not True
        ):
            continue
        provider_name = provider.get("provider")
        tenant = provider.get("tenant")
        board_url = provider.get("canonical_board_url")
        evidence_url = provider.get("evidence_url")
        verification_method = provider.get("verification_method")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                provider_name,
                tenant,
                board_url,
                evidence_url,
                verification_method,
            )
        ):
            continue
        if not _same_identity_url(selected.get("url"), board_url):
            continue
        record = store.load(company.company_name, linkedin_url)
        if record is None or record.career is None:
            continue
        if not (
            _same_identity_url(evidence_url, record.career.url)
            or _same_identity_url(evidence_url, record.career.evidence_url)
        ):
            continue
        adapter = DEFAULT_PROVIDER_REGISTRY.adapter_for(board_url)
        board = adapter.identify_board(board_url) if adapter is not None else None
        if adapter is None or board is None:
            continue
        canonicalize = getattr(adapter, "canonicalize_board", None)
        if callable(canonicalize):
            board = canonicalize(board)
        board_tenant = board.identifier or tenant_locator(board.url)
        if (
            board.provider != provider_name
            or board_tenant != tenant
            or not _same_identity_url(board.url, board_url)
        ):
            continue
        try:
            store.save(
                company.company_name,
                linkedin_url,
                provider_board=VerifiedProviderBoardEvidence(
                    provider=provider_name,
                    tenant=tenant,
                    canonical_board_url=board.url,
                    relationship_evidence_url=evidence_url,
                    verification_method=verification_method,
                    source="first_party_handoff",
                    observed_at=record.career.observed_at,
                ),
            )
        except (OSError, TypeError, ValueError):
            continue
        restored += 1
    return restored


def _source_stage_trace(source_record: dict, stage: str) -> dict | None:
    trace = source_record.get("trace")
    stages = trace.get("stages") if isinstance(trace, dict) else None
    value = stages.get(stage) if isinstance(stages, dict) else None
    return value if isinstance(value, dict) else None


def _same_identity_url(left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    normalized_left = canonicalize_identity_url(left)
    normalized_right = canonicalize_identity_url(right)
    return bool(normalized_left and normalized_left == normalized_right)


def _empty_company_discovery_evidence_provenance(args: argparse.Namespace) -> dict:
    source_value = getattr(args, "company_discovery_evidence_store", None)
    return {
        "status": "not_configured" if not source_value else "pending",
        "source_configured": bool(source_value),
        "source_path_sha256": (
            hashlib.sha256(
                str(Path(source_value).expanduser().resolve()).encode("utf-8")
            ).hexdigest()
            if source_value
            else None
        ),
        "source_status": "not_configured" if not source_value else "pending",
        "selected_identity_count": 0,
        "frozen_record_count": 0,
        "restored_stored_provider_input_count": 0,
    }


def _company_discovery_evidence_source_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    if not path.is_file() or path.is_symlink():
        return "unreadable"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "corrupt"
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != COMPANY_DISCOVERY_EVIDENCE_SCHEMA_VERSION
        or not isinstance(payload.get("records"), dict)
    ):
        return "incompatible"
    return "available"


def _reset_bundle_file_output(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise FailureReplayError(f"Unsafe replay evidence output: {path}")
    if path.exists():
        path.unlink()


def _write_scoped_tapes(
    output_root: Path,
    tapes: dict[str, OutcomeTape],
) -> dict:
    _reset_checkpoint_output(output_root)
    tape_root = output_root / "tapes"
    tape_root.mkdir(parents=True, exist_ok=True)
    entries = []
    for scope_id, tape in sorted(tapes.items()):
        path = tape_root / f"{scope_id}.json"
        _write_json_atomic(
            path,
            {
                "scope": asdict(tape.scope),
                "tape": tape.as_payload(),
            },
        )
        entries.append(
            {
                "scope_id": scope_id,
                "path": str(path.relative_to(output_root)),
                "request_count": tape.scope.request_count,
                "records_sha256": tape.scope.records_sha256,
            }
        )
    summary = {
        "evidence_mode": "scoped_outcome_tape",
        "scope_count": len(entries),
        "outcome_count": sum(entry["request_count"] for entry in entries),
    }
    manifest = {
        "schema_version": 3,
        "evidence_mode": "scoped_outcome_tape",
        "entries": entries,
        "summary": summary,
    }
    _write_json_atomic(output_root / "scoped-replay-manifest.json", manifest)
    return manifest


def _run_legacy_replay_records(
    companies: list,
    replay_records: list[dict],
    source_records: list[dict],
    record_plans: tuple[ReplayRecordPlan, ...],
    checkpoint_root: Path,
    fixtures_dir: Path,
    run_configuration: DeterministicRunConfig,
    company_discovery_evidence_path: Path | None,
) -> list:
    discoveries = []
    for company, replay_record, source_record, plan in zip(
        companies,
        replay_records,
        source_records,
        record_plans,
        strict=True,
    ):
        record_checkpoint_root = checkpoint_root / "records" / plan.record_id
        resume_stage = _seed_authoritative_handoffs(
            [company],
            [replay_record],
            [source_record],
            record_checkpoint_root,
            run_configuration,
        )[0]
        application = build_application(
            FetcherConfig(fixtures_dir=fixtures_dir, offline=True),
            checkpoint_dir=record_checkpoint_root,
            run_configuration=run_configuration,
            company_discovery_evidence_path=company_discovery_evidence_path,
        )
        discoveries.append(
            application.pipeline.discover(company, start_at=resume_stage)
        )
    return discoveries


def _run_scoped_replay_records(
    companies: list,
    replay_records: list[dict],
    source_records: list[dict],
    record_plans: tuple[ReplayRecordPlan, ...],
    tapes: dict[str, OutcomeTape],
    checkpoint_root: Path,
    run_configuration: DeterministicRunConfig,
    company_discovery_evidence_path: Path | None,
) -> list:
    discoveries = []
    for company, replay_record, source_record, plan in zip(
        companies,
        replay_records,
        source_records,
        record_plans,
        strict=True,
    ):
        company = _scoped_execution_company(company, source_record)
        execution_fingerprint_value = plan.stage_evidence_lineage[0].execution_fingerprint
        record_checkpoint_root = checkpoint_root / "records" / plan.record_id
        resume_stage = _seed_authoritative_handoffs(
            [company],
            [replay_record],
            [source_record],
            record_checkpoint_root,
            run_configuration,
            record_plans=(plan,),
        )[0]
        _seed_scoped_replay_producer_state(
            record_checkpoint_root,
            company,
            source_record,
        )
        start_stage = resume_stage or PIPELINE_STAGES[0]
        start_index = PIPELINE_STAGES.index(start_stage)
        scopes_by_stage = {
            lineage.stage: lineage.snapshot_scope
            for lineage in plan.stage_evidence_lineage
            if lineage.snapshot_scope is not None
            and PIPELINE_STAGES.index(lineage.stage) >= start_index
        }
        captured_stages = [
            lineage.stage
            for lineage in plan.stage_evidence_lineage
            if PIPELINE_STAGES.index(lineage.stage) >= start_index
        ]
        if not captured_stages:
            raise FailureReplayError(
                f"Scoped replay record {plan.record_id} has no captured execution boundary"
            )
        stop_stage = captured_stages[-1]
        stop_index = PIPELINE_STAGES.index(stop_stage)
        expected_stages = set(PIPELINE_STAGES[start_index : stop_index + 1])
        if set(scopes_by_stage) != expected_stages:
            missing = sorted(expected_stages - set(scopes_by_stage), key=PIPELINE_STAGES.index)
            raise FailureReplayError(
                f"Scoped replay record {plan.record_id} is missing stage scopes: {missing}"
            )
        controller = ScopedReplayController(
            {
                stage: tapes[scope.scope_id]
                for stage, scope in scopes_by_stage.items()
            },
            execution_fingerprint=execution_fingerprint_value,
        )
        application = build_application_from_fetcher(
            _RedactionHydratingScopedFetcher(controller),
            checkpoint_dir=record_checkpoint_root,
            run_configuration=run_configuration,
            capture_coordinator=controller,
            company_discovery_evidence_path=company_discovery_evidence_path,
        )
        try:
            same_attempt_continuation = (
                _captured_scoped_resume_stage(source_record) == resume_stage
                and any(
                    stage.get("status") not in {"success", "not_applicable"}
                    for stage in source_record.get("stages", [])[:start_index]
                    if isinstance(stage, dict)
                )
            )
            discovery = application.pipeline.discover(
                company,
                start_at=resume_stage,
                stop_after=stop_stage,
                capture_attempt_id=f"scoped-replay-{plan.record_id[:16]}",
                execution_fingerprint_override=execution_fingerprint_value,
                same_attempt_continuation=same_attempt_continuation,
            )
            controller.assert_all_consumed()
        except (FetchError, KeyError, TypeError, ValueError) as error:
            raise FailureReplayError(
                f"Scoped replay record {plan.record_id} diverged: {error}"
            ) from error
        replay_source_trace = replay_record.get("source_trace")
        replay_metadata = (
            replay_source_trace.get("replay")
            if isinstance(replay_source_trace, dict)
            else None
        )
        if isinstance(replay_metadata, dict):
            discovery.trace.setdefault("source_trace", {})["replay"] = dict(
                replay_metadata
            )
        discoveries.append(discovery)
    return discoveries


def _seed_scoped_replay_producer_state(
    checkpoint_root: Path,
    company,
    source_record: dict,
) -> None:
    """Restore captured non-request inputs that affected a producer stage."""

    trace = source_record.get("trace")
    stage_traces = trace.get("stages") if isinstance(trace, dict) else None
    website_trace = (
        stage_traces.get("website_resolution")
        if isinstance(stage_traces, dict)
        else None
    )
    if not isinstance(website_trace, dict) or (
        website_trace.get("linkedin_official_evidence_source") != "cache"
    ):
        return
    linkedin_company_url = company.linkedin_company_url
    if not isinstance(linkedin_company_url, str) or not linkedin_company_url:
        return

    cached_urls: list[str] = []
    candidates = website_trace.get("candidates", [])
    selected = website_trace.get("selected")
    if isinstance(selected, dict):
        candidates = (
            [*candidates, selected]
            if isinstance(candidates, list)
            else [selected]
        )
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        reasons = candidate.get("reasons")
        url = candidate.get("url")
        if (
            isinstance(reasons, list)
            and "candidate source: linkedin_cached_official_website" in reasons
            and isinstance(url, str)
            and url not in cached_urls
        ):
            cached_urls.append(url)
    if not cached_urls:
        return

    FilesystemLinkedInWebsiteEvidenceStore(
        checkpoint_root / LINKEDIN_EVIDENCE_CACHE_FILENAME
    ).save(
        company.company_name,
        linkedin_company_url,
        tuple(cached_urls),
    )


def _scoped_execution_company(company, source_record: dict):
    trace = source_record.get("trace")
    stage_traces = trace.get("stages") if isinstance(trace, dict) else None
    linkedin_trace = (
        stage_traces.get("linkedin_discovery")
        if isinstance(stage_traces, dict)
        else None
    )
    source = linkedin_trace.get("source") if isinstance(linkedin_trace, dict) else None
    if source not in SCOPED_REPLAY_SOURCE_KINDS:
        return company

    website_trace = stage_traces.get("website_resolution")
    preferred_website = (
        website_trace.get("preferred_url")
        if isinstance(website_trace, dict)
        else None
    )
    identity_trace = stage_traces.get("hiring_identity_resolution")
    selected_identity = (
        identity_trace.get("selected") if isinstance(identity_trace, dict) else None
    )
    identity_career_root = (
        selected_identity.get("career_root_url")
        if isinstance(selected_identity, dict)
        else None
    )
    career_trace = stage_traces.get("career_discovery")
    trusted_direct_root = (
        isinstance(career_trace, dict)
        and career_trace.get("preferred_root_validation") == "trusted_provenance"
        and not isinstance(identity_career_root, str)
    )
    source_trace = dict(company.source_trace)
    source_trace.pop("replay", None)
    return replace(
        company,
        company_website_url=(
            preferred_website if isinstance(preferred_website, str) else ""
        ),
        career_root_url=(company.career_root_url if trusted_direct_root else None),
        source=source,
        source_trace=source_trace,
    )


def _export_replay_records_with_sources(
    records: list[dict],
    export_args: SimpleNamespace,
) -> tuple[list[dict], list[dict], dict[str, int]]:
    replay_records: list[dict] = []
    source_records: list[dict] = []
    per_record_args = SimpleNamespace(**vars(export_args))
    per_record_args.limit = None
    selected_records = [
        record for record in records if _matches_filters(record, per_record_args)
    ]
    export_attempted_count = 0
    replayability_dropped_count = 0
    for record in selected_records:
        if (
            export_args.limit
            and replay_records
            and len(replay_records) >= export_args.limit
        ):
            break
        export_attempted_count += 1
        exported = export_replay_records([record], per_record_args)
        if not exported:
            replayability_dropped_count += 1
            continue
        replay_records.append(exported[0])
        source_records.append(record)
    counts = {
        "source_result_count": len(records),
        "filter_matched_count": len(selected_records),
        "selected_count": len(source_records),
        "export_attempted_count": export_attempted_count,
        "exported_count": len(replay_records),
        "replayability_dropped_count": replayability_dropped_count,
        "limit_omitted_count": len(selected_records) - export_attempted_count,
    }
    return replay_records, source_records, counts


def _build_record_integrity(
    args: argparse.Namespace,
    selection_counts: dict[str, int],
    *,
    result_count: int,
    trace_count: int,
    comparison_count: int,
) -> dict:
    source_count = selection_counts["source_result_count"]
    explicit_filters = any(
        (
            args.pipeline_status,
            args.stage,
            args.stage_status,
            args.reason_code,
            args.provider,
        )
    )
    limit_covers_source = args.limit is None or args.limit >= source_count
    full_coverage_required = not explicit_filters and limit_covers_source
    counts = {
        **selection_counts,
        "result_count": result_count,
        "trace_count": trace_count,
        "comparison_count": comparison_count,
    }
    reasons: list[dict[str, object]] = []
    if full_coverage_required:
        checks = (
            (
                "filter_match_count_mismatch",
                source_count,
                counts["filter_matched_count"],
            ),
            ("selection_count_mismatch", source_count, counts["selected_count"]),
            ("export_count_mismatch", source_count, counts["exported_count"]),
            ("result_count_mismatch", source_count, result_count),
            ("trace_count_mismatch", source_count, trace_count),
            ("comparison_count_mismatch", source_count, comparison_count),
        )
        reasons.extend(
            {"code": code, "expected": expected, "actual": actual}
            for code, expected, actual in checks
            if expected != actual
        )
        if counts["replayability_dropped_count"]:
            reasons.append(
                {
                    "code": "replayability_records_dropped",
                    "count": counts["replayability_dropped_count"],
                }
            )
    elif explicit_filters:
        reasons.append({"code": "explicit_failure_filters"})
    else:
        reasons.append(
            {
                "code": "limit_below_source_count",
                "limit": args.limit,
                "source_result_count": source_count,
            }
        )
    return {
        "status": "failed" if full_coverage_required and reasons else "passed",
        "full_coverage_required": full_coverage_required,
        "counts": counts,
        "reasons": reasons,
    }


def _selection_integrity_failed(record_integrity: dict) -> bool:
    selection_reason_codes = {
        "filter_match_count_mismatch",
        "selection_count_mismatch",
        "export_count_mismatch",
        "replayability_records_dropped",
    }
    return record_integrity.get("status") == "failed" and any(
        reason.get("code") in selection_reason_codes
        for reason in record_integrity.get("reasons", [])
    )


def _scoped_execution_boundary_errors(
    source_records: list[dict],
    replay_records: list[dict],
    record_plans: tuple[ReplayRecordPlan, ...],
) -> list[dict[str, object]]:
    errors: list[dict[str, object]] = []
    for source_record, replay_record, plan in zip(
        source_records,
        replay_records,
        record_plans,
        strict=True,
    ):
        if plan.evidence_mode != "scoped_outcome_tape":
            continue
        requested_start = _effective_replay_resume_stage(
            source_record,
            replay_record,
            plan,
        )
        try:
            upstream_executions = _authoritative_upstream_executions(
                source_record,
                requested_start,
                scoped_stage_evidence=True,
            )
        except _ScopedStageSeedAmbiguity as error:
            errors.append(
                {
                    "reason_code": "scoped_stage_seed_ambiguous",
                    "record_id": plan.record_id,
                    "company_name": source_record.get("company_name") or "",
                    "detail": str(error),
                }
            )
            continue
        resumable = requested_start is not None and upstream_executions is not None
        start_stage = requested_start if resumable else PIPELINE_STAGES[0]
        start_index = PIPELINE_STAGES.index(start_stage)
        captured_stages = [
            lineage.stage
            for lineage in plan.stage_evidence_lineage
            if PIPELINE_STAGES.index(lineage.stage) >= start_index
        ]
        if not captured_stages:
            errors.append(
                {
                    "record_id": plan.record_id,
                    "company_name": source_record.get("company_name") or "",
                    "start_stage": start_stage,
                    "missing_stages": [start_stage],
                }
            )
            continue
        stop_index = PIPELINE_STAGES.index(captured_stages[-1])
        expected_stages = list(PIPELINE_STAGES[start_index : stop_index + 1])
        missing_stages = [
            stage for stage in expected_stages if stage not in captured_stages
        ]
        if missing_stages:
            errors.append(
                {
                    "record_id": plan.record_id,
                    "company_name": source_record.get("company_name") or "",
                    "start_stage": start_stage,
                    "missing_stages": missing_stages,
                }
            )
    return errors


def _record_integrity_with_boundary_errors(
    record_integrity: dict,
    boundary_errors: list[dict[str, object]],
) -> dict:
    updated = {
        **record_integrity,
        "counts": {
            **record_integrity.get("counts", {}),
            "boundary_invalid_count": len(boundary_errors),
        },
        "reasons": list(record_integrity.get("reasons", [])),
        "status": "failed",
    }
    missing_boundary_errors = [
        error
        for error in boundary_errors
        if error.get("reason_code") != "scoped_stage_seed_ambiguous"
    ]
    ambiguous_seed_errors = [
        error
        for error in boundary_errors
        if error.get("reason_code") == "scoped_stage_seed_ambiguous"
    ]
    if missing_boundary_errors:
        updated["reasons"].append(
            {
                "code": "captured_execution_boundary_missing",
                "count": len(missing_boundary_errors),
                "records": missing_boundary_errors[:20],
            }
        )
    if ambiguous_seed_errors:
        updated["reasons"].append(
            {
                "code": "scoped_stage_seed_ambiguous",
                "count": len(ambiguous_seed_errors),
                "records": ambiguous_seed_errors[:20],
            }
        )
    return updated


def _seed_authoritative_handoffs(
    companies: list,
    replay_records: list[dict],
    source_records: list[dict],
    checkpoint_root: Path,
    run_configuration: DeterministicRunConfig,
    record_plans: tuple[ReplayRecordPlan, ...] | None = None,
) -> list[str | None]:
    store = FilesystemCheckpointStore(checkpoint_root)
    resume_stages: list[str | None] = []
    aligned_plans = record_plans or (None,) * len(companies)
    for company, replay_record, source_record, record_plan in zip(
        companies,
        replay_records,
        source_records,
        aligned_plans,
        strict=True,
    ):
        resume_stage = _effective_replay_resume_stage(
            source_record,
            replay_record,
            record_plan,
        )
        executions = _authoritative_upstream_executions(
            source_record,
            resume_stage,
            scoped_stage_evidence=(
                record_plan is not None
                and record_plan.evidence_mode == "scoped_outcome_tape"
            ),
        )
        if executions is None:
            resume_stages.append(None)
            continue
        fingerprint = (
            record_plan.stage_evidence_lineage[0].execution_fingerprint
            if record_plan is not None and record_plan.stage_evidence_lineage
            else execution_fingerprint(asdict(company), run_configuration.digest)
        )
        lineage_by_stage = (
            {
                lineage.stage: lineage
                for lineage in record_plan.stage_evidence_lineage
            }
            if record_plan is not None
            else {}
        )
        for execution in executions:
            execution.evidence_lineage = lineage_by_stage.get(execution.result.stage)
            store.save(fingerprint, execution)
        resume_stages.append(resume_stage)
    return resume_stages


def _resolve_run_configuration(
    source_records: list[dict],
    legacy_mode: str | None,
) -> tuple[DeterministicRunConfig, str]:
    payloads = [record.get("run_configuration") for record in source_records]
    present = [payload for payload in payloads if payload is not None]
    if not present:
        if legacy_mode != "composition-defaults":
            raise FailureReplayError(
                "Selected records predate run configuration metadata; pass "
                "--legacy-run-config composition-defaults to replay them explicitly"
            )
        return DeterministicRunConfig.from_agent_config(AgentConfig()), "legacy_defaulted"
    if len(present) != len(payloads):
        raise FailureReplayError("Selected records mix missing and versioned run configurations")
    try:
        configurations = [DeterministicRunConfig.from_payload(payload) for payload in present]
    except ValueError as error:
        raise FailureReplayError(f"Invalid source run configuration: {error}") from error
    first = configurations[0]
    if any(configuration.digest != first.digest for configuration in configurations[1:]):
        raise FailureReplayError("Selected records contain incompatible run configurations")
    for source_record in source_records:
        recorded_digest = source_record.get("run_configuration_digest")
        if recorded_digest is not None and recorded_digest != first.digest:
            raise FailureReplayError("Source run configuration digest does not match its payload")
    return first, "source_record"


def _first_non_success_stage_name(replay_record: dict) -> str | None:
    source_trace = replay_record.get("source_trace")
    replay = source_trace.get("replay") if isinstance(source_trace, dict) else None
    stage = replay.get("first_non_success_stage") if isinstance(replay, dict) else None
    stage_name = stage.get("stage") if isinstance(stage, dict) else None
    return stage_name if stage_name in PIPELINE_STAGES else None


def _replay_resume_stage(source_record: dict, failure_stage: str | None) -> str | None:
    if failure_stage == "result_validation":
        stages = source_record.get("stages")
        opening_stage = next(
            (
                stage
                for stage in stages
                if isinstance(stage, dict)
                and stage.get("stage") == "opening_match"
            ),
            None,
        ) if isinstance(stages, list) else None
        if (
            isinstance(opening_stage, dict)
            and opening_stage.get("status") == "success"
            and not source_record.get("open_position_url")
        ):
            return "opening_match"
    if failure_stage != "opening_match":
        return failure_stage
    trace = source_record.get("trace")
    stage_traces = trace.get("stages") if isinstance(trace, dict) else None
    job_board_trace = (
        stage_traces.get("job_board_discovery")
        if isinstance(stage_traces, dict)
        else None
    )
    provider_detection = (
        job_board_trace.get("provider_detection")
        if isinstance(job_board_trace, dict)
        else None
    )
    method = (
        provider_detection.get("method")
        if isinstance(provider_detection, dict)
        else None
    )
    if method in {"page_evidence", "page_probe"}:
        return "job_board_discovery"
    if method is None and _results_require_page_derived_board(source_record):
        return "job_board_discovery"
    return failure_stage


def _effective_replay_resume_stage(
    source_record: dict,
    replay_record: dict,
    record_plan: ReplayRecordPlan | None,
) -> str | None:
    failure_stage = _first_non_success_stage_name(replay_record)
    resume_stage = _replay_resume_stage(
        source_record,
        failure_stage,
    )
    if record_plan is None or record_plan.evidence_mode != "scoped_outcome_tape":
        return resume_stage

    captured_resume_stage = _captured_scoped_resume_stage(source_record)
    if captured_resume_stage is not None:
        resume_stage = captured_resume_stage
    # Scoped tapes preserve the opening-stage request boundary, so replay the
    # serialized S5 handoff instead of rerunning page-derived board discovery
    # in the same in-memory context as S6.
    elif failure_stage == "opening_match":
        resume_stage = "opening_match"

    while resume_stage in SCOPED_REPLAY_PRODUCER_DEPENDENCIES:
        resume_stage = SCOPED_REPLAY_PRODUCER_DEPENDENCIES[resume_stage]
    return resume_stage


def _captured_scoped_resume_stage(source_record: dict) -> str | None:
    """Recover the actual resumed phase boundary recorded by the live runner."""

    trace = source_record.get("trace")
    events = trace.get("checkpoint_events") if isinstance(trace, dict) else None
    if not isinstance(events, list):
        return None
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        if not isinstance(event, dict) or event.get("action") != "invalidate_from":
            continue
        stage = event.get("stage")
        if stage not in PIPELINE_STAGES:
            continue
        stage_index = PIPELINE_STAGES.index(stage)
        subsequent = [item for item in events[index + 1 :] if isinstance(item, dict)]
        restored = {
            item.get("stage")
            for item in subsequent
            if item.get("action") == "restore"
        }
        saved = {
            item.get("stage")
            for item in subsequent
            if item.get("action") == "save"
        }
        if set(PIPELINE_STAGES[:stage_index]).issubset(restored) and stage in saved:
            return stage
    return None


def _results_require_page_derived_board(source_record: dict) -> bool:
    stages = source_record.get("stages")
    job_list_url = source_record.get("job_list_page_url")
    if not isinstance(stages, list) or not isinstance(job_list_url, str) or not job_list_url:
        return False
    try:
        parsed_url = urlparse(job_list_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or parsed_url.username
            or parsed_url.password
        ):
            return False
    except (TypeError, ValueError):
        return False
    job_board_result = next(
        (
            stage
            for stage in stages
            if isinstance(stage, dict) and stage.get("stage") == "job_board_discovery"
        ),
        None,
    )
    provider = job_board_result.get("provider") if isinstance(job_board_result, dict) else None
    trace = source_record.get("trace")
    stage_traces = trace.get("stages") if isinstance(trace, dict) else None
    job_board_trace = (
        stage_traces.get("job_board_discovery")
        if isinstance(stage_traces, dict)
        else None
    )
    if isinstance(job_board_trace, dict) and (
        job_board_trace.get("pages_visited")
        or job_board_trace.get("selected_page_source")
    ):
        return True
    if not isinstance(provider, str) or not provider:
        return False
    adapter = DEFAULT_PROVIDER_REGISTRY.adapter_named(provider)
    if adapter is None or not isinstance(
        adapter,
        (PageAwareProviderAdapter, PageProbeProviderAdapter),
    ):
        return False
    try:
        return adapter.identify_board(job_list_url) is None
    except (TypeError, ValueError):
        return False


def _authoritative_upstream_executions(
    source_record: dict,
    resume_stage: str | None,
    *,
    scoped_stage_evidence: bool = False,
) -> list[StageExecution] | None:
    if resume_stage is None:
        return None
    resume_index = PIPELINE_STAGES.index(resume_stage)
    stages = source_record.get("stages")
    if not isinstance(stages, list):
        return None
    stage_by_name = {
        stage.get("stage"): stage
        for stage in stages
        if isinstance(stage, dict) and stage.get("stage") in PIPELINE_STAGES
    }
    upstream = PIPELINE_STAGES[:resume_index]
    allowed_statuses = (
        {
            "success",
            "not_applicable",
            "failed",
            "partial",
            "unsupported",
            "not_run",
        }
        if scoped_stage_evidence
        else {"success", "not_applicable"}
    )
    if any(
        stage_name not in stage_by_name
        or stage_by_name[stage_name].get("status") not in allowed_statuses
        for stage_name in upstream
    ):
        return None

    result_fields = {field.name for field in fields(StageResult)}
    executions = [
        StageExecution(
            result=StageResult(
                **{
                    key: value
                    for key, value in stage_by_name[stage_name].items()
                    if key in result_fields
                }
            ),
            updates=_authoritative_stage_updates(
                stage_name,
                source_record,
                scoped_stage_evidence=scoped_stage_evidence,
            ),
            trace={},
        )
        for stage_name in upstream
    ]
    required_update_by_stage = {
        "website_resolution": "company_website_url",
        "career_discovery": "career_page_url",
        "job_board_discovery": "job_list_page_url",
        "opening_match": "open_position_url",
    }
    if any(
        execution.result.status == "success"
        and (required := required_update_by_stage.get(execution.result.stage)) is not None
        and required not in execution.updates
        for execution in executions
    ):
        return None
    return executions


def _authoritative_stage_updates(
    stage: str,
    source_record: dict,
    *,
    scoped_stage_evidence: bool = False,
) -> dict:
    fields_by_stage = {
        "website_resolution": ("company_website_url",),
        "hiring_identity_resolution": (
            "company_website_url",
            "hiring_entity_name",
            "career_root_url",
        ),
        "career_discovery": ("career_page_url",),
        "job_board_discovery": ("job_list_page_url",),
        "opening_match": ("job_list_page_url", "open_position_url"),
    }
    updates = {
        field: source_record[field]
        for field in fields_by_stage.get(stage, ())
        if source_record.get(field) not in (None, "")
    }
    if stage == "job_board_discovery":
        result = next(
            (
                item
                for item in source_record.get("stages", [])
                if isinstance(item, dict) and item.get("stage") == stage
            ),
            {},
        )
        if scoped_stage_evidence:
            updates.pop("job_list_page_url", None)
            stage_url = _scoped_job_board_stage_url(result, source_record)
            if stage_url is not None:
                updates["job_list_page_url"] = stage_url
            portfolio = _scoped_job_board_portfolio(source_record)
            if portfolio is not None:
                updates["discovered_job_board"] = portfolio.primary
                updates["job_board_portfolio"] = portfolio
        if result.get("provider"):
            updates["provider"] = result["provider"]
    updates.update(_legacy_identity_checkpoint_updates(stage, source_record, updates))
    return updates


def _scoped_job_board_stage_url(result: dict, source_record: dict) -> str | None:
    candidates: list[str] = []
    evidence = result.get("evidence") if isinstance(result, dict) else None
    if isinstance(evidence, list):
        candidates.extend(
            item["url"]
            for item in evidence
            if isinstance(item, dict)
            and item.get("field") == "job_list_page_url"
            and isinstance(item.get("url"), str)
            and item["url"]
        )

    trace = source_record.get("trace")
    stage_traces = trace.get("stages") if isinstance(trace, dict) else None
    job_board_trace = (
        stage_traces.get("job_board_discovery")
        if isinstance(stage_traces, dict)
        else None
    )
    traced_url = (
        job_board_trace.get("job_list_page_url")
        if isinstance(job_board_trace, dict)
        else None
    )
    if isinstance(traced_url, str) and traced_url:
        candidates.append(traced_url)

    unique_candidates = tuple(dict.fromkeys(candidates))
    if len(unique_candidates) > 1:
        raise _ScopedStageSeedAmbiguity(
            "Scoped job_board_discovery evidence has conflicting "
            f"job_list_page_url values: {list(unique_candidates)}"
        )
    return unique_candidates[0] if unique_candidates else None


def _verified_identity_job_board(
    source_record: dict,
    *,
    provider: str,
    board_url: str,
) -> JobBoard | None:
    """Restore a typed dynamic board only from a verified S7 identity chain."""

    if provider == "generic":
        return None
    assertion = source_record.get("identity_assertion")
    if not isinstance(assertion, dict) or assertion.get("verdict") != "verified":
        return None
    payload = assertion.get("provider")
    if not isinstance(payload, dict):
        return None
    try:
        identity = ProviderIdentity.from_checkpoint_payload(payload)
    except (TypeError, ValueError):
        return None
    if (
        not identity.relationship_verified
        or identity.provider != provider
        or canonicalize_identity_url(identity.canonical_board_url)
        != canonicalize_identity_url(board_url)
        or not identity.tenant
    ):
        return None
    return JobBoard(
        url=identity.canonical_board_url,
        provider=identity.provider,
        identifier=identity.tenant,
        replay_safe=True,
    )


def _scoped_job_board_portfolio(source_record: dict) -> JobBoardPortfolio | None:
    trace = source_record.get("trace")
    stage_traces = trace.get("stages") if isinstance(trace, dict) else None
    job_board_trace = (
        stage_traces.get("job_board_discovery")
        if isinstance(stage_traces, dict)
        else None
    )
    portfolio_summary = (
        job_board_trace.get("job_board_portfolio")
        if isinstance(job_board_trace, dict)
        else None
    )
    if not isinstance(portfolio_summary, dict):
        return None
    eligible_count = portfolio_summary.get("eligible_count")
    eligible_set_complete = portfolio_summary.get("eligible_set_complete")
    if (
        type(eligible_count) is not int
        or eligible_count < 1
        or type(eligible_set_complete) is not bool
    ):
        raise _ScopedStageSeedAmbiguity(
            "Scoped job-board portfolio summary is incomplete"
        )
    primary_url = portfolio_summary.get("primary_url")
    primary_provider = portfolio_summary.get("primary_provider")
    provider_detection = (
        job_board_trace.get("provider_detection")
        if isinstance(job_board_trace, dict)
        else None
    )
    detected_url = (
        provider_detection.get("url")
        if isinstance(provider_detection, dict)
        else None
    )
    detected_provider = (
        provider_detection.get("provider")
        if isinstance(provider_detection, dict)
        else None
    )
    detection_method = (
        provider_detection.get("method")
        if isinstance(provider_detection, dict)
        else None
    )
    result = next(
        (
            item
            for item in source_record.get("stages", [])
            if isinstance(item, dict) and item.get("stage") == "job_board_discovery"
        ),
        {},
    )
    result_provider = result.get("provider") if isinstance(result, dict) else None
    if (
        not isinstance(primary_url, str)
        or not primary_url
        or not isinstance(primary_provider, str)
        or not primary_provider
        or len(
            {
                provider
                for provider in (
                    primary_provider,
                    detected_provider,
                    result_provider,
                )
                if isinstance(provider, str) and provider
            }
        )
        != 1
    ):
        raise _ScopedStageSeedAmbiguity(
            "Scoped job-board portfolio primary detection metadata is inconsistent"
        )

    if (
        eligible_count == 1
        and eligible_set_complete
        and primary_provider == "generic"
        and detected_url is None
        and detected_provider is None
    ):
        return None
    if detected_url != primary_url or detected_provider != primary_provider:
        raise _ScopedStageSeedAmbiguity(
            "Scoped job-board portfolio primary detection metadata is inconsistent"
        )

    if eligible_count == 1 and eligible_set_complete:
        adapter = DEFAULT_PROVIDER_REGISTRY.adapter_named(primary_provider)
        board = adapter.identify_board(primary_url) if adapter is not None else None
        if board is None or not board.replay_safe:
            board = _verified_identity_job_board(
                source_record,
                provider=primary_provider,
                board_url=primary_url,
            )
        if board is None or not board.replay_safe:
            return None
        try:
            discovered = DiscoveredJobBoard(
                board=board,
                detection_method=detection_method,
                evidence_url=detected_url,
            )
            return JobBoardPortfolio(
                boards=(discovered,),
                eligible_set_complete=True,
            )
        except (TypeError, ValueError) as error:
            raise _ScopedStageSeedAmbiguity(
                "Scoped singleton job-board discovery evidence is not checkpoint-safe"
            ) from error

    opening_trace = (
        stage_traces.get("opening_match")
        if isinstance(stage_traces, dict)
        else None
    )
    board_portfolio = (
        opening_trace.get("board_portfolio")
        if isinstance(opening_trace, dict)
        else None
    )
    attempts = (
        board_portfolio.get("attempts")
        if isinstance(board_portfolio, dict)
        else None
    )
    if not isinstance(attempts, list) or len(attempts) != eligible_count:
        raise _ScopedStageSeedAmbiguity(
            "Scoped job-board portfolio attempts do not cover the captured eligible set"
        )

    discovered: list[DiscoveredJobBoard] = []
    for attempt in attempts:
        board_url = attempt.get("board_url") if isinstance(attempt, dict) else None
        provider = attempt.get("provider") if isinstance(attempt, dict) else None
        attempt_trace = attempt.get("trace") if isinstance(attempt, dict) else None
        provider_api = (
            attempt_trace.get("provider_api")
            if isinstance(attempt_trace, dict)
            else None
        )
        provider_detection = (
            provider_api.get("provider_detection")
            if isinstance(provider_api, dict)
            else None
        )
        detection_method = (
            provider_detection.get("source_method")
            if isinstance(provider_detection, dict)
            else None
        )
        adapter = (
            DEFAULT_PROVIDER_REGISTRY.adapter_named(provider)
            if isinstance(provider, str)
            else None
        )
        board = adapter.identify_board(board_url) if adapter is not None else None
        if board is None or not board.replay_safe or board.provider != provider:
            raise _ScopedStageSeedAmbiguity(
                "Scoped job-board portfolio contains a non-replayable board identity"
            )
        try:
            discovered.append(
                DiscoveredJobBoard(
                    board=board,
                    detection_method=detection_method,
                    evidence_url=board_url,
                )
            )
        except (TypeError, ValueError) as error:
            raise _ScopedStageSeedAmbiguity(
                "Scoped job-board portfolio has invalid discovery evidence"
            ) from error
    if len({(item.board.provider, item.board.url) for item in discovered}) != len(
        discovered
    ):
        raise _ScopedStageSeedAmbiguity(
            "Scoped job-board portfolio contains duplicate board identities"
        )
    if discovered[0].board.url != primary_url:
        raise _ScopedStageSeedAmbiguity(
            "Scoped job-board portfolio primary identity does not match its attempts"
        )
    return JobBoardPortfolio(
        boards=tuple(discovered),
        eligible_set_complete=eligible_set_complete,
    )


def _legacy_identity_checkpoint_updates(
    stage: str,
    source_record: dict,
    updates: dict,
) -> dict:
    """Hydrate typed but unverified identity for pre-2.2 partial replay records."""

    assertion = source_record.get("identity_assertion")
    if isinstance(assertion, dict):
        field_contract = {
            "hiring_identity_resolution": (
                "hiring",
                "hiring_identity_evidence",
                HiringIdentityEvidence,
            ),
            "job_board_discovery": (
                "provider",
                "provider_identity",
                ProviderIdentity,
            ),
            "opening_match": (
                "opening",
                "opening_identity",
                OpeningIdentity,
            ),
        }.get(stage)
        if field_contract is None:
            return {}
        assertion_field, update_field, contract_type = field_contract
        payload = assertion.get(assertion_field)
        if not isinstance(payload, dict):
            return {}
        try:
            return {
                update_field: contract_type.from_checkpoint_payload(payload)
            }
        except (TypeError, ValueError):
            return {}
    source_name = str(source_record.get("company_name") or "").strip()
    hiring_name = str(
        source_record.get("hiring_entity_name") or source_name
    ).strip()
    if stage == "hiring_identity_resolution" and source_name and hiring_name:
        same_entity = _normalized_identity_text(source_name) == _normalized_identity_text(
            hiring_name
        )
        evidence_url = _canonical_public_url(
            source_record.get("career_root_url")
            or source_record.get("company_website_url")
        )
        return {
            "hiring_identity_evidence": HiringIdentityEvidence(
                source_company_name=source_name,
                hiring_entity_name=hiring_name,
                relationship_type="same_entity" if same_entity else "input_asserted",
                verification_method=(
                    "legacy_same_entity_replay"
                    if same_entity
                    else "legacy_input_replay"
                ),
                verified=same_entity,
                evidence_url=evidence_url,
            )
        }
    if stage == "job_board_discovery" and hiring_name:
        board_url = _canonical_public_url(source_record.get("job_list_page_url"))
        if board_url is None:
            return {}
        provider = str(updates.get("provider") or "generic")
        adapter = DEFAULT_PROVIDER_REGISTRY.adapter_named(provider)
        board = adapter.identify_board(board_url) if adapter is not None else None
        canonical_board = (
            canonicalize_identity_url(board.url) if board is not None else board_url
        )
        tenant = (
            board.identifier
            if board is not None and board.identifier
            else tenant_locator(canonical_board)
        )
        return {
            "provider_identity": ProviderIdentity(
                hiring_entity_name=hiring_name,
                provider=provider,
                tenant=tenant,
                canonical_board_url=canonical_board,
                evidence_url=board_url,
                verification_method="legacy_replay_input",
                relationship_verified=False,
            )
        }
    if stage == "opening_match" and hiring_name:
        board_url = _canonical_public_url(source_record.get("job_list_page_url"))
        opening_url = _canonical_public_url(source_record.get("open_position_url"))
        if board_url is None or opening_url is None:
            return {}
        provider = str(updates.get("provider") or result_provider(source_record) or "generic")
        adapter = DEFAULT_PROVIDER_REGISTRY.adapter_named(provider)
        board = adapter.identify_board(board_url) if adapter is not None else None
        canonical_board = (
            canonicalize_identity_url(board.url) if board is not None else board_url
        )
        tenant = (
            board.identifier
            if board is not None and board.identifier
            else tenant_locator(canonical_board)
        )
        return {
            "opening_identity": OpeningIdentity(
                hiring_entity_name=hiring_name,
                provider=provider,
                tenant=tenant,
                canonical_board_url=canonical_board,
                canonical_opening_url=opening_url,
            )
        }
    return {}


def _build_outcome_gate(
    replay_records: list[dict],
    result_records: list[dict],
    *,
    trace_records: list[dict] | None = None,
    source_records: list[dict] | None = None,
) -> dict:
    comparisons = []
    counts = {
        "reproduced": 0,
        "expected_transition": 0,
        "budget_recovery": 0,
        "fixture_gap": 0,
        "mismatch": 0,
    }
    record_count = max(len(replay_records), len(result_records))
    for index in range(record_count):
        replay_input = replay_records[index] if index < len(replay_records) else None
        replay_result = result_records[index] if index < len(result_records) else None
        replay_trace = (
            trace_records[index]
            if trace_records is not None and index < len(trace_records)
            else None
        )
        source_record = (
            source_records[index]
            if source_records is not None and index < len(source_records)
            else None
        )
        expected_record_id = _replay_record_id(replay_input)
        actual_record_id = _replay_record_id(replay_trace)
        compare_identity = bool(
            source_record is not None
            and (
                _optional_string(source_record.get("pipeline_status")) == "success"
                or _optional_string(source_record.get("status")) == "success"
            )
        )
        original = (
            _source_outcome(source_record, include_identity=compare_identity)
            if source_record is not None
            else _original_outcome(replay_input)
        )
        expected_transition = _expected_transition(replay_input)
        replayed_original = _result_outcome(
            replay_result,
            failure_stage=_outcome_stage_name(original),
            include_identity=compare_identity,
        )
        replayed_expected = (
            _result_outcome(
                replay_result,
                failure_stage=_outcome_stage_name(expected_transition),
                include_identity=False,
            )
            if expected_transition is not None
            else replayed_original
        )
        budget_replay_outcome = _result_outcome(
            replay_result,
            failure_stage=None,
            include_identity=True,
        )
        original_identity = _identity_comparison(source_record)
        replay_identity = _identity_comparison(replay_result, replay_trace)
        identity_comparison = (
            "available"
            if original_identity is not None and replay_identity is not None
            else "unavailable"
        )
        identity_matches = (
            identity_comparison == "unavailable"
            or original_identity == replay_identity
        )
        identity_system_gap = _is_identity_system_gap(
            original,
            original_identity,
        )
        expected_transition_valid = _expected_transition_contract_matches(
            expected_transition,
            original,
            replayed_expected,
            original_identity,
            replay_identity,
            identity_comparison,
        )
        source_identity_prefix = _successful_identity_prefix(source_record)
        successful_outcome_reproduced = bool(
            source_record is not None
            and original is not None
            and original.get("pipeline_status") == "success"
            and _outcomes_match(original, replayed_original)
        )
        if expected_record_id is not None and actual_record_id != expected_record_id:
            classification = "mismatch"
            reason = "record_identity_changed"
        elif identity_system_gap and _has_reason_code(replay_result, "OPENING_NOT_FOUND"):
            classification = "mismatch"
            reason = "identity_system_gap_degraded"
        elif successful_outcome_reproduced:
            classification = "reproduced" if identity_matches else "mismatch"
            reason = "outcome_equal" if identity_matches else "identity_outcome_changed"
        elif (
            _contains_reason_code(
                (replay_result, replay_trace),
                "OFFLINE_FIXTURE_MISSING",
            )
            and not _contains_reason_code(
                (source_record,),
                "OFFLINE_FIXTURE_MISSING",
            )
        ):
            classification = "fixture_gap"
            reason = "offline_fixture_missing"
        elif _outcomes_match(original, replayed_original):
            classification = "reproduced" if identity_matches else "mismatch"
            reason = "outcome_equal" if identity_matches else "identity_outcome_changed"
        elif (
            expected_transition is not None
            and _outcomes_match(_transition_outcome(expected_transition), replayed_expected)
            and _identity_prefix_matches(source_record, replay_result)
            and expected_transition_valid
        ):
            classification = "expected_transition"
            reason = "declared_transition_equal"
        elif (
            expected_transition is None
            and _is_budget_recovery(source_record, replay_result)
        ):
            classification = "budget_recovery"
            reason = "company_budget_replay_advanced"
        else:
            classification = "mismatch"
            reason = (
                "record_count_changed"
                if replay_input is None or replay_result is None
                else "declared_transition_not_met"
                if expected_transition is not None
                else "outcome_changed"
            )
        counts[classification] += 1
        comparisons.append(
            {
                "index": index,
                "record_id": expected_record_id,
                "company_name": _record_field(replay_input, replay_result, "company_name"),
                "job_title": _record_field(
                    replay_input,
                    replay_result,
                    "job_title",
                    fallback="linkedin_job_title",
                ),
                "classification": classification,
                "reason": reason,
                "original_outcome": original,
                "expected_transition": expected_transition,
                "replay_outcome": (
                    replayed_expected
                    if expected_transition is not None
                    else budget_replay_outcome
                    if classification == "budget_recovery"
                    else replayed_original
                ),
                "source_identity_prefix": source_identity_prefix,
                "identity_comparison": identity_comparison,
                "original_identity": original_identity,
                "replay_identity": replay_identity,
            }
        )

    if counts["mismatch"]:
        status = "failed"
    elif counts["fixture_gap"]:
        status = "incomplete"
    else:
        status = "passed"
    return {
        "status": status,
        "classification_counts": counts,
        "records": comparisons,
    }


def _replay_record_id(record: dict | None) -> str | None:
    if not isinstance(record, dict):
        return None
    trace = record.get("trace")
    payload = trace if isinstance(trace, dict) else record
    source_trace = payload.get("source_trace")
    replay = source_trace.get("replay") if isinstance(source_trace, dict) else None
    record_id = replay.get("record_id") if isinstance(replay, dict) else None
    if not isinstance(record_id, str):
        return None
    return record_id


def _original_outcome(record: dict | None) -> dict | None:
    if not isinstance(record, dict):
        return None
    source_trace = record.get("source_trace")
    replay_metadata = source_trace.get("replay") if isinstance(source_trace, dict) else None
    if not isinstance(replay_metadata, dict):
        return None
    return {
        "pipeline_status": _optional_string(replay_metadata.get("pipeline_status")),
        "failure_stage": _stage_outcome(
            replay_metadata.get("first_non_success_stage")
        ),
    }


def _expected_transition(record: dict | None) -> dict | None:
    if not isinstance(record, dict):
        return None
    source_trace = record.get("source_trace")
    replay_metadata = source_trace.get("replay") if isinstance(source_trace, dict) else None
    transition = (
        replay_metadata.get("expected_transition")
        if isinstance(replay_metadata, dict)
        else None
    )
    if not isinstance(transition, dict):
        return None
    outcome = {
        "pipeline_status": _optional_string(transition.get("pipeline_status")),
        "failure_stage": _stage_outcome(transition.get("failure_stage")),
    }
    for field in ("old_disposition", "new_disposition", "identity_expectation"):
        if field in transition:
            outcome[field] = _normalize_terminal_value(transition.get(field))
    return outcome


def _outcome_stage_name(outcome: dict | None) -> str | None:
    failure_stage = outcome.get("failure_stage") if isinstance(outcome, dict) else None
    if not isinstance(failure_stage, dict):
        return None
    return _optional_string(failure_stage.get("stage"))


def _transition_outcome(transition: dict) -> dict:
    return {
        "pipeline_status": transition.get("pipeline_status"),
        "failure_stage": transition.get("failure_stage"),
    }


def _outcomes_match(expected: dict | None, actual: dict | None) -> bool:
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return False
    if (
        expected.get("pipeline_status") != actual.get("pipeline_status")
        or expected.get("failure_stage") != actual.get("failure_stage")
    ):
        return False
    return all(
        actual.get(field) == expected[field]
        for field in ("terminal_semantic", "terminal_disposition", "result_identity")
        if field in expected
    )


def _source_outcome(
    record: dict | None,
    *,
    include_identity: bool,
) -> dict | None:
    if not isinstance(record, dict):
        return None
    outcome = {
        "pipeline_status": _optional_string(record.get("pipeline_status") or record.get("status")),
        "failure_stage": _stage_outcome(_first_non_success_result_stage(record)),
    }
    if include_identity:
        outcome["result_identity"] = _result_identity(record)
    outcome.update(_terminal_outcome_fields(record))
    return outcome


def _result_outcome(
    record: dict | None,
    *,
    failure_stage: str | None,
    include_identity: bool = False,
) -> dict | None:
    if not isinstance(record, dict):
        return None
    stages = record.get("stages")
    stage_by_name = {
        str(stage.get("stage")): stage
        for stage in stages if isinstance(stage, dict) and stage.get("stage")
    } if isinstance(stages, list) else {}
    replay_failure = stage_by_name.get(failure_stage) if failure_stage else None
    if not failure_stage:
        replay_failure = next(
            (
                stage_by_name[stage_name]
                for stage_name in PIPELINE_STAGES
                if stage_name in stage_by_name
                and stage_by_name[stage_name].get("status") not in {"success", "not_applicable"}
            ),
            None,
        )
    outcome = {
        "pipeline_status": _optional_string(
            record.get("pipeline_status") or record.get("status")
        ),
        "failure_stage": _stage_outcome(replay_failure),
    }
    if include_identity:
        outcome["result_identity"] = _result_identity(record)
    outcome.update(_terminal_outcome_fields(record))
    return outcome


def _terminal_outcome_fields(record: dict) -> dict:
    fields: dict[str, object] = {}
    trace = record.get("trace")
    trace = trace if isinstance(trace, dict) else {}
    terminal = trace.get("terminal")
    terminal = terminal if isinstance(terminal, dict) else {}
    evaluation = record.get("evaluation")
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    for target, names in (
        ("terminal_semantic", ("terminal_semantic", "semantic")),
        ("terminal_disposition", ("terminal_disposition", "disposition")),
    ):
        value = next(
            (
                candidate
                for candidate in (
                    _first_present_value(record, names),
                    _first_present_value(trace, names),
                    _first_present_value(terminal, names),
                    (
                        evaluation.get("record_disposition")
                        if target == "terminal_disposition"
                        else None
                    ),
                )
                if candidate is not None
            ),
            None,
        )
        if value is not None:
            fields[target] = _normalize_terminal_value(value)
    return fields


def _expected_transition_contract_matches(
    expected: dict | None,
    original: dict | None,
    replayed: dict | None,
    original_identity: dict | None,
    replay_identity: dict | None,
    identity_comparison: str,
) -> bool:
    if expected is None:
        return False
    if identity_comparison == "unavailable":
        return True
    required = {"old_disposition", "new_disposition", "identity_expectation"}
    if not required.issubset(expected):
        return False
    if _terminal_disposition(original) != expected["old_disposition"]:
        return False
    if _terminal_disposition(replayed) != expected["new_disposition"]:
        return False
    expectation = expected["identity_expectation"]
    if expectation in {"same", "reproduce"}:
        return original_identity == replay_identity
    if isinstance(expectation, dict):
        return replay_identity == _normalize_identity_contract(expectation)
    return False


def _terminal_disposition(outcome: dict | None) -> object:
    if not isinstance(outcome, dict):
        return None
    return outcome.get("terminal_disposition", outcome.get("terminal_semantic"))


def _identity_comparison(*records: dict | None) -> dict | None:
    for record in records:
        for candidate in _identity_candidates(record):
            normalized = _normalize_identity_contract(candidate)
            if normalized is not None:
                return normalized
    return None


def _identity_candidates(record: dict | None) -> list[dict]:
    if not isinstance(record, dict):
        return []
    trace = record.get("trace")
    trace = trace if isinstance(trace, dict) else {}
    stages = trace.get("stages")
    validation = stages.get("result_validation") if isinstance(stages, dict) else None
    candidates = [
        record,
        record.get("identity_assertion"),
        record.get("identity"),
        trace,
        trace.get("identity"),
        validation,
    ]
    return [candidate for candidate in candidates if isinstance(candidate, dict)]


def _normalize_identity_contract(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    verdict = _first_present_value(value, ("identity_verdict", "verdict"))
    failure_codes = _first_present_value(
        value,
        ("identity_failure_codes", "failure_codes", "identity_failures"),
    )
    chain = _first_present_value(
        value,
        (
            "normalized_identity_chain",
            "identity_chain",
            "normalized_chain",
            "chain",
        ),
    )
    if chain is None and any(
        isinstance(value.get(name), dict)
        for name in ("hiring", "provider", "opening")
    ):
        chain = {
            name: value.get(name)
            for name in ("hiring", "provider", "opening")
            if isinstance(value.get(name), dict)
        }
    conflicts = _first_present_value(
        value,
        ("conflicting_fields", "identity_conflicting_fields"),
    )
    if verdict is None and failure_codes is None and chain is None and conflicts is None:
        return None
    return {
        "verdict": _normalize_terminal_value(verdict),
        "failure_codes": _normalized_failure_codes(failure_codes),
        "conflicting_fields": _normalized_conflicting_fields(conflicts),
        "normalized_chain": _normalize_identity_value(chain),
    }


def _is_identity_system_gap(outcome: dict | None, identity: dict | None) -> bool:
    if not isinstance(identity, dict):
        return False
    disposition = _terminal_disposition(outcome)
    return (
        disposition == "system_gap"
        or identity.get("verdict") == "system_gap"
        or "RESULT_IDENTITY_MISMATCH" in identity.get("failure_codes", [])
    )


def _first_present_value(value: dict, names: tuple[str, ...]) -> object:
    for name in names:
        if name in value and value[name] is not None:
            return value[name]
    return None


def _normalized_failure_codes(value: object) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return sorted(
        {
            item.strip().upper()
            for item in values
            if isinstance(item, str) and item.strip()
        }
    )


def _normalized_conflicting_fields(value: object) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return sorted(
        {
            " ".join(item.split()).casefold()
            for item in values
            if isinstance(item, str) and item.strip()
        }
    )


def _normalize_identity_value(value: object, *, key: str | None = None) -> object:
    if key and is_sensitive_key(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(name): _normalize_identity_value(item, key=str(name))
            for name, item in sorted(value.items())
            if str(name) not in {"schema_version", "verification_method"}
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_identity_value(item, key=key) for item in value]
    if isinstance(value, str):
        if key and key.casefold() in {"identifier", "tenant"} and value.lstrip().startswith("{"):
            try:
                structured = json.loads(value)
            except (json.JSONDecodeError, TypeError, ValueError):
                structured = None
            if isinstance(structured, dict):
                normalized = _normalize_identity_value(structured)
                return json.dumps(
                    normalized,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
        if key and "url" in key.casefold():
            return _canonical_public_url(value) or " ".join(value.split())
        return " ".join(value.split()).casefold()
    return value


def _normalize_terminal_value(value: object) -> object:
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    return _normalize_identity_value(value)


def _first_non_success_result_stage(record: dict) -> dict | None:
    stages = record.get("stages")
    if not isinstance(stages, list):
        return None
    stage_by_name = {
        stage.get("stage"): stage
        for stage in stages
        if isinstance(stage, dict) and stage.get("stage") in PIPELINE_STAGES
    }
    return next(
        (
            stage_by_name[stage_name]
            for stage_name in PIPELINE_STAGES
            if stage_name in stage_by_name
            and stage_by_name[stage_name].get("status") not in {"success", "not_applicable"}
        ),
        None,
    )


def _result_identity(record: dict) -> dict:
    provider = _optional_string(result_provider(record))
    return {
        "company_website_url": _canonical_public_url(record.get("company_website_url")),
        "hiring_entity_name": _normalized_identity_text(record.get("hiring_entity_name")),
        "career_page_url": _canonical_provider_board_identity(
            record.get("career_page_url"),
            provider,
        ),
        "job_list_page_url": _canonical_provider_board_identity(
            record.get("job_list_page_url"),
            provider,
        ),
        "open_position_url": _canonical_public_url(record.get("open_position_url")),
        "provider": provider,
    }


def _canonical_provider_board_identity(
    value: object,
    provider: str | None,
) -> str | None:
    canonical_url = _canonical_public_url(value)
    if canonical_url is None or provider is None:
        return canonical_url
    adapter = DEFAULT_PROVIDER_REGISTRY.adapter_named(provider)
    if adapter is None or not adapter.supports_listing:
        return canonical_url
    board = adapter.identify_board(canonical_url)
    if board is None or board.provider != provider:
        return canonical_url
    return _canonical_public_url(board.url)


def _successful_identity_prefix(record: dict | None) -> dict | None:
    if not isinstance(record, dict):
        return None
    stages = record.get("stages")
    if not isinstance(stages, list):
        return None
    stage_by_name = {
        stage.get("stage"): stage
        for stage in stages
        if isinstance(stage, dict) and stage.get("stage") in PIPELINE_STAGES
    }
    identity = _result_identity(record)
    fields_by_stage = {
        "website_resolution": ("company_website_url",),
        "hiring_identity_resolution": (
            "company_website_url",
            "hiring_entity_name",
        ),
        "career_discovery": ("career_page_url",),
        "job_board_discovery": ("job_list_page_url", "provider"),
        "opening_match": ("open_position_url",),
    }
    prefix: dict[str, str | None] = {}
    for stage_name in PIPELINE_STAGES:
        stage = stage_by_name.get(stage_name)
        if stage is None or stage.get("status") not in {"success", "not_applicable"}:
            break
        if stage.get("status") == "success":
            for field in fields_by_stage.get(stage_name, ()):
                prefix[field] = identity[field]
            if stage_name == "hiring_identity_resolution":
                career_root = _canonical_public_url(record.get("career_root_url"))
                if career_root is not None:
                    prefix["career_root_url"] = career_root
    return prefix


def _identity_prefix_matches(source: dict | None, replay: dict | None) -> bool:
    prefix = _successful_identity_prefix(source)
    if prefix is None:
        return source is None
    if not isinstance(replay, dict):
        return False
    replay_identity = _result_identity(replay)
    if "career_root_url" in prefix:
        replay_identity["career_root_url"] = _canonical_public_url(
            replay.get("career_root_url")
        )
    return all(replay_identity.get(field) == value for field, value in prefix.items())


def _is_budget_recovery(source: dict | None, replay: dict | None) -> bool:
    if not isinstance(source, dict) or not isinstance(replay, dict):
        return False
    source_failure = _first_non_success_result_stage(source)
    if not isinstance(source_failure, dict):
        return False
    source_stage = _optional_string(source_failure.get("stage"))
    if (
        source_failure.get("reason_code") != "COMPANY_TIME_BUDGET_EXHAUSTED"
        or source_stage not in PIPELINE_STAGES
        or _authoritative_upstream_executions(source, source_stage) is None
        or not _identity_prefix_matches(source, replay)
        or _has_reason_code(replay, "COMPANY_TIME_BUDGET_EXHAUSTED")
    ):
        return False

    replay_stages = replay.get("stages")
    if not isinstance(replay_stages, list):
        return False
    replay_by_name = {
        stage.get("stage"): stage
        for stage in replay_stages
        if isinstance(stage, dict) and stage.get("stage") in PIPELINE_STAGES
    }
    completed = replay_by_name.get(source_stage)
    if not isinstance(completed, dict) or completed.get("status") not in {
        "success",
        "not_applicable",
    }:
        return False
    replay_failure = _first_non_success_result_stage(replay)
    if replay_failure is None:
        return True
    replay_stage = _optional_string(replay_failure.get("stage"))
    return bool(
        replay_stage in PIPELINE_STAGES
        and PIPELINE_STAGES.index(replay_stage) > PIPELINE_STAGES.index(source_stage)
    )


def _canonical_public_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        normalized = normalize_url(value)
    except (TypeError, ValueError):
        return None
    return normalized[:-1] if normalized.endswith("/") and normalized.count("/") > 2 else normalized


def _normalized_identity_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).casefold()
    return normalized or None


def _stage_outcome(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {
        "stage": _optional_string(value.get("stage")),
        "status": _optional_string(value.get("status")),
        "reason_code": _optional_string(value.get("reason_code")),
    }


def _has_reason_code(record: dict | None, reason_code: str) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("stages"), list):
        return False
    return any(
        isinstance(stage, dict) and stage.get("reason_code") == reason_code
        for stage in record["stages"]
    )


def _contains_reason_code(value: object, reason_code: str) -> bool:
    if isinstance(value, dict):
        return value.get("reason_code") == reason_code or any(
            _contains_reason_code(nested, reason_code)
            for nested in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_reason_code(item, reason_code) for item in value)
    return False


def _record_field(
    primary: dict | None,
    secondary: dict | None,
    field: str,
    *,
    fallback: str | None = None,
) -> str | None:
    for record in (primary, secondary):
        if isinstance(record, dict):
            value = record.get(field) or (record.get(fallback) if fallback else None)
            if normalized := _optional_string(value):
                return normalized
    return None


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _write_json_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    main()
