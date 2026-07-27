# v260 Development Diagnostic Cohort - Phase A

## Purpose

Collect an eleventh backend-only development cohort on frozen `.259` code.
The run seeks a generic provider-family, inventory, transport, identity,
portfolio or replay defect shared by at least three independent companies.
Stage labels and terminal reason counts are diagnostic only.

Plugin work, authenticated External Apply, coordinator-v2, the LLM branch and
sealed holdouts remain outside this run.

## Frozen Input

Input:
`/private/tmp/v260-diagnostic-input.json`

- SHA-256:
  `f12220acbc6fa6eaa5f10b02c4f0b352b88b64e9eabd362b6454526b3165f331`;
- records: 30;
- independent companies: 30;
- unique LinkedIn job IDs: 30;
- overlap with 581 known public development job IDs: 0;
- public-search role families:
  - Operations Manager: 5;
  - Data Analyst: 5;
  - Sales Engineer: 5;
  - Recruiter: 5;
  - Mechanical Engineer: 5;
  - Nurse Practitioner: 5.

Selection preserves public-search order after prior-ID, duplicate-company and
role-quota filtering. ATS, Website, Career, provider and expected terminal did
not influence selection. External Apply remains unknown.

Manifest:
`/private/tmp/v260-diagnostic-input-manifest.json`

It records `sealed_holdouts_read=false`.

## Frozen Execution

- adapter version: `2026-07-27.259`;
- candidate discovery engine: `stage_v1`;
- isolated roots below `/private/tmp/v260-diagnostic-run1`;
- no checkpoint, completion, evidence, snapshot or replay reuse;
- serial 30-company live capture;
- no product-code change during live or replay.

## Acceptance

1. Audit every Exact for company, hiring entity, provider, tenant, title,
   location, current state and canonical opening URL.
2. Export and replay all 30 records; report every mismatch and integrity gap.
3. Classify every non-Exact by observable trigger and production code path.
4. Require at least three independent companies, one trigger, one code path
   and expected recovery of at least three records before Phase B.
5. Keep HP portfolio pollution and intermediary publication hypotheses open
   only if this independent cohort supplies the missing qualifying companies.
6. Preserve zero wrong URL, cross-company, cross-tenant and wrong-location
   publication.
