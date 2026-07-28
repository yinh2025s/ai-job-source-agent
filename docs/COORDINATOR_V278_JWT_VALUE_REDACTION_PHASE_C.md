# Coordinator `.278` JWT Value Redaction - Phase C

Date: 2026-07-28
Product adapter: `2026-07-28.278`
Decision: **focused offline privacy contract accepted**

## Scope

The `.277` measurement exposed signed JWT-shaped capability values in public
page snapshots for four independent companies:

- TreeHouse Foods;
- Tyler Technologies;
- Pitch Aeronautics;
- QXO.

All values entered through the same snapshot-body sanitizer and survived into
content-addressed blobs and replay tapes. No live request, new cohort, sealed
holdout, authenticated plugin flow or LLM work was used by `.278`.

## Implementation

The snapshot sanitizer now recognizes a bounded three-segment base64url value
only when:

1. header and payload decode to JSON objects;
2. the header has a non-empty `alg`;
3. the payload has a capability or time claim;
4. token segment and boundary lengths remain within fixed limits.

The complete value becomes `[REDACTED]`. Ordinary dotted strings, malformed
base64/JSON, missing-alg headers, claimless payloads, four-segment values and
tokens embedded in identifiers remain unchanged.

Both ordinary value boundaries and an explicit URL-encoded assignment
separator (`%3d`) are supported. This was required by the generic Q4 page
contract used by TreeHouse Foods; it is not keyed to a company, domain, issuer
or claim value.

Request identity, provider, tenant, discovery, title, location and S7 behavior
are unchanged. Advancing the adapter version to `.278` invalidates older
behavior checkpoints.

## Focused Corpus

Every JWT-bearing immutable page record from the `.277` snapshot store was
re-entered through the production `.278` `SnapshotStore`.

| Metric | Result |
| --- | ---: |
| Snapshot records | 9 |
| Independent hosts | 4 |
| Replay fixtures | 7 |
| Duplicate records | 4 |
| Privacy exclusions | 0 |
| Skipped records | 0 |
| Capture JWT / Google / AWS shapes | 0 / 0 / 0 |
| Replay JWT / Google / AWS shapes | 0 / 0 / 0 |

`replay_snapshots.py` completed successfully and validated the sanitized
content-addressed corpus. No usable token was restored and no request identity
was weakened.

Focused artifact:

`artifacts/releases/v278-jwt-value-redaction-20260728-run2.tar.zst`

SHA-256:

`59eec0f2e57a84dfb33e2d324ae3c9caa8606e3c3f9615cf8100349dadabb0c5`

## Tests And Safety

The snapshot, request-identity and snapshot-replay slice passed 119/119.
Integrated release gates then passed:

- full suite: 2,846 passed, 4 skipped;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 48 native adapters, 0 issues;
- tracked JWT/Google/AWS value-shape scan: 0;
- `git diff --check`: passed.

The first full-suite invocation reproduced the sandbox denial for a temporary
loopback extension-bridge bind. The identical permission-enabled offline rerun
passed; no external live traffic was used.

The focused capture and replay corpus is clean for JWT, Google browser-key and
AWS access-key shapes.

The `.277` Brown and Caldwell replay mismatch is unchanged because its nested
public `State` defect is outside this privacy contract. Crawford Thomas raw URL
serialization is also unchanged and remains below the implementation
threshold.

## Decision

Accept `.278` as a capture-time privacy correction. It does not change the
`.277` live score and does not authorize a new live batch. The integrated
offline gate, grouped commits and push are complete. The product-level
completion decision is recorded in
`docs/BACKEND_RELEASE_COMPLETION_AUDIT_V278.md`.
