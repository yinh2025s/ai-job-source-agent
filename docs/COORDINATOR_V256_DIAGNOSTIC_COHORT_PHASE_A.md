# v256 Development Diagnostic Cohort - Phase A

## Purpose

Collect a ninth backend-only development cohort on frozen `.255` code. The
cohort seeks a generic provider-family, inventory, transport, identity or
replay defect shared by at least three independent companies. Stage labels and
terminal reason counts are diagnostic only.

Plugin work, authenticated External Apply, coordinator-v2, the LLM branch and
sealed holdouts remain outside this run.

## Frozen Input

Input: `/private/tmp/v256-diagnostic-input.json`

- SHA-256:
  `009bd616f23a7bbfc04e64d98aef1b25695aa6e14e3eff80b9a08d3ff61e8533`;
- records: 30;
- independent companies: 30;
- unique LinkedIn job IDs: 30;
- overlap with the original and Fresh100 cohorts plus v245-v255: 0;
- excluded prior development job IDs: 435;
- six public-search role families, five records each:
  - Data Scientist;
  - Operations Manager;
  - Electrical Engineer;
  - Customer Success Manager;
  - Nurse Practitioner;
  - Construction Project Manager.

Records preserve public-search order after prior-ID and duplicate-company
removal. ATS, Website, Career, provider and expected terminal did not influence
selection. External Apply remains unknown.

Manifest: `/private/tmp/v256-diagnostic-input-manifest.json`.
It records `sealed_holdouts_read=false`.

## Frozen Execution

- adapter version: `2026-07-27.255`;
- candidate discovery engine: `stage_v1`;
- isolated roots below `/private/tmp/v256-diagnostic-run1`;
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
