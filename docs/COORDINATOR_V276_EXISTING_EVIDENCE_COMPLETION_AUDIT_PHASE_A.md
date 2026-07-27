# v276 Existing-Evidence Completion Audit - Phase A

Date: 2026-07-28
Product adapter: unchanged at `2026-07-28.275`
Decision: **no Phase B implementation selected**

## Scope

This audit follows the v273 release stop rule. It uses only:

- the existing Fresh100 `.270` cold trace;
- reviewed focused terminal evidence already tracked by the repository;
- the `.275` GovernmentJobs focused live and replay report.

It does not open a new cohort, run a new live benchmark, inspect sealed
holdouts, modify product behavior or use the isolated LLM branch.

## Projection Ledger Correction

Rebuilding the causal ledger from the `.270` trace exposed stale arithmetic in
the development projection manifest. The old expected counts described the
historical `.244` projection, not the newer `.270` cold artifact.

The `.270` trace by itself produces:

| Durable state | Records |
| --- | ---: |
| Exact | 32 |
| Verified No Match | 18 |
| External Blocked | 1 |
| Unresolved | 49 |

Applying the existing reviewed terminal allowlist changes six records:

- Salas O'Brien, BWXT, Cintas and NYC DSS: unresolved to Exact;
- Sunbird Software and Milwaukee Tool: Verified No Match to Exact.

That yields 38 Exact, 16 Verified No Match, one External Blocked and 45
unresolved. The `.275` focused gate then provides current-version,
complete-inventory `OPENING_NOT_FOUND` evidence for two Fresh100 records:

- City of College Station, LinkedIn job `4426127673`;
- City of Lubbock, LinkedIn job `4432727055`.

The conservative development projection is therefore:

| Durable state | Projected records |
| --- | ---: |
| Exact | 38 |
| Verified No Match | 18 |
| External Blocked | 1 |
| Unresolved | 43 |

This is a reviewed evidence projection, not a replacement for the `.270`
32/100 raw Exact score and not an official same-run Fresh100 evaluation.

## Causal Cluster Review

The 43 unresolved records were reviewed by executable trigger and production
code path, not by failed stage label. Two broad ledger signatures contain at
least three companies:

1. provider-search results filtered to zero;
2. eligible board portfolio incomplete.

Neither is qualified for implementation. The first combines different missing
candidate and identity causes and has no reviewed batch recovery expectation.
The second combines different providers and portfolio states.

The S6-S7 review found no provider-family cluster with the same trigger, same
code path and at least three evidence-supported Exact recoveries. Seven generic
single-page inventories share an incomplete stop, but none contains a positive
target-opening candidate. Four location-evidence cases support at most one or
two likely recoveries. Native-provider and S7 failures split across different
providers, tenants and identity paths.

The S2-S5 review likewise found no implementation-ready cluster under the
three-company and three-recovery rule. Its closest candidates were:

| Near cluster | Companies | Evidence-backed recovery expectation |
| --- | ---: | ---: |
| official host repeatedly returns 403 | 6 | 0; no correct downstream candidate |
| strong first-party Career candidate not published | 3 | 0 terminal recoveries; only two candidates are credible |
| eligible Career action does not become a Job List | 3 | 1 Job List |
| resolver produces no authoritative Website | 3 | 0 |

The strongest follow-up direction is the Career verifier path shared by Caesars
Entertainment and Splashlight. Nisga'a Tek reaches the same stage label but its
candidate is an error page, so it cannot be counted as a third expected
recovery. Stage labels and search-result absence are not treated as causal
equivalence.

## Decision

No behavior implementation is legal from the available evidence:

- no company, domain or job-ID exception is added;
- no identity, location, provider or tenant gate is relaxed;
- no new live or benchmark batch is started;
- sealed holdouts and the LLM branch remain untouched.

The next behavior phase remains blocked on new independent evidence that
qualifies one common trigger and code path with at least three expected
recoveries. Repository work in this cycle is limited to correcting the
projection ledger, documenting the rejected clusters, running focused
governance checks, and committing and pushing the result.
