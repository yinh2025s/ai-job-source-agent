# Backend Release Completion Audit After `.277`

Date: 2026-07-28
Audited release commit: `6a71ed16a8337dcd66ab551bbf66330f2e25d929`
Product adapter: `2026-07-28.277`
Decision: **release clean and pushed; product goal remains open**

## Scope

This audit supersedes the goal-status portion of
`docs/BACKEND_RELEASE_COMPLETION_AUDIT_V273.md` without rewriting the immutable
v273 evidence. It incorporates the existing Fresh100 `.275` cold run and the
offline `.277` snapshot privacy closure.

No new cohort, live request, sealed holdout, authenticated plugin run or LLM
branch was opened. The worktree was clean and `main` matched `origin/main` at
the audited commit.

## Current Evidence

Fresh100 `.275` completed 100/100 cold live records from new roots:

- 91 verified Websites;
- 77 Career pages;
- 72 verified Job Lists;
- 31 S7 Exact openings;
- zero wrong opening URL, title, location, company or tenant among the 31
  Exact results.

The raw causal ledger is 31 Exact, 21 Verified No Match, one External Blocked
and 47 unresolved. The separately reviewed development projection is 38 Exact,
19 Verified No Match, one External Blocked and 42 unresolved. It is not a
replacement for the raw score or a complete eligibility ledger.

The `.275` replay gate failed at 97 reproduced, two budget recoveries, one
mismatch and zero fixture gaps. `.277` changes snapshot credential sanitation,
not discovery or publication behavior, and created no complete live/replay
capsule.

The `.277` offline release gates passed:

- 2,841 tests passed, 4 skipped;
- provider benchmark 25/25;
- resolver benchmark 6/6;
- architecture validation 48 adapters / 0 issues;
- tracked credential-shape scan: zero matches;
- `git diff --check`: passed.

The focused `.277` corpus re-captured 18 records across 10 scopes and six hosts
through the production `SnapshotStore`. Capture and replay conversion retained
zero Google/AWS credential-shaped values. This proves the selected
snapshot-body contract, not end-to-end artifact privacy.

## Completion Audit

| Requirement | Current evidence | Status |
| --- | --- | --- |
| Fresh100 has an evidence-backed terminal for all 100 records | Reviewed projection still has 42 unresolved records | **Not met** |
| Every eligible Fresh100 record is S7 Exact | Complete eligibility annotations do not exist | **Missing** |
| Fresh100 `SYSTEM_GAP=0` | Official five-class count is unreportable while 42 records remain unresolved | **Not met** |
| Fresh100 publication safety is zero-error | All 31 `.275` Exact results passed the recorded safety audit | **Proven for published `.275` Exact results** |
| Current-version Fresh100 cold live is 100/100 from empty state | `.275` completed; current adapter is `.277` | **Missing at current version** |
| Current-version Fresh100 replay is strict 100/100 | `.275` replay was 97/100 with one mismatch and two budget recoveries | **Not met** |
| Frozen100 preserves the historical 69 Exact on the current version | `.188` is immutable and valid; no `.277` regression exists | **Missing** |
| Offline release gates pass | `.277` full suite and all three offline benchmarks passed | **Proven** |
| Snapshot values are sanitized before hashing | Six-host `.277` focused corpus passed | **Proven for focused contract** |
| Trace/checkpoint/completion outputs are independently privacy-safe | Crawford Thomas raw extracted-URL serialization remains a one-company residual | **Not met** |
| Release capsule is unmodified, privacy-clean and replayable | `.275` archive is audit-only; `.277` has no full live capsule | **Missing** |
| Two unseen cohorts meet the acceptance contract | Accepted cohorts: 0/2; sealed v2/v3 remain unopened | **Not met** |
| LLM direction remains isolated | No LLM code was inspected or integrated | **Proven** |

## Causal Cluster Decision

Three independent read-only reviews rechecked the `.275` live ledger, replay
defects and `.277` impact. No remaining candidate meets all required
implementation conditions:

1. at least three independent companies;
2. one observable trigger;
3. one production code path;
4. at least three expected terminal recoveries.

The large `search_results_filtered_to_zero` and portfolio-incomplete labels mix
different causal paths. Interactive ambiguous GET affects two companies.
Caller-deadline recovery affects two companies and restores no terminal.
UltiPro structured-State drift has only one current expected recovery.
Crawford Thomas trace serialization is a one-company residual.

Therefore no further product, provider, scheduler, replay or serialization
change is authorized from existing evidence. A shared stage label is not a
failure cluster.

## Stop And Next Gate

The v273 instruction remains in force: do not start another cohort or
accumulate uncommitted behavior changes. The backend release is clean,
grouped, committed and pushed.

Meaningful product validation now requires an explicit measurement decision:

1. authorize a current-version Fresh100 cold live plus strict replay using new
   isolated roots and an unmodified privacy-clean capsule;
2. if that gate is accepted, authorize the same-version Frozen100 no-regression
   live/replay;
3. only after development and Frozen gates close, consume sealed holdouts under
   `docs/BLIND_HOLDOUT_PROTOCOL.md`.

Until that decision, sealed v2/v3 remain unopened, the LLM and plugin branches
remain isolated, and the product goal cannot be marked complete.

