# ADR-0023: Preflight Scoped Replay Execution Boundaries

- Status: accepted
- Date: 2026-07-14

## Context

A process budget can terminate a stage after earlier stages have atomically
published their evidence scopes but before the active stage finalizes its own
scope. The company result remains valid and retryable, but the interrupted stage
cannot be reproduced from the scoped outcome tape. Automatic failure-bundle
generation previously discovered this only after writing replay inputs and
starting replay, where it raised an uncaught `FailureReplayError` and replaced a
structured live gate with a Python traceback.

## Decision

1. Every scoped replay record is preflighted before checkpoint reset, replay
   input publication, tape materialization, or pipeline execution.
2. The effective replay start is derived by the same authoritative-upstream
   checkpoint rule used by execution. A resumable prefix starts at the first
   non-success stage; otherwise replay starts at S1.
3. At least one captured scope must exist at or after that start, and scopes
   must form a contiguous canonical prefix through the captured terminal stage.
   Earlier finalized scopes cannot stand in for an interrupted active stage.
4. A missing boundary produces an atomic failed bundle manifest with reason
   `replay_plan_integrity_failed`, outcome gate `failed`, a bounded list of
   affected record IDs/stages, and `boundary_invalid_count`. No replay input,
   checkpoint, or derived result is published.
5. Embedded live evaluation consumes the same manifest, writes final results,
   trace, summary, and filter/selection/export/replay counts, then exits through
   the normal bundle gate. It must not swallow the defect or emit an uncaught
   traceback.
6. A timeout record without a finalized scope remains retryable live evidence,
   not replay evidence. Replayability cannot be inferred from trace text,
   orphan snapshot records, an earlier stage scope, or a successful input URL.

## Consequences

- Live artifacts remain inspectable when automatic replay cannot prove a
  timeout outcome.
- Missing evidence remains a release-gate failure instead of a false replay
  success.
- Full and filtered bundles use one pre-execution integrity boundary; filtered
  selection does not relax scoped completeness.
