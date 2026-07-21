# Fresh 100 `.200` Network Rerun Report

Run date: 2026-07-21

## Isolation

- Runtime commit: `bc33bce753c68e783995383970c00ed2af713818`
- Adapter version: `2026-07-21.200`
- Input: `samples/evaluation/live100_fresh_cohort_20260718.json`
- Input SHA-256: `fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`
- New run root: `/private/tmp/fresh100-v200-network-rerun-20260721-run1`
- Cold start: 100 pending, 0 restored, `--no-resume`
- Four company workers used separate completion and snapshot scopes.
- Code remained frozen throughout the benchmark.
- Blind holdouts v2 and v3 were neither opened nor executed.

LinkedIn public search and the CISA dataset endpoint both returned HTTP 200 in
the preflight. No prior evidence, checkpoint, completion, or snapshot was
restored.

## Live Results

| Metric | `.199` rerun | `.200` rerun | Delta |
| --- | ---: | ---: | ---: |
| Program raw Exact | 23/100 | 28/100 | +5 |
| Strictly audited Exact | 22/100 | 23/100 | +1 |
| Verified website | 73/100 | 72/100 | -1 |
| Career page | 57/100 | 59/100 | +2 |
| Verified Job List | 55/100 | 57/100 | +2 |
| Retryable terminal | 30/100 | 27/100 | -3 |

The run completed all 100 live records in 1,082.1 seconds. Pipeline status was
28 success, 34 partial, and 38 failed. The terminal reason counts were:

| Reason | Count |
| --- | ---: |
| Exact opening | 28 |
| Rate limited | 19 |
| Opening discovery incomplete | 14 |
| Opening not found | 11 |
| Career page not found | 8 |
| Network timeout | 7 |
| Job board not found | 6 |
| Result identity mismatch | 5 |
| Company time budget exhausted | 3 |
| HTTP forbidden | 3 |
| No public openings | 3 |
| DNS failed | 1 |
| Provider variant unsupported | 1 |
| Server error | 1 |

All 45 captured `RATE_LIMITED` request outcomes came from LinkedIn company-page
requests during website resolution. The terminal network distribution therefore
changed from 29 timeouts in `.199` to 19 rate-limited records plus 7 timeouts in
`.200`; it did not become a stable network environment. Network variation is
the dominant explanation for the record-level churn, not the `.200` public
domain registry module.

## Exact Audit

Twenty-three of the 28 raw Exact records have continuous company, title,
location, provider, tenant, board, and opening evidence. No confirmed wrong-city,
cross-company, cross-tenant, or obviously invalid opening URL was found among
those 23.

Five raw Exact records do not satisfy the strict product gate:

| Company | Finding |
| --- | --- |
| Sunbird Software | Title and tenant are continuous, but the selected opening has no location evidence. |
| STEAMe | Title and tenant are continuous, but the selected opening has no location evidence. |
| IMG | Title and tenant are continuous, but the selected opening has no location evidence. |
| Steampunk, Inc. | Title and tenant are continuous, but the selected opening has no location evidence. |
| Resolute Road Hospitality | The first-party Career page hands off to a Braintree Hospitality Paylocity tenant, but the output still records `same_entity` instead of an explicit hiring relationship. |

These five records remain pending or rejected under the strict audit and are not
counted as verified Exact merely because the program emitted an opening URL.

## Record Churn

Eight records became raw Exact relative to `.199`: iClassPro, Knock, Loveland
Innovations, Vectra AI, Wolfe, Resolute Road Hospitality, the B&D Industries
Project Manager posting, and Milwaukee Tool. Three records lost raw Exact:

- NYC Department of Social Services changed from Exact to `RATE_LIMITED`.
- Arkema changed from the known wrong-city Exact to `RATE_LIMITED`; removing this
  raw success is not a correctness regression.
- Prophetic changed from Exact to `NO_PUBLIC_OPENINGS`, consistent with dynamic
  public inventory rather than a public-domain registry effect.

The other 20 raw Exact records retained the same opening URL.

The `.200` authoritative public-domain route emitted identity-compatible
candidates for City of Sioux Falls and State of Montana. Both later failed on
LinkedIn HTTP 429 during ordinary homepage verification. City of Pharr reached
its correct official website and then received HTTP 403 on Career discovery.
City of College Station reached its official GovernmentJobs board but the
opening inventory request ended in a server error. The module therefore proved
candidate generation and introduced no observed wrong government URL, but this
run does not establish four-company end-to-end recovery.

## Replay Gate

The automatic 100-record replay bundle failed preflight before replay execution.
Team Royal's outer company timeout produced no finalized
`website_resolution` capture boundary. The integrity manifest correctly reports:

- selected/exported: 100/100
- replayed: 0/100
- boundary-invalid records: 1
- reason: `captured_execution_boundary_missing`

This is separate from the `.199` batch-final evidence contamination and
wall-clock normalization defects fixed and validated on the isolated
`codex/outcome-tape-determinism-b` branch. That branch was not integrated during
this code-frozen run. The `.200` replay gate is failed, not waived.

## Artifact Digests

- `results.json`: `b138714f4e2b9e939900015e8364461a3f6c375f70e734faf8301465acb83f38`
- `trace.json`: `36b0349bcde4e8994c192a0ba5a76e9d428a3d9191bb46e51f528a0cde046a9b`
- `summary.json`: `4ff577842e7fe4aa0509b8976a44fadd259fb53e9ddfcae02f2a123d64a9c77c`
- Read-only run archive:
  `artifacts/releases/fresh100-v200-network-rerun-20260721-run1.tar.zst`
- Archive SHA-256:
  `d708c05546905e88ca1eebd5d0c6b5b826ad47d2f1eaf16a4539d10a02db33ee`

The `.188` and `.199` artifacts and scores remain unchanged. This `.200` run is
a complete cold live diagnostic, not a release acceptance result.
