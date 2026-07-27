# v248 Development Diagnostic Cohort - Phase A

## Purpose

Collect another backend-only causal evidence cohort on unchanged `.246` code.
This run does not test the extension, coordinator-v2 or an LLM path and cannot
change Fresh100 or holdout metrics.

## Frozen Input

Input: `/private/tmp/v248-diagnostic-input.json`

- records: 30;
- independent companies: 29;
- six role families with five records each:
  - Medical Assistant;
  - DevOps Engineer;
  - Operations Manager;
  - Attorney;
  - Recruiter;
  - UX Designer;
- overlap with Fresh100, v245, v246 and v247 diagnostic LinkedIn job IDs: 0.

The records were collected from public LinkedIn search cards. External Apply
state remains unknown and is not interpreted as absent.

## Frozen Execution

- adapter version: `2026-07-27.246`;
- candidate discovery engine: `stage_v1`;
- isolated checkpoint, completion, evidence, snapshot and replay roots below
  `/private/tmp/v248-diagnostic-run1`;
- no cache, checkpoint, completion or snapshot reuse from prior cohorts;
- code remains frozen throughout live capture and replay.

## Acceptance

After the run:

1. audit every Exact for company, title, location, provider, tenant and URL;
2. replay the complete captured bundle and retain all mismatches and fixture
   gaps;
3. classify failures by trigger and common code path, not stage or budget label;
4. authorize implementation only for a path reproduced across at least three
   independent companies with expected recovery of at least three;
5. retain lower-count findings as evidence only;
6. do not inspect sealed blind v2/v3.
