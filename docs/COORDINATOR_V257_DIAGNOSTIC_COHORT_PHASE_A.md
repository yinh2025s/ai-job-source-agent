# v257 Development Diagnostic Cohort - Phase A

## Purpose

Collect a tenth backend-only development cohort on frozen `.255` code. The
cohort seeks a generic provider-family, inventory, transport, identity or
replay defect shared by at least three independent companies. Stage labels and
terminal reason counts are diagnostic only.

Plugin work, authenticated External Apply, coordinator-v2, the LLM branch and
sealed holdouts remain outside this run.

## Frozen Input

Input: `/private/tmp/v257-diagnostic-input.json`

- SHA-256:
  `6ce4f479be3d91dc93b52d216998be2627a48c6bf7246a059b87f66180524e36`;
- records: 30;
- independent companies: 30;
- unique LinkedIn job IDs: 30;
- overlap with 529 known public development job IDs: 0;
- public-search role families:
  - Security Engineer: 6;
  - Financial Analyst: 5;
  - Product Manager: 5;
  - Marketing Manager: 5;
  - Human Resources Manager: 3;
  - Quality Engineer: 6.

Selection preserves public-search order after prior-ID, duplicate-company and
role-quota filtering. ATS, Website, Career, provider and expected terminal did
not influence selection. External Apply remains unknown.

Manifest: `/private/tmp/v257-diagnostic-input-manifest.json`.
It records `sealed_holdouts_read=false`.

## Frozen Execution

- adapter version: `2026-07-27.255`;
- candidate discovery engine: `stage_v1`;
- isolated roots below `/private/tmp/v257-diagnostic-run1`;
- no checkpoint, completion, evidence, snapshot or replay reuse;
- serial 30-company live capture;
- no product-code change during live or replay.

## Acceptance

1. Audit every Exact for the complete S7 identity and title/location chain.
2. Export and replay all 30 records; report every mismatch and integrity gap.
3. Classify every non-Exact by observable trigger and production code path.
4. Require at least three independent companies, one trigger, one code path
   and expected recovery of at least three records before Phase B.
5. Reject apparent clusters whose replay or targeted evidence produces fewer
   than three recoverable records.
6. Preserve zero wrong URL, cross-company, cross-tenant and wrong-location
   publication.
