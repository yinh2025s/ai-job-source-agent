# Fresh100 `.201` Focused Live Report

Run date: 2026-07-21

## Scope

- Runtime commit: `c352035`
- Adapter version: `2026-07-21.201`
- Source cohort: `samples/evaluation/live100_fresh_cohort_20260718.json`
- Focused records: Sunbird Software, STEAMe, IMG (International Medical
  Group), Steampunk, Inc., and Team Royal
- Run root: `/private/tmp/fresh100-v201-focused-20260721-run1`
- Cold start: 5 pending, 0 restored, one worker, `--no-resume`
- All checkpoint, completion, evidence, snapshot, failure, replay, and output
  paths were new and isolated from `.200`.

## Result

The run produced 0 Exact, 1 verified Job List, and 5 structured failures:

| Company | Terminal reason | Finding |
| --- | --- | --- |
| Sunbird Software | `NETWORK_TIMEOUT` | S2 transport failed before provider validation. |
| STEAMe | `RATE_LIMITED` | LinkedIn company evidence returned HTTP 429. |
| IMG (International Medical Group) | `RESULT_IDENTITY_MISMATCH` | JazzHR board and exact title were found, but S7 rejected the opening with `OPENING_LOCATION_UNVERIFIED`. |
| Steampunk, Inc. | `NETWORK_TIMEOUT` | S2 transport failed before provider validation. |
| Team Royal | `NETWORK_TIMEOUT` | The original 25-second outer S2 timeout triggered bounded recapture, which produced a finalized failure boundary. |

The four `.200` raw Exact records that lacked opening-location evidence did not
publish Exact under `.201`. IMG exercised the complete path and proved the new
S7 failure code. The other three were blocked by current transport outcomes, so
this run proves fail-closed behavior but does not measure their discovery recall.

## Boundary And Replay Gate

Team Royal exercised the new outer-timeout recovery path:

- eligible: 1
- attempted: 1
- replaced: 1
- boundary still missing: 0
- recapture execution failure: 0

The full scoped outcome-tape replay passed:

- integrity: 5/5
- reproduced: 5/5
- mismatch: 0
- fixture gap: 0
- replayability dropped: 0

This closes the missing capture-boundary defect observed in `.200`. It does not
claim that Team Royal's live website was resolved; the captured terminal outcome
is an honest, replayable `NETWORK_TIMEOUT`.

## Decision

The `.201` correctness contracts are accepted: an opening with missing target
location evidence cannot become Exact, and an outer S2 timeout no longer makes
the complete replay bundle invalid. The focused network was still unstable, so
the next causal work must separate LinkedIn 429/timeout transport from records
that can bypass website resolution through verified External Apply or provider
candidates. No company-specific rule is justified by this run.

Read-only archive:
`artifacts/releases/fresh100-v201-focused-20260721-run1.tar.zst`

SHA-256:
`72efc3971fecc388718deb2f9e4e82acc270ca7880d8c81b25d1ef46d06113e4`
