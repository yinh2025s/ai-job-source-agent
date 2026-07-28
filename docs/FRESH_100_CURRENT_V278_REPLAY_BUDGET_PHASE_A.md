# Fresh100 Current `.278` Replay Budget Semantics - Phase A

Date: 2026-07-28
Product adapter under analysis: `2026-07-28.278`
Input cohort: `samples/evaluation/live100_fresh_cohort_20260718.json`
Decision: **one bounded replay-determinism cluster qualifies**

## Measurement Evidence

The user-authorized `.278` cold live completed 100/100 records from new
checkpoint, completion, evidence and snapshot roots. Code remained frozen
through live and strict replay.

| Metric | `.278` live |
| --- | ---: |
| Website | 90 |
| Career | 76 |
| Verified Job List | 69 |
| S7 Exact | 31 |
| Pipeline success / partial / failed | 31 / 47 / 22 |

Full replay exported, replayed and compared 100/100 records with zero fixture
gap, but failed at 93 reproduced, two budget recoveries and five mismatches.
The raw capsule is not shareable because one public Google Maps browser key
survives in trace, checkpoint and completion serialization. Snapshot index,
blob hashes, byte counts, store identity and sequence continuity pass.

## Qualified Cluster

Three independent companies share one trigger and one code path:

- Caesars Entertainment
- ProMach
- Systematic Business Consulting

In live execution, Career discovery consumes the bounded stage transport
budget. Its trace records `transport_budget.exhausted=true` and
`search_discovery.source_fetch_budget_exhausted=true`. S5 still performs
candidate discovery, but correctly declines to classify the result as a
verified absence and retains `job_board_discovery=not_run`.

Replay reconstructs the recorded fetch outcomes through an injected scoped
tape fetcher, but does not restore the immutable Career transport-budget
snapshot recorded in the source stage trace. Its Career trace therefore lacks
the top-level `transport_budget` object. Replay consequently publishes
`job_board_discovery=partial / NO_PUBLIC_OPENINGS`, changing the aggregate
pipeline from failed to partial.

This is not a provider, company or search-recall defect. It is one conservative
strict-replay state-restoration defect in:

`scripts/replay_failure_bundle.py`

## Frozen Scope

The implementation may only make strict replay expose the immutable,
source-recorded Career transport-budget snapshot while the outcome tape remains
the execution authority. It must not:

- add provider, company, domain or job-ID branches;
- change candidate production, provider verification or identity gates;
- promote any record to Exact or Verified No Match;
- enforce the recorded budget outside the tape or consume requests out of order;
- invent a budget snapshot when the source stage did not record one;
- change the verified-absence predicate;
- change sealed cohorts, plugin, coordinator-v2 or the LLM branch.

## Acceptance

1. Add focused replay tests covering recorded budget snapshot restoration,
   cache-hit accounting and the no-recorded-budget fallback.
2. Replay all three affected `.278` records from the existing scoped snapshots.
3. All three must reproduce their live terminal and stage statuses.
4. Full `.278` replay must improve from 93 to at least 96 reproduced with zero
   fixture gaps.
5. Brown and Caldwell, Versana, Diamondback Energy and State of Montana remain
   separate debt unless their own evidence changes.
6. Run the integrated offline release gate before commit and push.

## Rollback

Revert the single predicate and its focused tests. The `.278` live artifacts
remain immutable historical evidence regardless of the `.279` replay result.
