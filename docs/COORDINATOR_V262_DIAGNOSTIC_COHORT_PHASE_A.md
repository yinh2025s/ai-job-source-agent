# v262 Development Diagnostic Cohort - Phase A

## Purpose

Run a twelfth backend-only development cohort on frozen `.261` code. This run
collects generic provider, inventory, first-party detail, locale, action,
identity, portfolio and replay defects shared by at least three independent
companies.

Plugin work, authenticated External Apply, coordinator-v2, the LLM branch and
sealed holdouts remain outside this run.

## Frozen Input

Input:
`/private/tmp/v262-diagnostic-input.json`

- SHA-256:
  `7bdb90180c7dc95616d7f60fd523fbf03cd67503b5fa6c3de2b0d30ee7c3e45a`;
- records: 30;
- independent companies: 30;
- unique LinkedIn job IDs: 30;
- overlap with 589 known public development job IDs: 0;
- public-search role families:
  - Operations Manager: 5;
  - Data Analyst: 5;
  - Sales Engineer: 5;
  - Recruiter: 5;
  - Mechanical Engineer: 5;
  - Nurse Practitioner: 5.

The records are unused members of the S1-only public pool collected for v260.
They have never executed S2-S7. Selection preserves public-search order after
prior-ID, duplicate-company and fixed role-quota filtering. Website, Career,
ATS, provider and expected terminal did not influence selection.

Manifest:
`/private/tmp/v262-diagnostic-input-manifest.json`

It records `sealed_holdouts_read=false`.

## Frozen Execution

- adapter version: `2026-07-27.261`;
- candidate discovery engine: `stage_v1`;
- isolated roots below `/private/tmp/v262-diagnostic-run1`;
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
5. Specifically test whether Motorola locale evidence, Daedalus first-party
   numeric detail, DataAnnotation generic Apply, portfolio pollution or
   intermediary publication gains the missing independent examples.
6. Preserve zero wrong URL, cross-company, cross-tenant and wrong-location
   publication.
