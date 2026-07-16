# Blind Holdout V1 Report

Status: **complete under Codex artifact-review authority; independent human review not claimed**.

## Freeze And Execution

- Freeze commit: `5b090a236933638dceb463e235daed9e5d71cabc`
- Cohort digest: `04b3d45d2bb738ef0aa8af0edc39235a718a11556002925b8dea807c6e1e3376`
- Records: 40 unique companies and 40 unique LinkedIn jobs
- Historical audit: 2,398 files, complete Git patch history, 45 rejected overlaps, 0 skipped files
- Discovery-answer prefills: 0
- Run ID: `d5c9a520-2edc-4493-af38-e1bb178eb506`
- Live executions: exactly 1; serial; no resume or retry resubmission
- Runtime: 1,169.6 seconds
- Artifact digest chain: valid

The one-shot ledger was consumed before network execution. This cohort is permanently
`blind_observed` and cannot be rerun as a blind claim.

## Raw Funnel

| Stage | Count | Rate |
| --- | ---: | ---: |
| Verified website | 33/40 | 82.5% |
| Career page | 23/40 | 57.5% |
| Job list | 15/40 | 37.5% |
| Exact opening | 4/40 | 10.0% |

Pipeline statuses are 4 success, 18 partial, and 18 failed. Terminal/runtime reasons include
8 job-board-not-found, 8 opening-discovery-incomplete, 7 fetch-budget-exhausted, 7
website-not-resolved, 3 career-page-not-found, 2 verified opening-not-found, and 1
result-identity-mismatch. These are runtime outcomes, not final human dispositions.

## Review Separation

Codex artifact review is complete for 40/40 records. Separate official web checks found current
opening, board-tenant, hiring-entity, title, location, and accessibility evidence for all four
exact outputs. This manifest is the evaluation authority for the V1 metrics below.

The separate independent-human manifest is unsigned and incomplete. Contract validation
currently accepts 1/40 records (`Resonate AI`); the remaining 39 records still contain
incomplete fields. Top-level `reviewer_id` and `reviewed_at`, the detached SSH signature, and
the allowed-signers file are absent. It is retained as a distinct future review channel and is
not used, implied, or claimed in this baseline.

## Codex-Reviewed Metrics

The following metrics are derived from the frozen live artifacts and the separate 40/40 Codex
artifact review. They are reportable as the Codex-reviewed V1 baseline, but must not be
described as independent human evaluation.

| Metric | Result | Authority / interpretation |
| --- | ---: | --- |
| Raw exact rate | 4/40 (10.0%) | Frozen runtime output |
| Exact precision | 4/4 (100.0%) | All four emitted exact URLs passed Codex identity review |
| Conditional exact recall | 4/4 (100.0%) | Denominator includes only four known-eligible records; 36 remain unknown |
| System defect rate | 36/40 (90.0%) | Codex-reviewed disposition |
| Verified closed / no-public | 0/40 | No record was verified into either disposition |
| Recruiter / client undisclosed | 0/40 | No relationship was verified into this disposition |
| External blocked | 0/40 | No record met this disposition; runtime separately recorded 7 retryable failures |
| Eligibility unknown | 36/40 (90.0%) | Eligibility could not be established from the reviewed artifacts |

All four exact outputs (`Solace`, `Newsweek`, `Atomic Machines`, and `Victaulic`) have a
Codex-suggested verified identity chain. The other 36 records are provisionally marked
`system_gap`, but that label intentionally does not claim that a public matching opening
existed. Two records reached verified provider inventory with no match, seven ended after
fetch-budget exhaustion, and the remaining failures stopped earlier in discovery or identity
validation. Those runtime facts do not establish closed/no-public or undisclosed-client
dispositions, which is why eligibility remains unknown for 36 records.

## Sampling Limitation

Although candidate collection queried eight job families, first-unseen selection plus historical
overlap rejection yielded 14 Registered Nurse, 11 Account Executive, 10 Manufacturing Engineer,
4 Financial Analyst, and 1 Software Engineer records. The baseline is genuinely unseen but is
not job-family balanced. V1 must be reported with this skew; balancing requires a newly frozen
V2 policy and cannot retroactively alter this cohort.

## Completion Audit

| Goal requirement | Authoritative evidence | Status |
| --- | --- | --- |
| Freeze 30-50 previously unseen records | 40-record cohort; historical audit rejected 45 overlaps and recorded zero post-selection overlaps | Proven |
| Freeze cohort identity and source boundary | Cohort, identity, candidate-pool, source-tree, and rejected-overlap SHA-256 values in `holdout-manifest.json` | Proven |
| Freeze timestamp and run configuration | `frozen_at`, `history_cutoff`, full `run_configuration`, and its SHA-256 in the holdout manifest | Proven |
| Execute exactly once after freeze | Consumed one-shot ledger plus execution manifest with `live_execution_count=1` and one run ID | Proven |
| Preserve result artifact integrity | Execution manifest binds results, trace, and summary digests; chain verifier passes | Proven |
| Report raw exact rate | 4/40 (10.0%) from frozen results | Proven |
| Keep Codex artifact review and other review channels separate | Separate manifests and explicit authority labels | Proven |
| Verify every exact identity chain | Codex review checked company/hiring entity, provider tenant, title, location, and accessibility for 4/4 | Proven |
| Report precision, recall, defect rate, and six dispositions | Codex-reviewed metrics and denominator caveats are reported above | Proven |
| Make no failure/provider/heuristic/identity fix during baseline | No such change is included in the frozen execution or this reporting update | Proven |
| Propose no more than three next-round candidates and stop | Three clusters are listed below; no product fix is started in this report | Proven |

Therefore the blind run and Codex-reviewed baseline are complete. Independent human evaluation
remains a separate optional extension and is not part of this report's authority claim.

## Candidate Failure Clusters

No fixes were made during or after this baseline. Based on frequency and stage coverage, the
maximum-three candidates for the next development round are:

1. **Job-board discovery**: 8 records ended with `JOB_BOARD_NOT_FOUND`. Investigate generic
   career-to-ATS handoff discovery and provider-candidate validation as one cluster, without
   company-specific exceptions.
2. **Opening discovery**: 8 records reached a board but ended with
   `OPENING_DISCOVERY_INCOMPLETE`. Investigate provider inventory acquisition and bounded
   title/location matching using the frozen records only as observed diagnostics, not as a
   new blind cohort.
3. **Website resolution and fetch reliability**: 14 records comprise 7
   `WEBSITE_NOT_RESOLVED` and 7 retryable `FETCH_BUDGET_EXHAUSTED` outcomes. Separate identity
   resolution defects from transient network-budget failures before changing recall logic.

## Optional Independent Review

The human reviewer must independently inspect the official evidence, complete
`artifacts/blind_holdout/v1/reviews/human-review.json`, and sign the unchanged bytes with a
reviewer-controlled SSH key under namespace `ai-job-source-human-review`. Only then may
`scripts/apply_blind_reviews.py` generate a separately attributed human-reviewed result. This
optional extension must not overwrite or retroactively rename the completed Codex-reviewed V1
baseline.
