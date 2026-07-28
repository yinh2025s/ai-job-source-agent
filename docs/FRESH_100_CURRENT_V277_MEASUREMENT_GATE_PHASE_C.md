# Fresh100 Current `.277` Measurement Gate - Phase C

Date: 2026-07-28
Product adapter during live and replay: `2026-07-28.277`
Input cohort: `samples/evaluation/live100_fresh_cohort_20260718.json`
Cohort SHA-256: `fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`
Decision: **measurement accepted; replay and raw-artifact privacy gates failed**

## Frozen Run

The existing 100-record Fresh100 development cohort was run once from new
checkpoint, completion, evidence, snapshot, failure-bundle and replay roots.
No prior `.188` or `.275` cache/completion was restored. Code remained frozen
through live and replay; sealed cohorts, the plugin and the LLM branch remained
closed.

The run used the stage-v1 coordinator, legacy search backend, four bounded
company workers, a 120-second company budget, 25-second Website budget,
8-second fetch timeout, one retry and six verification requests.

## Live Result

| Metric | `.277` |
| --- | ---: |
| Records completed | 100/100 |
| Website | 87 |
| Career | 73 |
| Verified Job List | 69 |
| S7 Exact | 29 |
| Pipeline success / partial / failed | 29 / 48 / 23 |
| Elapsed | 944 seconds |

The raw causal ledger is:

| Terminal class | Count |
| --- | ---: |
| Exact | 29 |
| Evidence-backed Verified No Match | 19 |
| External Blocked | 1 |
| Unresolved | 51 |

This is the authoritative `.277` raw measurement. Existing focused evidence
must not be merged into the 29/100 score.

Compared with `.275`, IMG became Exact while Frost, Versana DevOps and ProMach
lost Exact. Milwaukee Tool and NDIT also regressed from verified no-match to
network failures. The net Exact change is 31 to 29.

## Exact Safety Audit

All 29 published openings were reviewed against their final identity
assertions:

- 29/29 identity verdicts are verified with no failure code;
- 29/29 selected titles match the requested title contract;
- 29/29 provider, tenant and canonical-board chains are continuous;
- location evidence is 17 exact, 9 overlap, 2 regional and 1 explicit URL
  qualifier;
- wrong opening URL, wrong company, wrong tenant and wrong location: 0.

Six displayed URLs differ from the asserted canonical URL only by a trailing
slash. The two Arkema records intentionally resolve to the same Beaumont
opening. Team Royal and Resolute Road Hospitality retain first-party hiring
handoff evidence.

Manual eligibility labels are not present for all 100 records, so Exact
precision and eligible Exact recall are not reported as cohort truth metrics.
The 29/29 safety audit is a publication-integrity result, not an eligibility
denominator.

## Full Replay

| Replay metric | Result |
| --- | ---: |
| Source / selected / exported / replayed / compared | 100 / 100 / 100 / 100 / 100 |
| Reproduced | 98 |
| Budget recovery | 1 |
| Mismatch | 1 |
| Fixture gap | 0 |
| Outcome gate | failed |

Diamondback Energy changed from live `COMPANY_TIME_BUDGET_EXHAUSTED` to replay
`CAREER_PAGE_NOT_FOUND`, an allowed budget recovery. Brown and Caldwell changed
from Exact to `INVALID_STRUCTURED_DATA`: the public UltiPro nested
`State.Name=Hawaii` was over-redacted, replay fell back to `Maui, HI`, and the
location gate correctly rejected the candidate.

The Brown trigger occurs in three UltiPro records but changes only one terminal,
so its expected recovery is one. It does not qualify for implementation under
the three-company/three-recovery rule.

The 71-record failure bundle completed with 70 reproduced, one budget recovery,
zero mismatch and zero fixture gap.

## Causal Review

The apparent seven-record S2 network group splits into separate production
paths:

- LinkedIn-company fetch: four companies, only two demonstrated terminal
  recoveries;
- homepage verification: two companies;
- search transport: one company.

Other stage-level groups likewise mix different triggers or lack three proven
terminal recoveries. No transport, discovery, provider, identity or replay
cluster qualifies for behavior implementation. This run therefore does not add
another heuristic, provider or company exception.

## Artifact Privacy

Immutable page blobs, byte counts, request identities, sequence continuity and
snapshot-store identity passed. The mutable `sites/` aliases are convenience
views and are not the authoritative content-addressed blobs.

The raw `.277` capsule is not shareable:

- one Crawford Thomas Google browser-key shape remains in trace, S5 checkpoint
  and completion serialization;
- JWT-shaped capability values remain in page/replay bodies for TreeHouse
  Foods, Tyler Technologies, Pitch Aeronautics and QXO.

The four-host JWT cluster shares one trigger and one
`sanitize_snapshot_body -> SnapshotStore -> replay` path, so it qualified for
the bounded `.278` privacy phase. Crawford remains a separate one-company
serialization residual.

## Decision

Keep the `.277` live result as immutable historical measurement evidence. Do
not archive or publish its raw capsule. Accept the 29/100 raw Exact result and
29/29 identity safety result, but keep the product goal open because replay is
98/100 and the raw artifact is not privacy-clean.

After `.278` changes capture behavior, another full live benchmark requires a
new explicit measurement authorization. Focused `.278` sanitation evidence
cannot rewrite this `.277` score.

Local evidence root:

`/private/tmp/fresh100-current-v277-cold-20260728-run1`
