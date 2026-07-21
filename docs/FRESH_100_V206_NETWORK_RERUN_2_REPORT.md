# Fresh 100 `.206` Network Rerun 2 Report

Run date: 2026-07-21

## Isolation

- Runtime commit: `11b12f0e4fe4ddaf4ba628463c04fc09b56d64b9`
- Adapter version: `2026-07-21.206`
- Input: `samples/evaluation/live100_fresh_cohort_20260718.json`
- Input SHA-256: `fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`
- Run root: `/private/tmp/fresh100-v206-network-rerun-20260721-run2`
- Cold start: 100 pending, 0 restored, `--no-resume`
- Explicit cohort guard: `--limit 100 --require-full-cohort`
- Four company workers used clean completion, checkpoint, evidence and snapshot
  roots.
- The runtime source was exported from the frozen commit, so the uncommitted
  `.207` route-outcome work did not participate in this run.
- Blind holdouts v2 and v3 were neither opened nor executed.

This is a second network diagnostic on the already observed Fresh100
development cohort. It does not replace `.188`, the first `.206` run, or any
blind holdout result.

## Live Results

| Metric | `.206` run 1 | `.206` run 2 | Delta |
| --- | ---: | ---: | ---: |
| Strict S7 Exact | 23/100 | 22/100 | -1 |
| Verified website | 75/100 | 75/100 | 0 |
| Career page | 60/100 | 58/100 | -2 |
| Verified Job List | 55/100 | 56/100 | +1 |
| Retryable terminal | 27/100 | 27/100 | 0 |
| Records observing `NETWORK_TIMEOUT` | 25/100 | 26/100 | +1 |
| Terminal `NETWORK_TIMEOUT` | 24/100 | 22/100 | -2 |

The live phase completed in 1,339.8 seconds. The second run did not establish a
healthier network baseline. It observed one more network-timeout reason than
run 1, while several records progressed past an earlier timeout and ended in a
different evidence-backed terminal state.

| Terminal reason | Count |
| --- | ---: |
| Network timeout | 22 |
| Result identity mismatch | 11 |
| Opening not found | 11 |
| Opening discovery incomplete | 10 |
| Job Board not found | 7 |
| HTTP forbidden | 5 |
| Career page not found | 4 |
| Company time budget exhausted | 4 |
| No public openings | 2 |
| Provider variant unsupported | 1 |
| Server error | 1 |

Three openings not Exact in run 1 became Exact:

- BWXT - Project Manager
- ProMach - Project Manager
- Prophetic - UX Designer

Four run-1 Exact openings were lost to terminal network timeouts:

- Loveland Innovations - DevOps Engineer
- iClassPro - Class Management Software - DevOps Engineer
- Indica Labs - DevOps Engineer
- Resolute Road Hospitality - Human Resources Manager

This churn is direct evidence that transport materially affects the observed
score. The net regression and the stable opening-discovery failures also show
that transport is not the sole remaining product defect.

## Exact Audit

All 22 published openings have a verified S7 identity assertion, empty failure
codes, a verified hiring relationship, and consistent provider, tenant, board,
opening, title and location evidence. Observed wrong URL, cross-company,
cross-tenant and wrong-location counts are all zero. Four result URLs differ
from their identity-canonical form only by a trailing slash; they canonicalize
to the same opening and are not audit failures.

The S7 gate continued to reject unsafe title, location or identity outcomes for
Sunbird, Target Hospitality, Mayo Clinic, IMG, Steampunk, both Arkema records,
Aramark and Cintas.

## Replay Gate

The failure bundle selected, exported and replayed 78/78 non-Exact records. It
classified 74 as reproduced and four company-budget outcomes as explicit
budget recovery, with zero mismatch and zero fixture gap.

The full bundle selected, exported and replayed 100/100 records. It classified
96 as reproduced and Diamondback Energy, NDIT, ARUP Laboratories and HP as the
same explicit budget recovery. Record integrity passed with zero omission,
zero dropped record, zero mismatch and zero fixture gap.

## Artifact Digests

- `results.json`: `67a3a9eb946d5c87bcc07a3687c328fc6e787ac6f6639098c32787d7014e8e3c`
- `trace.json`: `0e14905c4af50d27a486c52dc6e0036bb9c71850767ded8ac298598ba9d21207`
- `summary.json`: `e5ce709a51cd178b1cc56ecfaa825cd737e3737bbf9d1d1fcfbe6d31f3d0a320`
- Replay manifest: `8d17d08e9827518c19defbbea9f01d2067052c7f9fc96ae9f349233f79d32a32`
- Read-only run archive:
  `artifacts/releases/fresh100-v206-network-rerun-20260721-run2.tar.zst`
- Archive SHA-256:
  `723a4b543b8a15d1f814492c76d39b6a4d2083c70f1c7e8d9ab0837d90e3617d`

The next implementation decision remains the evidence-backed multi-route
cluster. OneApp, The Home Depot and Crosby still require route-local hiring
evidence to survive through S6; another identical network rerun would not close
that architecture defect.
