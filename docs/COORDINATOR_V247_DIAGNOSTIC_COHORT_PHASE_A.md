# v247 Development Diagnostic Cohort - Phase A

## Frozen Cohort

- 30 public LinkedIn search cards;
- 27 independent companies;
- five records each for Marketing Manager, Human Resources Business Partner,
  Electrical Engineer, Occupational Therapist, Controller and Customer Success
  Manager;
- zero job-ID overlap with Fresh100, v245 diagnostic and v246 diagnostic;
- development-only, not a blind holdout.

Input: `/private/tmp/v247-diagnostic-input.json`

## Run Contract

- frozen backend version `2026-07-27.246`;
- `stage_v1`, legacy search and no authenticated External Apply;
- fresh checkpoint, completion, evidence, snapshot and replay roots;
- no code modification during live/replay;
- no extension, coordinator-v2, LLM branch or sealed holdout access.

## Implementation Gate

Only a cluster with at least three independent companies, one common trigger,
one common code path, observed correct evidence and expected recovery of at
least three records may enter Phase B.
