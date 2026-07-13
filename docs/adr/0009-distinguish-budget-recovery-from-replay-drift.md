# ADR-0009: Distinguish Budget Recovery From Replay Drift

## Status

Accepted, 2026-07-14.

## Context

Failure replay intentionally omits the live runner's outer company process deadline. A live
attempt can therefore publish successful upstream stage checkpoints, hit
`COMPANY_TIME_BUDGET_EXHAUSTED` in a later stage, and then complete that stage offline from the
captured public evidence. Treating every such advance as an ordinary mismatch makes a valid
checkpoint recovery indistinguishable from URL, provider, or outcome drift. Declaring the final
outcome in advance is also not possible because a wall-clock interruption has no domain result.

The same live cohort exposed a separate replay-integrity failure. Snapshot body redaction treated
the sensitive key `code` as a suffix match inside `urlCode`, changing a valid Taleo configuration
before persistence. Live execution consumed the original page and reached a recorded HTTP 500,
while replay consumed the damaged fixture and stopped earlier with `INVALID_STRUCTURED_DATA`.

## Decision

- Failure bundle schema `4` adds the passing classification `budget_recovery`. It applies only
  when the source's first non-success stage is exactly
  `COMPANY_TIME_BUDGET_EXHAUSTED`, every upstream stage forms an authoritative successful prefix,
  replay has no fixture gap, replay completes the timed-out stage, and its next failure is strictly
  later or absent.
- A budget recovery must preserve every identity established by the successful source prefix.
  The gate compares canonical website, non-empty career root, hiring entity, career page, job-list
  URL, provider, and opening URL as those stages become authoritative. Newly discovered downstream
  identities may be added only after the interrupted stage.
- Replay records include the source identity prefix and the replay's full canonical result identity
  for audit. A repeated timeout, earlier failure, missing structured stage chain, changed identity,
  or ordinary non-budget improvement is not a budget recovery.
- `OFFLINE_FIXTURE_MISSING` keeps precedence over every declared or inferred transition. Fixture
  gaps remain `incomplete`; unapproved behavior or identity changes remain `mismatch`.
- Explicit `expected_transition` remains available for deliberate behavior changes, but when the
  full source result is available it must also preserve the authoritative source identity prefix.
  A declaration cannot hide URL or provider drift.
- Unquoted snapshot-body sensitive keys require a left JavaScript identifier boundary. Standalone
  `code=...` and `code: ...` remain redacted, while compound public configuration names such as
  `urlCode`, `statusCode`, and `countryCode` are preserved. Quoted sensitive keys and all existing
  token, cookie, CSRF, API-key, URL, body, and header privacy rules are unchanged.

## Consequences

The replay gate can now distinguish a deterministic continuation from a live wall-clock
interruption without weakening fixture completeness or URL correctness. Budget recovery is an
auditable classification, not a blanket "improvement is success" rule. Schema-3 manifests remain
historical artifacts and are not silently rewritten as schema 4.

Snapshot fixtures better preserve executable public configuration while retaining standalone
credential redaction. Existing blobs that were already over-redacted cannot be repaired by
guessing; affected pages must be captured again. Percepta's new capture preserves `urlCode`,
redacts `sessionCSRFToken`, and reproduces the live Taleo HTTP failure with exact request identity.
