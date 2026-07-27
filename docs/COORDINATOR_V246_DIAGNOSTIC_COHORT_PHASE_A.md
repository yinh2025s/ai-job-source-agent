# v246 Development Diagnostic Cohort - Phase A

## Purpose

The `.245` Ashby runtime/replay defect is closed, while the remaining
development failures do not contain another implementation-qualified causal
cluster. This run collects new backend evidence without using the extension,
authenticated LinkedIn details or sealed holdouts.

## Frozen Cohort

- 30 public LinkedIn search cards;
- 29 independent companies;
- five records each for Product Manager, Data Scientist, Financial Analyst,
  Civil Engineer, Sales Development Representative and Physical Therapist;
- zero LinkedIn job-ID overlap with Fresh100;
- zero LinkedIn job-ID overlap with the `.245` 24-record diagnostic cohort;
- label: `development_diagnostic`, not a blind holdout.

Input: `/private/tmp/v246-diagnostic-input.json`

## Run Contract

- adapter version: `2026-07-27.245`;
- candidate engine: `stage_v1`;
- search backend: `legacy`;
- code frozen for the complete live and replay;
- fresh checkpoint, completion, evidence, snapshot and replay roots;
- bounded serial execution to avoid shared network-rate artifacts;
- no External Apply enrichment, extension input, coordinator-v2 or LLM path.

## Phase B Gate

A product change is authorized only when the captured evidence identifies:

1. at least three independent companies;
2. one common trigger;
3. one common production code path;
4. observed correct-candidate evidence; and
5. expected recovery of at least three records.

Stage labels, timeouts or filtered search counts alone are not causal clusters.
The live run must finish and replay before any implementation begins.
