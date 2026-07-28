# `.281` Recorded Company-Budget Boundary Phase A

## Decision

One replay-determinism defect qualifies for Phase B:

> A live record terminates a pipeline stage with
> `COMPANY_TIME_BUDGET_EXHAUSTED`, but scoped offline replay consumes the same
> captured request tape without real network latency and changes the same
> stage to a non-retryable semantic terminal such as
> `CAREER_PAGE_NOT_FOUND`.

This is one observable trigger and one replay production path across many
independent companies. It does not change live discovery behavior.

## Evidence

The current Fresh100 `.278` capsule contains:

| Company | Live terminal | Current replay terminal |
| --- | --- | --- |
| Diamondback Energy | Career / `COMPANY_TIME_BUDGET_EXHAUSTED` | Career / `CAREER_PAGE_NOT_FOUND` |
| State of Montana | Career / `COMPANY_TIME_BUDGET_EXHAUSTED` | Career / `CAREER_PAGE_NOT_FOUND` |

The same trigger is independently present in unsealed diagnostic capsules,
including:

- 3M;
- Chipply;
- Eaton;
- Storyteller Overland;
- Slang AI;
- Cortland;
- Pinterest;
- Nintendo.

For each record:

1. the source has a finalized scoped capture boundary;
2. the first source failure is `COMPANY_TIME_BUDGET_EXHAUSTED`;
3. replay consumes the captured request outcomes;
4. the verified upstream identity prefix is unchanged;
5. replay reaches the same stage and emits a non-retryable semantic terminal;
6. the current outcome gate classifies the difference as
   `company_budget_replay_normalized`.

The current gate treats that classification as an accepted budget recovery.
The product goal is stricter: same-version replay requires zero recoveries and
an equal terminal outcome.

## Root Cause

The live outer company deadline is wall-clock state owned by the batch runner.
Scoped replay owns deterministic request outcomes, but it intentionally avoids
live network latency. After consuming the same stage tape, the provider logic
therefore reaches its ordinary semantic no-result terminal instead of
observing the recorded outer deadline.

The captured request tape alone cannot reproduce elapsed wall time. The live
terminal boundary is nevertheless authoritative evidence and is already
serialized in the source result.

## Phase B Contract

Add a replay-only recorded company-budget boundary projection after scoped
tape execution and before replay result serialization.

The projection may run only when all of these hold:

1. source first failure is `COMPANY_TIME_BUDGET_EXHAUSTED`;
2. replay first failure occurs in the same pipeline stage;
3. replay failure is structured, non-retryable and not
   `OFFLINE_FIXTURE_MISSING` or tape divergence;
4. the source's successful upstream identity prefix matches replay;
5. the scoped controller has already consumed all expected request outcomes;
6. no declared expected transition exists.

When eligible, replay must:

- restore the source terminal status, reason code and retryability;
- restore source public output fields at and after that terminal boundary;
- preserve replay run configuration and record identity;
- retain the underlying replay outcome in explicit replay trace diagnostics;
- classify the record as `reproduced`, not `budget_recovery`.

The projection must not:

- alter live execution;
- synthesize a Career page, Job Board or opening;
- convert a failure to Exact or Verified No Match;
- hide retryable transport drift, fixture gaps, tape divergence or identity
  changes;
- project a boundary when replay advances to a later stage;
- use company, domain, title, location or job-ID special cases.

## Ownership

Main-line ownership:

- `scripts/replay_failure_bundle.py`;
- `tests/test_replay_failure_bundle.py`;
- adapter version and governance documents.

No provider, pipeline, matcher, identity, extension, LLM or sealed-cohort file
is in scope.

## Acceptance

### Unit

- same-stage deterministic budget normalization is projected to the recorded
  source terminal;
- underlying replay outcome remains visible in trace diagnostics;
- retryable drift, identity drift, fixture gaps and later-stage advancement
  remain unprojected;
- ordinary reproduced and mismatched records are unchanged.

### Existing Capsules

Run current code against isolated copies of:

1. Fresh100 `.278` full replay capsule;
2. at least one unsealed diagnostic capsule containing three or more
   independent same-stage budget normalizations.

Expected:

- Fresh100 budget recovery count changes from 2 to 0;
- all selected diagnostic budget normalizations become reproduced;
- no mismatch or fixture gap is converted by the projection;
- record count, tape consumption and identity comparison remain intact.

This phase does not claim Fresh100 strict replay closure: Versana and Brown and
Caldwell remain separate mismatches.

## Rollback

If any projected record changes verified identity, consumes a different tape,
suppresses a retryable/integrity error or fails to reproduce its source
terminal exactly, revert the projection and keep the existing
`budget_recovery` accounting.
