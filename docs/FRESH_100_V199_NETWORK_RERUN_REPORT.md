# Fresh 100 `.199` Network Rerun Report

Run date: 2026-07-21

## Isolation

- Runtime commit: `66054fdcad16b5c910e22b41266f6452d8cc11d9`
- Adapter version: `2026-07-20.199`
- Input: `samples/evaluation/live100_fresh_cohort_20260718.json`
- Input SHA-256: `fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`
- New run root: `/private/tmp/fresh100-v199-network-rerun-20260721-run1`
- Cold start: 100 pending, 0 restored, `--no-resume`
- v2/v3 blind holdouts were not opened or executed.
- The run used direct network access after LinkedIn returned HTTP 200; the inactive
  local proxy was not used.

The product behavior at this commit is the same as the current `bf578db` main
commit; the latter only adds blind-selection receipts and governance text.

## Live Results

| Metric | `.188` cold baseline | `.199` network rerun |
| --- | ---: | ---: |
| Program raw Exact | 12/100 | 23/100 |
| Audited correct Exact | 11/100 | 22/100 |
| Verified website | 47/100 | 73/100 |
| Career page | 42/100 | 57/100 |
| Verified Job List | 38/100 | 55/100 |
| Wrong opening URL | 1 | 1 |
| Cross-company false positive | 0 | 0 observed |
| Cross-tenant false positive | 0 | 0 observed |

Wall time was 1,317 seconds. The pipeline terminal distribution was 23 success,
35 partial and 42 failed. The semantic terminal distribution was:

| Terminal outcome | Count |
| --- | ---: |
| Exact opening | 23 |
| Retryable failure | 30 |
| Discovery unresolved | 22 |
| Verified no match | 12 |
| External blocked | 5 |
| Other non-success | 5 |
| Identity ambiguous | 1 |
| Unsupported capability | 1 |
| No public openings | 1 |

The improved network materially raised every discovery level, but it did not
remove the system defects. The 30 retryable terminals include 29
`NETWORK_TIMEOUT` diagnostics; the remaining records still expose Job List
navigation, inventory search, identity and provider capability gaps.

## Exact Audit

All 23 raw Exact records passed the automated S7 identity assertion and output
validation. Independent review rejected one:

- Arkema requested Beaumont, Texas, but the selected official opening is in
  Clear Lake, Texas. It remains a same-company and same-tenant location false
  positive and must not count as Exact.

The other 22 records have matching company, title, provider/tenant and canonical
opening evidence, with no observed cross-company or cross-tenant URL. The newly
recovered IMG official posting was additionally checked live and contains both
`UX Designer` and `Indianapolis, IN`.

## Replay Gate

The live result files contain all 100 records, but replay did not pass:

- Automatic failed/partial replay diverged on Hawaiian Electric because one
  `GET https://www.hawaiianelectric.com` outcome remained unconsumed.
- A separate all-record replay diverged on CHAMP because one
  `GET https://www.champtitles.com/open-positions` outcome remained unconsumed.
- No live rerun or resume was used to hide either divergence.

This run is therefore a complete live benchmark with an incomplete replay gate,
not a release acceptance result.

## Artifact Digests

- `results.json`: `249a2a22f62f69aabbda0c03158a5b464a95da0fa45b2528df58bda06f61e6a4`
- `trace.json`: `a3730c60c44600a648b1ed532d341d8222fccc9d1ba5df3d5ae4d7735434f13e`
- `summary.json`: `3e3bdaa7103c6eaeea543b0620b240a3852e19e6735a735b4fd4d93ebeeab134`

The `.188` score remains immutable. This rerun is a separate diagnostic showing
that network quality explains a substantial part of the earlier loss, while
transport stability, replay determinism and behavioral discovery gaps remain.
