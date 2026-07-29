# Fresh100 `.283` Current Cold Gate Report

Date: 2026-07-29

Decision: **measurement completed; release gate failed**

## Frozen Inputs

- commit: `d76cfddc42dc4428385afd0b6904780b6304c49a`
- adapter version: `2026-07-29.283`
- runtime: CPython `3.12.6`
- input:
  `samples/evaluation/live100_fresh_cohort_20260718.json`
- input SHA-256:
  `fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`
- valid run root:
  `/private/tmp/fresh100-v283-cold-20260729-run2`
- resume mode: disabled
- checkpoint, completion, evidence, snapshot and output roots: new and isolated

An earlier startup under `run1` omitted the explicit `--limit 100`, so the CLI
selected its 30-record default. It was stopped immediately and is invalid. No
record, score or artifact from `run1` is included below.

The valid run used `--limit 100 --require-full-cohort`, completed all 100
records, and kept the code frozen through live and strict replay. It did not
access sealed cohorts, Frozen100, the plugin, coordinator-v2 or the isolated LLM
branch.

## Live Measurement

| Metric | `.281` | `.283` | Delta |
| --- | ---: | ---: | ---: |
| Website | 93 | 92 | -1 |
| Career page | 78 | 79 | +1 |
| verified Job List | 71 | 71 | 0 |
| raw S7 Exact | 34 | 36 | +2 |
| pipeline success | 34 | 36 | +2 |
| pipeline partial | 46 | 44 | -2 |
| pipeline failed | 20 | 20 | 0 |

The live run took 1,411.8 seconds. Its terminal report was:

| Reported terminal | Count |
| --- | ---: |
| Exact opening | 36 |
| verified no-match | 19 |
| external blocked | 6 |
| no public openings | 2 |
| discovery unresolved | 26 |
| retryable failure | 9 |
| other non-success | 2 |

These are pipeline reports, not ground-truth annotations. This development
cohort still has zero terminal annotations, so eligible Exact recall and formal
Exact precision remain not reportable.

## Exact Safety Audit

All 36 published URLs are public HTTPS URLs with no credentials, nonstandard
ports or fragments. Serialized opening evidence found no wrong location,
cross-company or cross-tenant publication. Workday's official response confirms
that Diamondback Energy opening `R100757` is titled `Cybersecurity Analyst` in
`Oklahoma City, OK`; its stale URL slug contains `Systems-Administrator` but is
not the authoritative title.

The 36 raw Exact outputs are not all release-clean:

- 34 have an artifact-complete identity path.
- Slant CRM and Team Royal reached valid-looking provider openings, but S2
  failed and S3 did not run. S7 nevertheless promoted provisional navigation
  evidence to `verified`. These two records must not be represented as fully
  unambiguous Exact until the identity contract is resolved.
- Brown and Caldwell is evidence-correct in live, but is not deterministic in
  strict replay.
- BWXT and both Arkema source records preserve safe opening identity, but the
  serialized output Job List differs from the identity assertion's canonical
  board route.
- Two distinct Arkema LinkedIn job IDs resolve to the same official opening.
  This is a source duplicate, not a cross-tenant error.

The conservative artifact statement is therefore **36 raw Exact, 34
artifact-clean Exact claims, two provisional identity claims, and one
replay-unstable live Exact**. This is not a formal precision denominator.

## Strict Replay

Replay record integrity passed:

- source, selected, exported, result, trace and comparison records: 100/100;
- replayability drops: 0;
- fixture gaps: 0;
- scoped outcome tape: 646 scopes and 2,842 outcomes.

The outcome gate failed:

| Classification | Count |
| --- | ---: |
| reproduced | 99 |
| mismatch | 1 |
| fixture gap | 0 |
| expected transition | 0 |
| budget recovery | 0 |

Brown and Caldwell changed from an UltiPro Exact to
`OPENING_NOT_FOUND`. Live parsed `Wailuku, Hawaii` from
`Address.City` plus nested `Address.State.Name`. Snapshot sanitization treated
the object-valued `State` field as sensitive state and replaced it with
`[REDACTED]`; replay then fell back to the less precise public description
`Maui, HI`, which the strict location gate correctly rejected.

The same sanitizer trigger is present for Target Hospitality, Brown and
Caldwell, and Salas O'Brien, but only Brown changes terminal outcome. It does
not meet the project rule requiring three expected terminal recoveries, so no
code change is made in this measurement phase.

NDIT and ARUP have recorded outer company-budget projections. Their underlying
latency-free replay reaches `CAREER_PAGE_NOT_FOUND`, then correctly projects
back to each live `COMPANY_TIME_BUDGET_EXHAUSTED` boundary. Both are reproduced,
not hidden replay mismatches.

## Causal Classification Of 64 Non-Exact Records

| Executable causal class | Count | Assessment |
| --- | ---: | --- |
| Complete/title-filtered official inventory has no match | 17 | Evidence terminal |
| No public openings | 2 | Evidence terminal |
| Verified provider externally blocked | 1 | Evidence terminal |
| Blocking observed before hiring/provider relationship verification | 5 | Incomplete evidence |
| Identity rejected | 3 | Correct fail-closed behavior |
| Correct candidate exists but transport failed | 1 | State of Montana TLS singleton |
| Budget starvation | 11 | Multiple underlying causes and paths |
| Correct candidate not produced | 22 | Multiple inventory/search contracts |
| Inventory integration not recognized | 2 | Two unrelated implementations |

The 17 verified no-match records are Wolfe, Matlen Silver, City of Lubbock, SDS
International, Sunbird Software, IGNITE, Target Hospitality, Mayo Clinic, PACS,
Milwaukee Tool, Dechert, Steampunk, QXO, Rider Levett Bucknall, Adapture, Jushi
and Cintas. Pitch Aeronautics and Prophetic have no public openings. Altec has a
verified first-party provider handoff followed by provider 403.

Sunwest Bank, City of Pharr, Benefis, City of Sioux Falls and Ken Garff are
blocked before a provider relationship is verified, so the six reported
`external_blocked` records do not all have the same evidence quality. STRIKE,
Focus and Aramark remain safely rejected for identity discontinuity. The
summary currently counts STRIKE and Focus as verified no-match because it reads
the S6 inventory disposition before the S7 rejection; governance reporting must
classify them as identity rejected.

The apparently large unresolved labels do not qualify as implementation
clusters:

- 19 records across 18 companies share
  `search_results_filtered_to_zero`, but the captured search results contain no
  evidenced correct candidate and therefore predict zero recoveries.
- Four Career transport-dispatch exhaustions share a budget controller but have
  different 404, fetch and no-candidate roots.
- Three opening-stage company-budget exhaustions have no evidence that all
  three possess a recoverable correct inventory.

No proposed change currently has one observable trigger, one production code
path and at least three expected evidence-terminal recoveries. No heuristic,
budget increase or identity relaxation is justified by this run.

## GovernmentJobs Regression Review

- City of College Station moved from `.281` timeout to Exact.
- City of Lubbock moved from `.281` timeout to complete official no-match.
- WICHITA COMPANY LIMITED did not exercise GovernmentJobs in this full run.
  LinkedIn supplied `wichita.co.uk`; that candidate led to Regal Rexnord's
  generic career system. This is upstream official-site candidate drift, not a
  GovernmentJobs adapter regression. The focused GovernmentJobs evidence still
  correctly rejects the City of Wichita tenant as a different employer.

The `.283` provider implementation is therefore retained, but the focused 3/3
closure must not be described as 3/3 full-cohort route coverage.

## Privacy And Artifact Integrity

Artifact SHA-256:

| Artifact | SHA-256 |
| --- | --- |
| `results.json` | `b4f59cd1c523af5224db09fa9e06390ffd62098b7348688b1a65e0725ceac151` |
| `trace.json` | `4b34859e71e9a704507cf9a125d858dbb11925f612a49904500ee4994a2cddd0` |
| `summary.json` | `d439991c9b84673a1008d92fa03779b80070995762e2dfb588a79625a1bb4dd3` |
| replay manifest | `abf3c72a67dca03dc0d1370af50794e20af0f833688176afcfea88e2427d3e92` |

The raw capsule is not shareable. A credential-shape scan found one public
Google browser-key-shaped value serialized in the trace, one S5 checkpoint and
one completion record. The value is not reproduced here, and no release archive
is created.

## Decision

The `.283` code remains frozen and accepted for its focused GovernmentJobs
contract, but this Fresh100 measurement does not pass the release gate:

- strict replay is 99/100 rather than 100/100;
- two raw Exact records rely on provisional identity after S2/S3 failure;
- Frozen100 no-regression remains open;
- formal eligible recall and precision remain unreportable;
- no new legal three-company/three-recovery implementation cluster is proven.

No new live batch starts from this report. The next Phase A must first obtain
non-sealed, record-level evidence for a genuinely shared trigger, or explicitly
change the recovery-threshold governance. It must not convert the current stage
labels into a heuristic backlog.
