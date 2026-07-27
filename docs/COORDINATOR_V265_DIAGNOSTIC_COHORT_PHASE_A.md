# v265 Backend Diagnostic Cohort - Phase A

## Purpose

Run another backend-only development cohort on frozen `.261` code, emphasizing
role-family breadth and companies never used in prior diagnostic cohorts.

Plugin work, authenticated External Apply, coordinator-v2, the LLM branch and
sealed holdouts remain outside this run.

## Frozen Input

Input:
`/private/tmp/v265-diagnostic-input.json`

- SHA-256:
  `63ec9db0847011452e608a6d0fea4ec1f6d7663d76ce5d2f78f7d54ec8b15dae`;
- records: 30;
- independent companies: 30;
- unique LinkedIn job IDs: 30;
- prior diagnostic company-name overlap: 0;
- prior diagnostic LinkedIn job-ID overlap: 0;
- role-family composition:
  - Backend Engineer: 3;
  - Business Analyst: 4;
  - Clinical Research Coordinator: 3;
  - Construction Project Manager: 2;
  - Cybersecurity Analyst: 3;
  - Electrical Engineer: 2;
  - Environmental Engineer: 2;
  - Financial Analyst: 3;
  - Human Resources Manager: 1;
  - Marketing Coordinator: 1;
  - Quality Engineer: 4;
  - Security Engineer: 1;
  - Supply Chain Manager: 1.

The records are unused S1-only members of existing public pools. Selection
excludes every prior diagnostic job ID and company name, takes the first record
per company and applies fixed cross-family quotas. No S2-S7 output influenced
selection.

Manifest:
`/private/tmp/v265-diagnostic-input-manifest.json`

It records `sealed_holdouts_read=false`.

## Frozen Execution

- adapter version: `2026-07-27.261`;
- candidate discovery engine: `stage_v1`;
- isolated roots below `/private/tmp/v265-diagnostic-run1`;
- no checkpoint, completion, evidence, snapshot or replay reuse;
- serial 30-company live capture;
- no product-code change during live or replay.

## Acceptance

1. Audit every Exact through the complete S7 identity chain.
2. Replay all 30 records with zero integrity and request-plan divergence.
3. Merge historical evidence only for identical triggers and code paths.
4. Require expected recovery of at least three independent companies before
   implementation.
5. Preserve zero wrong URL, cross-company, cross-tenant and wrong-location
   publication.
