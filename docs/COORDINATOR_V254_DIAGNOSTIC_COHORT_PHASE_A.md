# v254 Development Diagnostic Cohort - Phase A

## Purpose

Collect an eighth backend-only development cohort on frozen `.253` code. The
run seeks a provider-family, transport, parser or identity cluster shared by at
least three independent companies. A common stage or terminal reason alone
does not authorize implementation.

Plugin work, authenticated External Apply, coordinator-v2, the LLM branch and
sealed holdouts remain outside this run.

## Frozen Input

Input: `/private/tmp/v254-diagnostic-input.json`

- SHA-256:
  `99a4adbf17229945f3c0cafe72d2da245911b1f1ab0e7b0bc87760e67c4c4f90`;
- records: 30;
- independent companies: 30;
- unique LinkedIn job IDs: 30;
- overlap with the original and Fresh100 cohorts plus v245-v253: 0;
- excluded prior development job IDs: 405;
- public-search role families:
  - Cybersecurity Analyst: 5;
  - Product Designer: 3;
  - Supply Chain Manager: 5;
  - Clinical Research Coordinator: 6;
  - Sales Development Representative: 5;
  - Environmental Engineer: 6.

Records preserve public-search order after removing prior job IDs and duplicate
companies. ATS, company, website, Career page and terminal outcome did not
influence selection. External Apply remains unknown.

The input manifest is
`/private/tmp/v254-diagnostic-input-manifest.json`. It records
`sealed_holdouts_read=false`.

## Frozen Execution

- adapter version: `2026-07-27.253`;
- candidate discovery engine: `stage_v1`;
- isolated roots below `/private/tmp/v254-diagnostic-run1`;
- no checkpoint, completion, evidence, snapshot or replay reuse;
- serial full-cohort live capture;
- no product-code change during live or replay.

## Acceptance

1. Audit every Exact for company, hiring entity, title, location, provider,
   tenant, current status and canonical opening URL.
2. Export and replay all 30 records.
3. Classify every non-Exact by observable trigger and production code path,
   not by S2/S5/S6/S7 or terminal label alone.
4. Require at least three independent companies, one shared trigger, one
   shared code path and expected recovery of at least three records before
   Phase B.
5. If a proposed fix recovers fewer than three records, reject or redefine the
   cluster rather than declaring closure.
6. Preserve strict company, hiring entity, provider, tenant, title, location,
   opening URL and S7 validation.
