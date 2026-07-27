# v263 Backend Diagnostic Cohort - Phase A

## Purpose

Run another backend-only development cohort on frozen `.261` code to collect
generic recall and replay defects that satisfy the three-company implementation
gate.

Plugin work, authenticated External Apply, coordinator-v2, the LLM branch and
sealed holdouts remain outside this run.

## Frozen Input

Input:
`/private/tmp/v263-diagnostic-input.json`

- SHA-256:
  `17825f2cd49df13cbb240bc679c931059b7e6aad173df4ad336af4d88f56d23f`;
- records: 30;
- independent companies: 30;
- unique LinkedIn job IDs: 30;
- overlap with all prior diagnostic input job IDs: 0;
- role-family composition:
  - Data Analyst: 5;
  - Mechanical Engineer: 9;
  - Nurse Practitioner: 3;
  - Recruiter: 5;
  - Sales Engineer: 8.

The records are the remaining unused members of the v260 S1-only public pool.
Selection excludes every prior diagnostic LinkedIn job ID, takes the first
record per company and then takes the first 30 companies in deterministic
company ordering. No S2-S7 output, provider or terminal influenced selection.

Manifest:
`/private/tmp/v263-diagnostic-input-manifest.json`

It records `sealed_holdouts_read=false`.

## Frozen Execution

- adapter version: `2026-07-27.261`;
- candidate discovery engine: `stage_v1`;
- isolated roots below `/private/tmp/v263-diagnostic-run1`;
- no checkpoint, completion, evidence, snapshot or replay reuse;
- serial 30-company live capture;
- no product-code change during live or replay.

## Acceptance

1. Audit every Exact for company, hiring entity, provider, tenant, title,
   location, current state and canonical opening URL.
2. Export and replay all 30 records with zero mismatch, fixture gap, extra
   request and unconsumed tape.
3. Classify every non-Exact by observable trigger and common production code
   path, not by terminal stage.
4. Require at least three independent companies and expected recovery of at
   least three records before Phase B.
5. Preserve zero wrong URL, cross-company, cross-tenant and wrong-location
   publication.
