# Fresh100 Current `.279` Replay Budget Semantics - Phase C

Date: 2026-07-28
Release adapter: `2026-07-28.279`
Source live adapter: `2026-07-28.278`
Decision: **qualified replay cluster closed; strict replay still open**

## Implementation

Strict scoped replay now restores the immutable Career transport-budget
snapshot from each source stage trace. The outcome tape remains authoritative
for request order and outcomes; the restored object supplies only the budget
diagnostics that the live pipeline used when deciding whether an official
surface miss was complete.

The wrapper validates the recorded transport limit and reserved-dispatch count
against the replay run configuration. It preserves recorded cache-hit
accounting and does not invent a budget object for historical records that did
not capture one. No provider, company, domain, job ID, candidate, identity or
opening rule changed.

## Focused Acceptance

Focused unit tests cover recorded budget restoration, cache-hit accounting,
configuration validation and the no-recorded-budget fallback.

The existing `.278` 100-record scoped capsule was then replayed without network
access:

| Replay metric | Before | After |
| --- | ---: | ---: |
| Reproduced | 93 | **96** |
| Budget recovery | 2 | **2** |
| Mismatch | 5 | **2** |
| Fixture gap | 0 | **0** |

Caesars Entertainment, ProMach and Systematic Business Consulting now retain
their live `CAREER_PAGE_NOT_FOUND` terminal instead of being upgraded to
`NO_PUBLIC_OPENINGS`. Pitch Aeronautics and Prophetic retain their legitimate
verified-absence terminal because their recorded outer transport budgets were
not exhausted.

## Remaining Replay Debt

- Versana changes from S5 `COMPANY_IDENTITY_AMBIGUOUS` to retryable
  `CONNECTION_FAILED`.
- Brown and Caldwell changes from an Exact UltiPro opening to
  `OPENING_NOT_FOUND`.
- Diamondback Energy and State of Montana remain classified as expected company
  budget recoveries.

Versana and Brown use different triggers and code paths. Neither is authorized
as a singleton implementation cluster.

## Release Gates

- focused replay/composition/discovery slice: 192/192;
- full suite: 2,848 passed, 4 skipped;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 48 native adapters, 0 issues;
- tracked credential-shape scan: 0;
- `git diff --check`: passed.

The first full-suite run hit the sandbox restriction on a temporary loopback
extension-bridge bind. The identical permission-enabled offline run passed; no
external network traffic was used.

## Live Safety And Privacy

The immutable `.278` live result is 31 S7 Exact out of 100. All 31 authoritative
Exact outputs have verified identity assertions and passed company, title,
location, provider, tenant and opening-URL review. Wrong URL, wrong company,
wrong location and cross-tenant publication are zero.

Lab37 is one legacy-field consistency defect: its authoritative pipeline and S7
output are successful and safe, while legacy `status` remains failed and the
public Job List field is suppressed. It is not changed in this replay phase.

The raw `.278` capsule is not shareable. Ten Google browser-key-shaped values
from one public Maps script remain in trace, checkpoint and completion
serialization. Snapshot index/blob integrity passes, but mutable `sites`
aliases must not be treated as authoritative blobs.

## Decision

Accept the three-company replay-determinism closure and release it as `.279`.
Do not rewrite the immutable `.278` live score. Do not start another live
benchmark automatically. Strict replay remains below the final 100/100 goal,
and the privacy residual remains explicit.

Evidence roots:

- Live source: `/private/tmp/fresh100-current-v278-cold-20260728-run1`
- Accepted replay diagnostic:
  `/private/tmp/fresh100-current-v279-replay-recorded-budget-final`
