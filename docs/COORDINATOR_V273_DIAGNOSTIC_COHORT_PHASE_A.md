# v273 Backend Diagnostic Cohort - Phase A

## Purpose

Run a new backend-only development cohort collected from current public
LinkedIn search cards. This is not a blind product holdout.

Plugin work, authenticated External Apply, coordinator-v2, the LLM branch and
sealed holdouts remain outside this run.

## Frozen Input

Input:

`/private/tmp/v273-diagnostic-input.json`

- SHA-256:
  `d10afae92b17c43604192ca8e4240c77f337acf950ce42a77a5ad36aa6a7fc79`;
- records: 30;
- independent companies: 30;
- unique LinkedIn job IDs: 30;
- prior candidate-pool and diagnostic company-name overlap: 0;
- prior candidate-pool and diagnostic LinkedIn job-ID overlap: 0;
- sealed holdouts read: false.

Two current S1-only public pools were combined. Selection excludes every prior
pool and diagnostic company/job ID, keeps the first record per company,
preserves source order, applies fixed five-record role quotas and caps the
cohort at 30. No S2-S7 output influenced selection.

Role-family composition:

- Account Executive: 2;
- Data Scientist: 1;
- Legal Counsel: 5;
- Manufacturing Engineer: 1;
- Product Manager: 2;
- Program Manager: 4;
- Sales Manager: 5;
- Software Engineer: 5;
- UX Designer: 5.

Manifest:

`/private/tmp/v273-diagnostic-input-manifest.json`

## Frozen Execution

- adapter version: `2026-07-27.270`;
- candidate discovery engine: `stage_v1`;
- isolated roots below `/private/tmp/v273-diagnostic-run1`;
- no checkpoint, completion, evidence, snapshot or replay reuse;
- serial 30-company live capture;
- no product-code change during live or replay.

## Acceptance

1. Audit every Exact through the complete S7 identity chain.
2. Replay all 30 records with zero integrity and request-plan divergence.
3. Classify failures by observable trigger and shared production code path.
4. Require expected recovery of at least three independent companies before
   implementation.
5. Preserve zero wrong URL, cross-company, cross-tenant and wrong-location
   publication.
