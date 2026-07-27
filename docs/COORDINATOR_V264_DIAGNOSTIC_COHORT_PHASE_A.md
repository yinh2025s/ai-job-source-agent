# v264 Backend Diagnostic Cohort - Phase A

## Purpose

Run another backend-only development cohort on frozen `.261` code to seek the
third independent example for open parser, relationship, inventory and
transport contracts.

Plugin work, authenticated External Apply, coordinator-v2, the LLM branch and
sealed holdouts remain outside this run.

## Frozen Input

Input:
`/private/tmp/v264-diagnostic-input.json`

- SHA-256:
  `827bd1f1c89f8dc5cfd34cdac77a2db7e1a8ae5c4b4763bf8f39e6b756a77ca3`;
- records: 30;
- independent companies: 30;
- unique LinkedIn job IDs: 30;
- prior diagnostic company-name overlap: 0;
- prior diagnostic LinkedIn job-ID overlap: 0;
- six public-search role families with five records each:
  - Backend Engineer;
  - Business Analyst;
  - Financial Analyst;
  - Human Resources Manager;
  - Marketing Coordinator;
  - Quality Engineer.

The records are unused S1-only members of the v252, v254, v256, v257 and v260
public pools. Selection excludes every prior diagnostic job ID and company
name, takes the first record per company and applies fixed role quotas. No
S2-S7 result, provider or terminal influenced selection.

Manifest:
`/private/tmp/v264-diagnostic-input-manifest.json`

It records `sealed_holdouts_read=false`.

## Frozen Execution

- adapter version: `2026-07-27.261`;
- candidate discovery engine: `stage_v1`;
- isolated roots below `/private/tmp/v264-diagnostic-run1`;
- no checkpoint, completion, evidence, snapshot or replay reuse;
- serial 30-company live capture;
- no product-code change during live or replay.

## Acceptance

1. Audit every Exact for the complete S7 identity chain and canonical URL.
2. Replay all 30 records with zero mismatch, fixture gap, extra request and
   unconsumed tape.
3. Merge evidence with prior development cohorts only by identical trigger and
   production code path.
4. Require expected recovery of at least three independent companies before
   implementation.
5. Preserve zero wrong URL, cross-company, cross-tenant and wrong-location
   publication.
