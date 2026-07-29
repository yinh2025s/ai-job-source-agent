# Coordinator `.286` Typed Candidate Route Outcome Phase C

Date: 2026-07-29

Decision: **accepted for stage-v1 execution semantics**

## Scope

This phase preserves the outcome of stage-v1 candidate discovery from the
search producer through the composite wave and S5. It does not add candidates,
change ranking, authorize a provider or tenant, enable coordinator-v2, or alter
S7 identity checks.

## Implementation

- Added immutable `CandidateDiscoveryOutcome` and
  `CandidateDiscoveryWaveResult`.
- Distinguished produced candidates, completed-empty work, inapplicable
  producers, retryable source failure, deterministic source rejection, budget
  exhaustion and source-local candidate rejection.
- Made Career search produce typed failure and budget evidence instead of
  flattening every empty list to `no_valid_candidates`.
- Propagated producer outcomes through External Apply, direct Website/Career,
  Career-surface and Provider-search discovery.
- Made S5 retain an executed empty/failing candidate route as a concrete
  terminal. Only an all-inapplicable route set may remain `not_run`.
- Required a serialized provider identity with
  `relationship_verified=true` before reporting a denial as
  `external_blocked`; pre-relationship denials remain
  `discovery_unresolved`.

Adapter version advanced to `2026-07-29.286`. Result, identity, checkpoint and
run-configuration schemas did not change.

## Focused Measurement

Input: six original Fresh100 development records, with original LinkedIn job
IDs, in a new isolated root:

`/private/tmp/fresh6-v286-typed-outcome-20260729-run1`

| Company | `.283` S5 | `.286` S5 |
| --- | --- | --- |
| Caesars Entertainment | `not_run` | `BOT_PROTECTION` |
| Sunwest Bank | `not_run` | `FETCH_BUDGET_EXHAUSTED` |
| City of Pharr, TX | `not_run` | `BOT_PROTECTION` |
| Nisga'a Tek, LLC | `not_run` | `BOT_PROTECTION` |
| Benefis Health System | `not_run` | `BOT_PROTECTION` |
| Systematic Business Consulting | `not_run` | `BOT_PROTECTION` |

All six now record that S5 executed. The change publishes zero Job List or
opening URLs and therefore creates no company, tenant, title or location claim.
The final reporting projection is five `discovery_unresolved` and one
`retryable_failure`; none is represented as a verified External Block.

The final scoped-outcome-tape replay exported and reproduced 6/6 records with:

- 0 mismatch;
- 0 fixture gap;
- 0 expected transition;
- 0 budget recovery;
- 30 evidence scopes and 297 recorded outcomes.

The standard replay outcome gate compares the earliest pipeline failure, which
is S4 for these six records. A separate automatic S5 projection audit therefore
compares `status`, `reason_code`, `retryable` and the complete typed
`candidate_route_outcome` between live and replay. It passes 6/6 with zero
mismatch:

`/private/tmp/fresh6-v286-typed-outcome-20260729-run1/s5-projection-audit.json`

The live `summary.json` was produced before the reporting relationship gate was
tightened and is retained as a stale diagnostic rather than overwritten. The
current-code final reporting summary is:

`/private/tmp/fresh6-v286-typed-outcome-20260729-run1/final-reporting-summary.json`

## Gates

- focused affected tests: 363/363;
- final full tests: 2,907 passed, 4 skipped;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 48 adapters, 0 issues;
- `git diff --check`: passed.

## Decision Boundary

The contract is accepted because six independent records share the same lost
producer-state path and all six change from false `not_run` to reproducible
typed S5 terminals. This is a causal-reporting correction, not a recall gain.
The authoritative Fresh100 score remains `.283` at 36/100 until a separately
authorized code-frozen full measurement.
