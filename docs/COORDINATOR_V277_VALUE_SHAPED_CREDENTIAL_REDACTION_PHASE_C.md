# Coordinator `.277` Value-Shaped Credential Redaction - Phase C

Date: 2026-07-28
Product adapter: `2026-07-28.277`
Decision: **offline contract accepted; live artifact closure pending**

## Scope

`.277` closes one qualified snapshot-body privacy cluster from the existing
Fresh100 `.275` cold capture. No new cohort, live request, sealed holdout,
authenticated LinkedIn session or LLM branch was used.

Phase A identified six independent companies whose persisted HTML/JavaScript
contained high-confidence credential-shaped values that the field-name
sanitizer missed:

- Aperia
- City of Lubbock
- The Home Depot
- QXO
- Hays + Sons
- Wolfe

Crawford Thomas Recruiting exposed a separate raw extracted-URL trace path.
That one-company residual is not included in `.277`.

## Implementation

`sanitize_snapshot_body` now redacts two bounded value formats:

- fixed-length Google browser API keys with the `AIza` prefix;
- fixed-length AWS access key IDs with the `AKIA` or `ASIA` prefix.

The replacement happens after existing structured and text sanitation. It is
independent of company, domain and framework field names, preserves surrounding
HTML/JavaScript/URL shape, and is idempotent.

Near-miss prefixes and lengths remain unchanged. Provider, request, identity,
tenant, title, location, scheduler and S7 behavior are untouched. The adapter
version advances to `.277`, invalidating prior behavior checkpoints.

## Focused Corpus Gate

Every credential-bearing snapshot record from the raw `.275` capture was
re-entered through the production `SnapshotStore` under `.277`:

| Metric | Result |
| --- | ---: |
| Snapshot records captured | 18 |
| Original evidence scopes | 10 |
| Independent hosts | 6 |
| Verified replay fixtures | 14 |
| Privacy exclusions | 0 |
| Google/AWS shape matches after capture | 0 |
| Google/AWS shape matches after replay conversion | 0 |

`replay_snapshots.py` verified metadata, byte counts, content-addressed blob
hashes, request identity, paths and sanitized-body fixed points. The converted
fixture manifest completed successfully.

This is stronger than post-hoc archive substitution: every digest and fixture
was generated from already-sanitized bytes.

## Tests

Focused contract tests:

- snapshot sanitation: 42/42;
- request identity: 19/19;
- checkpoint metadata: 9/9;
- snapshot replay: 53/53;
- outcome tape: 13/13;
- stage checkpoint: 26/26;
- batch completion checkpoint: 12/12;
- failure replay bundle: 112/112.

Total focused: 286/286.

Integrated release gates:

- full suite: 2,841 passed, 4 skipped;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 48 native adapters, 0 issues;
- tracked credential-shape scan: 0 matches;
- `git diff --check`: passed.

The first full-suite run reproduced the known sandbox denial for a loopback
extension-bridge bind. The identical permission-enabled rerun passed all 2,841
tests; this was a local offline HTTP test, not external live traffic.

## Safety And Residuals

`.277` does not change discovery or publication outcomes. It cannot alter
company, provider, tenant, title, location or opening identity gates.

The current `.275` release archive remains intentionally audit-only because it
was scrubbed after capture. `.277` proves the future capture path for the six
selected hosts, but no new end-to-end live capsule was created under `.277`.

Crawford Thomas remains a separate one-company trace/checkpoint/completion
serialization residual. It is below the three-company implementation threshold
and must not be hidden inside this closure.

## Decision

Accept the `.277` snapshot-body contract and tests. Do not start another live
batch. Future live release work must prove:

1. newly captured snapshots are clean before hashing;
2. trace/checkpoint/completion outputs are independently privacy-safe;
3. the resulting unmodified capsule replays with intact digests.

Fresh100 terminal recall, its failed 100-record replay gate, current-version
Frozen100 no-regression and two unseen-cohort acceptance gates remain open.

## Artifact

The privacy-clean focused capture and replay-conversion evidence is preserved
as:

`artifacts/releases/v277-value-shaped-credential-redaction-20260728-run1.tar.zst`

The tracked checksum is:

`artifacts/releases/v277-value-shaped-credential-redaction-20260728-run1.tar.zst.sha256`

SHA-256:

`2fa81c698d7cc82b4c5e5b95a3cff1dacd6cd6681b4a6cf558f8ecfbc3ce9c9b`
