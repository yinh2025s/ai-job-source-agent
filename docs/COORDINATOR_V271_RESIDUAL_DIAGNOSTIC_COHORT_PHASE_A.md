# v271 Backend Residual Diagnostic Cohort - Phase A

## Purpose

Run the remaining never-used S1-only public records through the deterministic
backend on frozen `.270` code. This is a development diagnostic cohort, not a
blind product holdout.

Plugin work, authenticated External Apply, coordinator-v2, the LLM branch and
sealed holdouts remain outside this run.

## Frozen Input

Input:

`/private/tmp/v271-diagnostic-input.json`

- SHA-256:
  `557e50c7dfcf97d38d7371b5b567d131b74c32013f697cd36ad8baba8435878b`;
- records: 18;
- independent companies: 18;
- unique LinkedIn job IDs: 18;
- prior v245-v265 diagnostic company-name overlap: 0;
- prior v245-v265 diagnostic LinkedIn job-ID overlap: 0;
- sealed holdouts read: false.

The records are every remaining unused company in the frozen
v252/v254/v256/v257/v260 S1-only public pools. Selection excludes every prior
diagnostic company and job ID, keeps the first record per company and preserves
source-pool order. No S2-S7 output influenced selection.

Role-family composition:

- Business Analyst: 2;
- Clinical Research Coordinator: 1;
- Construction Project Manager: 1;
- Cybersecurity Analyst: 1;
- Electrical Engineer: 1;
- Financial Analyst: 2;
- Marketing Coordinator: 1;
- Quality Engineer: 5;
- Security Engineer: 2;
- Supply Chain Manager: 2.

Manifest:

`/private/tmp/v271-diagnostic-input-manifest.json`

## Frozen Execution

- adapter version: `2026-07-27.270`;
- candidate discovery engine: `stage_v1`;
- isolated roots below `/private/tmp/v271-diagnostic-run1`;
- no checkpoint, completion, evidence, snapshot or replay reuse;
- serial 18-company live capture;
- no product-code change during live or replay.

## Acceptance

1. Audit every Exact through the complete S7 identity chain.
2. Replay all 18 records with zero integrity and request-plan divergence.
3. Classify failures by observable trigger and shared production code path.
4. Require expected recovery of at least three independent companies before
   implementation.
5. Preserve zero wrong URL, cross-company, cross-tenant and wrong-location
   publication.
6. Do not reopen a closed provider based only on a shared failure stage.
