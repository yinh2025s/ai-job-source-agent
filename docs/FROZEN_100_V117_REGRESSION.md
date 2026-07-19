# Frozen 100 `.117` Regression

Run date: 2026-07-18

## Result

The observed development cohort completed all 100 live network records with the
same exhaustive three-route configuration used by the original run.

| Stage | Count |
| --- | ---: |
| Verified website | 57 |
| Career page | 44 |
| Verified Job List | 40 |
| S7 Exact opening | 27 |

This is a regression from the original 28 Exact baseline. Focused artifacts are
not merged into this number. All 27 published openings have an S7-approved URL;
rejected candidates remain absent from `open_position_url`.

## Failure Clusters

- 42 records: LinkedIn company evidence denied by HTTP 999 or 451 during S2.
- 8 records: other transport or company-budget terminals.
- 12 records: verified Job List without a published opening.
- 8 records: S7 identity rejection; all are correct fail-closed decisions.
- 4 repeated-company groups account for 20 transport/budget records. Company-level
  S2-S5 evidence reuse can remove at most 16 duplicate upstream runs while S6/S7
  remain posting-specific.

The 12 board-without-opening records split into six correct location-evidence
rejections, three verified no-match or external constraints, one SmartRecruiters
storefront inventory gap, one S6 budget-starvation case, and one additional
board-only partial. No location, company, tenant or relationship gate is relaxed.

## Replay Gate

Live results, traces, summary and route metrics were written successfully. The
automatic failure bundle then failed strict replay because S5 emitted board A,
S6 changed the final top-level board to B, and replay incorrectly projected B
back into S5. Outcome-tape strictness correctly rejected two unconsumed requests.
`.118` reconstructs producer state from stage-specific evidence and must fail
preflight when the handoff is ambiguous.

## Artifacts

- `/private/tmp/frozen100-v117-first-results.json`
- `/private/tmp/frozen100-v117-first-trace.json`
- `/private/tmp/frozen100-v117-first-summary.json`
- `/private/tmp/frozen100-v117-first-routes.json`

These live artifacts are local and are not committed because they contain large
raw public-network traces. This report records the reproducible aggregate facts.

## `.120` Follow-up

The ADP board-without-opening retry defect was traced to a missing replay policy,
not to title matching. `.120` adds strict WFN/SRCCAR checkpoint validation. Steve
Madden now persists `job_board_discovery` and resumes directly at S6: wall time
drops from 82.3 seconds to 16.7 seconds, and both first-party Corporate and Retail
ADP inventories are checked. Both currently return complete empty inventories, so
the historical LinkedIn opening is not recoverable as an active Exact URL. The
next unified frozen-100 run must distinguish this verified external state from a
system discovery defect.
