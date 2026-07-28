# ADR-0033: Replay Recorded Company-Budget Boundaries

Status: accepted

Date: 2026-07-29

## Context

ADR-0009 permits an explicit `budget_recovery` when offline replay advances
beyond a live outer company timeout while preserving the authoritative
identity prefix. A different deterministic case remained unresolved.

Some live records finish a scoped stage tape and then publish
`COMPANY_TIME_BUDGET_EXHAUSTED` because the batch runner's outer wall-clock
deadline expires. Offline replay consumes the same request outcomes without
real network latency and reaches a non-retryable semantic terminal in the same
stage, commonly `CAREER_PAGE_NOT_FOUND`. The request evidence and identity are
stable, but elapsed wall time is not represented in the tape.

Treating this as a passing `budget_recovery` leaves the replay outcome unequal
and prevents the final zero-recovery gate. Simulating network latency would be
slow, environment-dependent and less deterministic.

## Decision

After a scoped outcome tape has been fully consumed, replay may restore the
recorded outer company-budget terminal when:

1. the source first failure is `COMPANY_TIME_BUDGET_EXHAUSTED`;
2. replay's first failure is in the same pipeline stage;
3. replay's failure is structured and non-retryable;
4. the successful source identity prefix still matches;
5. no fixture gap, tape divergence or declared expected transition exists.

The replay projection restores source public fields and typed stage outcomes at
and after the recorded boundary. It preserves the current replay run
configuration and record identity. The unprojected replay outcome and stages
remain in an explicit trace diagnostic.

Retryable drift, identity drift and replay advancement to a later stage remain
unprojected. ADR-0009 continues to own genuine later-stage
`budget_recovery`; this decision only covers same-stage normalization.

Legacy and scoped replay bundle schemas advance to `6` and `8` respectively.
The manifest records the number of projected boundaries, and summary trace
records expose the same count.

## Consequences

- Same-stage wall-clock normalization becomes an equal, reproducible terminal
  instead of an accepted recovery.
- The live pipeline, provider adapters, identity gates and candidate selection
  do not change.
- Replay cannot use this projection to create a Website, Career page, Job
  Board, opening or stronger terminal disposition.
- Historical manifests retain their original schema and classification.
- Cross-version capsules can still fail earlier on config parity or unrelated
  tape divergence; the projection does not bypass those checks.
