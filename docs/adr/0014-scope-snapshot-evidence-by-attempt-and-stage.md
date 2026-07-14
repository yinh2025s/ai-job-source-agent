# ADR-0014: Scope Snapshot Evidence By Attempt And Stage

- Status: accepted
- Date: 2026-07-14

## Context

ADR-0006 made snapshot outcomes request-aware and ordered successful pages and
typed failures with a shared sequence. Materialization still selects the latest
outcome for each request across an entire snapshot root. That is unsafe when the
root contains parallel companies, multiple invocations, or a retry that restores
upstream checkpoints and recomputes only a downstream suffix.

The completion retry policy in ADR-0013 makes mixed-attempt results an ordinary
case. A durable result must identify the evidence used by each stage without
inferring provenance from timestamps, paths, trace strings, or global sequence.

## Decision

1. Every pipeline invocation has a privacy-safe random capture attempt ID. The
   live batch parent creates one ID per company invocation and passes the same ID
   through its S1-S3 and S4-S7 child processes. A retry or explicit rerun creates
   a new ID.
2. Every executed stage receives a typed `StageEvidenceLineage` containing the
   pipeline execution fingerprint, producer attempt ID, stage, and an optional
   `EvidenceScopeRef`. Restored checkpoints retain their original lineage, so one
   final completion may correctly contain scopes from multiple attempts.
3. Snapshot-enabled stages always publish a scope, including a zero-request scope.
   A null scope means capture was not configured, never "search the snapshot root
   for a plausible older outcome."
4. Snapshot schema v3 page and failure records carry the store ID, scope ID,
   attempt ID, execution fingerprint, canonical stage, and a stage-local request
   ordinal. The frozen scope also carries count, sequence bounds, and a digest of
   privacy-safe terminal outcome descriptors. Sequence bounds are diagnostic and
   do not define membership because parallel scopes may interleave.
5. Capture records caller-visible terminal fetch outcomes. Retry attempts remain
   internal. Cache hits used by another stage are recorded in that stage's scope,
   so the stage can replay independently.
6. Scoped replay consumes every outcome in the exact declared scope by sanitized
   request identity. Independent identities may be requested in a different order
   because concurrent discovery does not guarantee thread scheduling; repeated
   occurrences of the same identity remain separate and are consumed in capture
   order. Consumption is atomic. A missing, extra, mismatched, or unconsumed
   request is a typed replay divergence, and scoped replay never falls back to
   global latest-wins fixtures.
7. Each replay source occurrence receives a stable record ID, isolated checkpoint
   directory, application, tape cursor, and runtime cache. Outcome gates join by
   record ID rather than list position. Duplicate, missing, or unexpected IDs fail
   closed.
8. Contract schema moves to 1.3, stage checkpoint schema to 1.5, batch completion
   schema to 1.2, snapshot schema to v3, and scoped replay bundle schema to v6.
   Adapter version changes with the implementation. Result schema may remain 2.1
   because authoritative lineage is carried by checkpoint/completion and replay
   contracts, while a privacy-safe copy may be exposed in trace for diagnostics.
9. Legacy v1/v2 snapshots and bundle v5 remain available only through explicit
   `legacy_global_latest` materialization. Scoped and unscoped selected records
   cannot be mixed. Legacy records never compete with v3 records inside a scope.
10. Missing scopes, count/digest mismatches, wrong stage or execution identity,
    unknown fields, prefix gaps, and corrupt records fail closed. Orphan snapshots
    from an interrupted stage are ignored unless a durable checkpoint references
    their finalized scope.
11. The legacy snapshot materializer accepts only v1/v2 indexes. If either page or
    terminal-failure input contains any schema-v3 scoped record, including a mixed
    legacy/scoped index, it fails before cleaning or writing output and directs the
    caller to bundle-v6 scoped replay. Filtering v3 records into an empty legacy
    success is forbidden.

## Privacy

Scope and lineage contracts may contain only opaque IDs, SHA-256 digests,
canonical stage names, integer counts, and sequence bounds. They must not contain
absolute paths, raw URLs, HTML, request bodies, headers, cookies, tokens, browser
storage, screenshots, or authenticated LinkedIn payloads. Existing ADR-0006
sanitization remains mandatory for each outcome record.

## Consequences

- Crash recovery and selective retry can prove exactly which attempt produced
  every restored or recomputed stage.
- Parallel companies and repeated request identities cannot overwrite one
  another during replay.
- Concurrent requests with independent identities can change scheduling order
  without changing the declared evidence set or weakening duplicate ordering.
- Zero-request stages cannot silently borrow evidence from an older invocation.
- Existing captures remain useful for explicit legacy diagnosis but cannot claim
  attempt-scoped deterministic reproduction.
- A modern scoped capture cannot be accidentally published as an empty or partial
  legacy fixture tree.
- The implementation requires coordinated schema changes across runner,
  checkpoint, completion, snapshot, and replay boundaries.

## Validation

- Contract tests reject malformed IDs, unknown fields, mismatched stage/attempt,
  invalid sequence bounds, and non-canonical digests.
- Capture tests cover concurrent ordinals, zero-request scopes, success/failure,
  retry terminality, and cache hits across stages.
- Checkpoint and completion tests cover schema invalidation and mixed-attempt
  lineage across selective retry and crash recovery.
- Replay tests cover repeated identities, concurrent scheduling permutations,
  exact-once consumption, mismatch non-consumption, cross-stage and cross-attempt
  outcomes, isolated duplicate inputs, tape divergence, and legacy compatibility.
- Legacy materialization tests cover v3-only and mixed-index rejection, nonzero
  CLI exit, actionable bundle-v6 guidance, and preservation of existing output.
- Main runs all offline gates and one serialized scoped crash-resume/replay
  acceptance. Authenticated LinkedIn extension acceptance remains independently
  deferred.
