from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .checkpoint import input_fingerprint
from .evidence_scope import EvidenceScopeRef, StageEvidenceLineage
from .models import PIPELINE_STAGES


EvidenceMode = Literal["scoped_outcome_tape", "legacy_global_latest"]
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ReplayRecordPlan:
    """Privacy-safe replay identity and evidence selection for one input occurrence."""

    source_ordinal: int
    record_id: str
    evidence_mode: EvidenceMode
    stage_evidence_lineage: tuple[StageEvidenceLineage, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.source_ordinal, bool)
            or not isinstance(self.source_ordinal, int)
            or self.source_ordinal < 1
        ):
            raise ValueError("source_ordinal must be a positive integer")
        if not isinstance(self.record_id, str) or _SHA256_PATTERN.fullmatch(self.record_id) is None:
            raise ValueError("record_id must be a lowercase SHA-256 digest")
        if self.evidence_mode not in {"scoped_outcome_tape", "legacy_global_latest"}:
            raise ValueError("evidence_mode is unknown")
        if not isinstance(self.stage_evidence_lineage, tuple) or not all(
            isinstance(item, StageEvidenceLineage) for item in self.stage_evidence_lineage
        ):
            raise TypeError("stage_evidence_lineage must be a tuple of StageEvidenceLineage")
        _validate_canonical_lineage(self.stage_evidence_lineage)
        if self.evidence_mode != _evidence_mode(self.stage_evidence_lineage):
            raise ValueError("evidence_mode does not match stage evidence lineage")

    def scope_for_stage(self, stage: str) -> EvidenceScopeRef | None:
        """Return this occurrence's frozen scope for a canonical pipeline stage."""

        if stage not in PIPELINE_STAGES:
            raise ValueError(f"Unknown evidence stage: {stage!r}")
        for lineage in self.stage_evidence_lineage:
            if lineage.stage == stage:
                return lineage.snapshot_scope
        return None


def build_replay_record_plans(
    source_records: Sequence[dict[str, Any]],
    replay_records: Sequence[dict[str, Any]],
) -> tuple[ReplayRecordPlan, ...]:
    """Build strict, occurrence-isolated replay plans for an aligned record batch."""

    if isinstance(source_records, (str, bytes)) or not isinstance(source_records, Sequence):
        raise TypeError("source_records must be a sequence")
    if isinstance(replay_records, (str, bytes)) or not isinstance(replay_records, Sequence):
        raise TypeError("replay_records must be a sequence")
    if len(source_records) != len(replay_records):
        raise ValueError("Replay source and input record counts do not match")

    plans: list[ReplayRecordPlan] = []
    selected_mode: EvidenceMode | None = None
    for source_ordinal, (source_record, replay_record) in enumerate(
        zip(source_records, replay_records), start=1
    ):
        if not isinstance(source_record, dict) or not isinstance(replay_record, dict):
            raise ValueError("Replay source and input records must be objects")

        lineage = _extract_lineage(source_record)
        mode = _evidence_mode(lineage)
        if selected_mode is not None and mode != selected_mode:
            raise ValueError("Scoped and legacy replay records cannot be mixed")
        selected_mode = mode

        execution_fingerprint = source_record.get("execution_fingerprint")
        if execution_fingerprint is not None and (
            not isinstance(execution_fingerprint, str)
            or _SHA256_PATTERN.fullmatch(execution_fingerprint) is None
        ):
            raise ValueError("Source execution_fingerprint must be a lowercase SHA-256 digest")
        if (
            execution_fingerprint is not None
            and lineage
            and lineage[0].execution_fingerprint != execution_fingerprint
        ):
            raise ValueError("Source and lineage execution fingerprints do not match")

        plans.append(
            ReplayRecordPlan(
                source_ordinal=source_ordinal,
                record_id=_record_id(
                    source_ordinal,
                    input_fingerprint(replay_record),
                    execution_fingerprint,
                ),
                evidence_mode=mode,
                stage_evidence_lineage=lineage,
            )
        )
    return tuple(plans)


def _extract_lineage(source_record: dict[str, Any]) -> tuple[StageEvidenceLineage, ...]:
    if "stage_evidence_lineage" in source_record:
        payloads = source_record["stage_evidence_lineage"]
    else:
        trace = source_record.get("trace")
        payloads = trace.get("stage_evidence_lineage", []) if isinstance(trace, dict) else []

    if not isinstance(payloads, list):
        raise ValueError("Source stage_evidence_lineage must be a list")

    restored = tuple(StageEvidenceLineage.from_payload(payload) for payload in payloads)
    _validate_canonical_lineage(restored)
    return restored


def _validate_canonical_lineage(lineage: tuple[StageEvidenceLineage, ...]) -> None:
    seen_stages: set[str] = set()
    execution_fingerprints: set[str] = set()
    last_stage_index = -1
    for item in lineage:
        stage_index = PIPELINE_STAGES.index(item.stage)
        if item.stage in seen_stages or stage_index <= last_stage_index:
            raise ValueError("Source stage evidence lineage is not canonical")
        seen_stages.add(item.stage)
        execution_fingerprints.add(item.execution_fingerprint)
        last_stage_index = stage_index

    if len(execution_fingerprints) > 1:
        raise ValueError("Source stage evidence lineage has multiple execution fingerprints")


def _evidence_mode(lineage: tuple[StageEvidenceLineage, ...]) -> EvidenceMode:
    scoped_count = sum(item.snapshot_scope is not None for item in lineage)
    if scoped_count == 0:
        return "legacy_global_latest"
    if scoped_count != len(lineage):
        raise ValueError("Source stage evidence lineage is only partially scoped")
    return "scoped_outcome_tape"


def _record_id(
    source_ordinal: int,
    replay_input_fingerprint: str,
    source_execution_fingerprint: str | None,
) -> str:
    payload = {
        "replay_input_fingerprint": replay_input_fingerprint,
        "source_execution_fingerprint": source_execution_fingerprint,
        "source_ordinal": source_ordinal,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()
