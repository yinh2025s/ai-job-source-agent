# Blind Holdout V1 Report

Status: **provisional; signed human evaluation pending**.

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

## Review Status

Codex artifact review is complete for 40/40 records. Separate official web checks found current
opening, board-tenant, hiring-entity, title, location, and accessibility evidence for all four
exact outputs. This is a suggestion artifact only and cannot establish reportable precision.

The independent human manifest is still blank and unsigned. Until a human verifies and signs
all records, the following metrics are intentionally withheld:

- human-verified exact precision;
- conditional exact recall and its eligible denominator;
- system defect rate;
- final six-disposition distribution.

## Sampling Limitation

Although candidate collection queried eight job families, first-unseen selection plus historical
overlap rejection yielded 14 Registered Nurse, 11 Account Executive, 10 Manufacturing Engineer,
4 Financial Analyst, and 1 Software Engineer records. The baseline is genuinely unseen but is
not job-family balanced. V1 must be reported with this skew; balancing requires a newly frozen
V2 policy and cannot retroactively alter this cohort.

## Next Gate

The human reviewer must independently inspect the official evidence, complete
`artifacts/blind_holdout/v1/reviews/human-review.json`, and sign the unchanged bytes with a
reviewer-controlled SSH key under namespace `ai-job-source-human-review`. Only then may
`scripts/apply_blind_reviews.py` generate final traces, metrics, dispositions, and failure
clusters. No product fix or provider expansion may begin before that report is reviewed.
