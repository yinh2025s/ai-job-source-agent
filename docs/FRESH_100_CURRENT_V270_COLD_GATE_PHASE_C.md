# Fresh100 Current `.270` Cold Gate Phase C

Date: 2026-07-28
Code commit: `8ae36ef4`
Product adapter: `2026-07-27.270`
Decision: **live accepted for measurement; replay gate failed; no repair selected**

## Scope

This is a current-version cold regression of the existing Fresh100 development
cohort. It is not a new cohort, blind holdout or replacement for the immutable
`.188` and `.209` historical reports.

The run used the tracked
`samples/evaluation/live100_fresh_cohort_20260718.json` input. Its SHA-256 was
`fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`.
The input contained 100 records and 100 unique LinkedIn job IDs and was
byte-identical to the preserved release copy.

All checkpoint, completion, evidence, snapshot, failure and replay roots were
new. The first live line reported 100 pending records, zero restored
completions and zero retryable resubmissions. Code remained frozen throughout
the benchmark and replay analysis. The authenticated plugin and LLM experiment
were not used or inspected.

## Live Result

The cold run completed all 100 records:

| Metric | Current `.270` | Historical `.209` | Delta |
| --- | ---: | ---: | ---: |
| Verified Website | 90 | 78 | +12 |
| Career page | 78 | 60 | +18 |
| Verified Job List | 73 | 56 | +17 |
| S7 Exact opening | 32 | 19 | +13 |
| Pipeline partial | 48 | 38 | +10 |
| Pipeline failed | 20 | 43 | -23 |

Top-level application status was 72 success, 22 partial and 6 failed. That
status is not the Exact metric; the S7 publication result is 32 Exact, 48
partial and 20 failed.

The runtime terminal taxonomy was:

| Terminal | Count |
| --- | ---: |
| Exact opening | 32 |
| Verified no match | 18 |
| External blocked | 7 |
| No public openings | 3 |
| Discovery unresolved | 26 |
| Retryable failure | 7 |
| Unsupported capability | 1 |
| Other non-success | 6 |

There are no final evaluation annotations for this current run. Eligible Exact
recall and the official five-class `SYSTEM_GAP` total therefore remain
unreportable; runtime taxonomy must not be presented as manual ground truth.

Compared with `.209`, 16 of the previous 19 Exact records remained Exact, three
regressed in this cold network run, and 16 new Exact records were gained. The
three lost records were TreeHouse Foods, BWXT and Salas O'Brien. The net change
was +13 Exact.

## Exact Safety Audit

All 32 Exact records were inspected against result identity, trace lineage and
the captured official provider or first-party opening evidence:

| Safety check | Result |
| --- | ---: |
| Wrong opening URL | 0 |
| Wrong location | 0 |
| Cross-company publication | 0 |
| Cross-tenant publication | 0 |
| Exact without artifact evidence | 0 |
| Missing or hash-invalid Exact snapshot | 0 |

There were 31 unique opening URLs because two Arkema LinkedIn records safely
mapped to the same verified opening. All 32 identity assertions were verified.
Location evidence was 19 exact, 10 overlap, two region and one URL qualifier.

This audit proves publication safety at live capture time. It does not prove
that every job is still open today or that recall meets the product goal.

## Replay Result

The automatic 100-record same-version replay did not pass:

- B&D Industries, Human Resources Manager left one scoped GET unconsumed;
- the first remaining request was `GET https://www.banddindustries.com`;
- the bundle exited non-zero.

A read-only 99-record control excluding that record completed with:

- 96 reproduced;
- two outcome mismatches;
- one allowed company-budget recovery;
- zero fixture gaps.

The two outcome mismatches were:

- Target Hospitality: Exact to `OPENING_LOCATION_MISMATCH`;
- Brown and Caldwell: Exact to `INVALID_STRUCTURED_DATA`.

A separate nine-record tail replay after the B&D record reproduced 9/9. The
failure is therefore not an unbounded loss of all later tape entries.

The full acceptance requirement remains unmet:

```text
100/100 replay
0 mismatch
0 fixture gap
0 tape divergence
0 missing snapshot boundary
```

## Causal Clusters

Phase A split the replay failure by executable cause rather than by failed
stage:

| Cluster | Shared trigger | Companies | Outcome recovery | Decision |
| --- | --- | ---: | ---: | --- |
| Redirect-alias producer reconstruction | Shared evidence stores `www`, captured request redirects to apex, strict replay identity does not restore the producer input | 1 | 1 | Below threshold |
| UltiPro structured-State semantic drift | Snapshot sanitizer redacts public `Address.State` objects, so replay location differs from live | 3 | 2 | Trigger threshold met; recovery threshold not met |
| UltiPro duplicate pagination ID | Brown pages two and three repeat one opportunity ID, making inventory incomplete | 1 | 0 Exact recovery | Below threshold |

The structured-State defect occurs in Target Hospitality, Brown and Caldwell
and ARUP Laboratories. It changes the terminal outcome for the first two; ARUP
remains partial. It is a real evidence-fidelity defect, but the repository rule
requires the same trigger, code path and at least three expected recoveries
before implementation. It therefore does not enter Phase B in this cycle.

B&D is a separate replay producer-state reconstruction defect. Any future
repair must require the scoped tape to prove the exact request-to-final
redirect, preserve HTTPS and registrable-domain continuity, and must not
globally equate `www` and apex hosts.

Brown's final `INVALID_STRUCTURED_DATA` is also not a third cluster member. The
duplicate opportunity ID already existed in live inventory; the State
redaction only prevented replay from selecting the correct candidate before
that existing partial error became terminal.

## Decision

No behavior code is modified from these artifacts:

- no cluster has at least three expected terminal recoveries;
- no company, domain, job ID or benchmark exception is added;
- S7 location, company and tenant gates remain strict;
- old snapshots remain immutable;
- no new live cohort or Frozen100 run starts while Fresh replay acceptance is
  failing.

The Frozen100 current-version no-regression gate and two unseen-cohort product
gates remain pending. The product goal remains open.

## Immutable Artifacts

The complete live, automatic replay, control replay and focused diagnostic
artifacts are preserved as:

`artifacts/releases/fresh100-current-v270-cold-20260728-run1.tar.zst`

SHA-256:

`214c8e6a044f6f64e2c2c9b5a1c48b381dbac78b8464e0539ca0c6152cd87d50`

The tracked checksum is
`artifacts/releases/fresh100-current-v270-cold-20260728-run1.tar.zst.sha256`.
The archive payload is intentionally ignored by Git; the checksum and this
report are reviewable and versioned.

## Gate Status

No product code changed after the accepted v273 release gate at this same
commit lineage:

- 2,834 tests passed, 4 skipped;
- provider benchmark 25/25;
- resolver benchmark 6/6;
- architecture validation 48/0;
- Exact safety audit passed.

This Phase C adds measurement artifacts and governance only. Re-running the
2,834-test suite for a documentation-only commit would not add behavioral
evidence. The final repository gate for this group is clean Markdown structure,
checksum verification and `git diff --check`.
