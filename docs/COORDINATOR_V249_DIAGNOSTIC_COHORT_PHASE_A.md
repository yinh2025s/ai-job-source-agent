# v249 Development Diagnostic Cohort - Phase A

## Purpose

Collect backend-only causal evidence on the unchanged `.246` implementation.
The extension, authenticated External Apply path, coordinator-v2, LLM branch
and sealed blind cohorts remain outside this run.

## Frozen Input

Input: `/private/tmp/v249-diagnostic-input.json`

- records: 30;
- independent companies: 28;
- unique LinkedIn job IDs: 30;
- prior job-ID overlap: 0 against Fresh100 and v245-v248;
- role families:
  - Customer Success Manager;
  - Content Marketing Manager;
  - Data Analyst;
  - Sales Development Representative;
  - Project Manager;
  - Controller.

Records are selected in public-search result order. Company names and ATS
families are not used to include or exclude samples. Public cards leave
External Apply state unknown.

## Frozen Execution

- adapter version: `2026-07-27.246`;
- candidate discovery engine: `stage_v1`;
- isolated roots below `/private/tmp/v249-diagnostic-run1`;
- no checkpoint, completion, evidence, snapshot or replay reuse;
- serial full-cohort live capture;
- no product-code modification during the run.

## Acceptance

1. Audit every Exact for company, title, location, provider, tenant, opening
   status and canonical URL.
2. Export and replay all 30 records on the same version.
3. Classify non-Exact outcomes by observable trigger and production code path,
   not stage or terminal label.
4. Authorize implementation only when at least three independent companies
   share one trigger and code path with expected recovery of at least three.
5. Keep lower-count findings as evidence; do not add company exceptions.
6. Do not alter Fresh100 aggregate metrics from this development cohort.
