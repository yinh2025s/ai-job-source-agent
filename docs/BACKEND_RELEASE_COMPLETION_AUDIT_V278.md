# Backend Release Completion Audit After `.278`

Date: 2026-07-28
Audited release commit: `5d71343c65a50b65665a7282133e4f0f399b299b`
Product adapter: `2026-07-28.278`
Decision: **release clean and pushed; product goal remains open**

## Scope

This audit supersedes the goal-status portion of
`docs/BACKEND_RELEASE_COMPLETION_AUDIT_V277.md` without rewriting historical
evidence. It incorporates the code-frozen Fresh100 `.277` measurement and the
offline `.278` JWT snapshot-privacy closure.

No new live request, Frozen100 run, sealed holdout, authenticated plugin run or
LLM integration was used. At the audited commit, `main` is clean and equals
`origin/main`.

## Authoritative Evidence

The `.277` Fresh100 development-cohort measurement completed 100/100 records
from new checkpoint, completion, evidence, snapshot and replay roots:

- 87 verified Websites;
- 73 Career pages;
- 69 verified Job Lists;
- 29 S7 Exact openings;
- 29/29 published Exact results passed company, title, location, provider,
  tenant and opening-URL review;
- zero wrong URL, wrong location, cross-company or cross-tenant publication
  among those 29 results.

The raw causal ledger is 29 Exact, 19 evidence-backed Verified No Match, one
External Blocked and 51 unresolved. Full replay completed all 100 records but
failed the outcome gate at 98 reproduced, one Diamondback Energy budget
recovery, one Brown and Caldwell mismatch and zero fixture gaps.

`.278` changes capture-time privacy behavior, not discovery or publication
outcomes. It re-captured nine historical JWT-bearing records from four hosts
through the production `SnapshotStore` and produced seven replay fixtures.
Capture and replay output contain zero JWT, Google-browser-key or AWS-access-key
shapes and zero privacy exclusions.

Integrated `.278` release gates passed:

- 2,846 tests, 4 skipped;
- provider benchmark 25/25;
- resolver benchmark 6/6;
- architecture validation 48 adapters / 0 issues;
- tracked credential-shape scan: zero;
- `git diff --check`: passed.

## Completion Matrix

| Requirement | Authoritative evidence | Status |
| --- | --- | --- |
| Fresh100 has an evidence-backed terminal for all 100 records | `.277` has 51 unresolved records | **Not met** |
| Every eligible Fresh100 record is S7 Exact | Complete eligibility labels do not exist | **Missing** |
| Closed, absent, blocked and ambiguous inputs have reproducible typed terminals | 19 Verified No Match and one External Blocked are proven; 51 remain unresolved | **Partial** |
| Fresh100 `SYSTEM_GAP=0` | Five-class completion cannot be reported while 51 records remain unresolved | **Not met** |
| Publication safety has zero wrong URL/location/company/brand/tenant | Proven for all 29 published `.277` Exact results | **Partial cohort proof** |
| Fresh100 cold live completes 100/100 from empty state | Proven for code-frozen `.277` | **Proven at `.277`** |
| Same-version Fresh100 replay is strict 100/100 | `.277` is 98 reproduced / 1 budget recovery / 1 mismatch | **Not met** |
| Frozen100 preserves its historical 69 Exact on the current version | No `.278` Frozen100 live/replay exists | **Missing** |
| Offline release gates pass | `.278` full suite and all offline benchmarks passed | **Proven** |
| Snapshot JWT values are sanitized before hashing | Four-host `.278` focused corpus passed | **Proven for focused contract** |
| Trace/checkpoint/completion outputs are independently privacy-safe | Crawford Thomas raw extracted-URL serialization remains a one-company residual | **Not met** |
| Full release capsule is unmodified, privacy-clean and replayable | Raw `.277` capsule is not shareable; `.278` has no full live capsule | **Missing** |
| Two unseen cohorts meet recall, precision, safety and replay gates | Accepted unseen cohorts: 0/2; sealed v2/v3 remain unopened | **Not met** |
| LLM direction remains isolated pending explicit user approval | No LLM code was integrated into `main` | **Proven** |

## Causal Cluster Audit

Two independent read-only reviews rechecked the `.277` causal ledger, raw trace
and full replay. No unresolved cluster meets all four implementation gates:

1. one observable trigger;
2. one production code path;
3. at least three independent companies;
4. evidence-based expected terminal recovery of at least three.

The company-count candidates are:

| Candidate | Companies | Expected recoveries | Decision |
| --- | ---: | ---: | --- |
| `search_results_filtered_to_zero` | 25 | 0 proven | Reject: CareerSurface and ProviderSearch are different sources, and final causes span timeout, forbidden, identity, no-public-opening and no-board |
| `eligible_board_portfolio_incomplete` | 5 | 0 proven | Reject: four generic paths and one Paylocity path replay unchanged |
| `transport_dispatch_budget_exhausted` | 3 | 0 proven | Reject: no captured first-party candidate or recovered terminal |

The remaining form, pagination, integration, provider, identity, transport and
replay signatures affect one or two independent companies each. Brown and
Caldwell is a one-terminal replay mismatch; Diamondback changes only from one
failure terminal to another. Crawford Thomas is a one-company privacy
serialization residual.

No new heuristic, provider, scheduler, replay or serialization behavior is
authorized from existing evidence.

## Stop And Next Gates

The release-stop requirement is satisfied: `.278` code, tests, reports and
governance changes are grouped, committed and pushed. Do not accumulate another
behavior batch from the current observed cohort.

No remaining offline action can prove the product goal. The next meaningful
steps require explicit live authorization and must remain serial:

1. run a code-frozen `.278` Fresh100 cold measurement from entirely new roots,
   then strict replay and full-capsule privacy validation;
2. only after that gate is accepted, run same-version Frozen100 live/replay and
   verify all historical 69 Exact records;
3. only after both development gates close, consume sealed v2/v3 through
   `docs/BLIND_HOLDOUT_PROTOCOL.md`, including independent signed human review.

Until authorized, sealed v2/v3 remain unopened, and the LLM and plugin branches
remain isolated. The product goal must stay active.
