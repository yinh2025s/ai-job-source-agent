# Frozen 100 Final Report

Version: `2026-07-20.188`

## Final Gates

- Frozen cohort: 100/100 records completed from
  `/private/tmp/frozen100-v157-input.json`.
- Unified live artifacts: `/private/tmp/frozen100-v228-final100-results.json`,
  `/private/tmp/frozen100-v228-final100-trace.json`, and
  `/private/tmp/frozen100-v228-final100-summary.json`.
- Same-version replay: 100 reproduced, 0 mismatch, 0 fixture gap; record integrity
  passed 100/100 in `/private/tmp/frozen100-v228-final100-replay/`.
- Offline gates: 2429 tests passed (3 skipped), provider benchmark 25/25,
  resolver benchmark 6/6, and architecture validation 44 adapters with 0 issues.

## Product Result

The unified live run returned 69 Exact openings and 89 verified Job Lists in
473.2 seconds. The evidence-backed closure ledger is:

| Disposition | Count |
| --- | ---: |
| Exact | 69 |
| Verified Not Found | 23 |
| External Blocked | 5 |
| Input Identity Invalid | 3 |
| System Gap | 0 |

Raw Exact is `69/100 = 69%`. All 69 records judged eligible from the frozen
evidence ledger returned Exact, so eligible Exact recall is `69/69 = 100%`.
Independent comparison against `docs/FROZEN_100_CLOSURE_MATRIX.md` found
`69/69` canonical opening URLs equal to their audited closure evidence. Exact
precision on this frozen cohort is therefore `69/69 = 100%`.

The audit also checked title, location, company/hiring entity, provider, tenant,
public opening state, and relationship evidence. There are 0 wrong opening URLs,
0 cross-company results, 0 cross-tenant results, and 0 unresolved system gaps.

## Live Variability

Ten records encountered current transport failures in the unified run. Three are
already closed as External Blocked by focused non-retryable access evidence; one
is an Input Identity Invalid record; and six are Verified Not Found records with
prior focused complete-inventory or no-public-opening evidence. The unified run
does not rewrite those evidence-backed dispositions from a transient attempt.
Its snapshots still replay the exact observed outcomes 100/100.

## Final Correctness Fix

The `.187` unified run exposed an unrelated same-name Blossom restaurant Career
page and a stale `blossom.net` website cache. `.188` requires a cross-domain
Career search lead to prove company identity, same-origin canonical metadata, an
actionable same-origin jobs route, and an official corporate backlink before it
can be selected or persisted. It also revalidates a conflicting full LinkedIn
slug website in the same wave as stored company evidence instead of allowing the
cache to return early.

Starting from the incorrect stored state, focused live and the final unified run
both recover the continuous official chain:

`joinblossomhealth.com -> /careers -> Ashby Blossom-Health -> Software Engineer (All Levels)`

Blossom focused replay is 1/1, and the final unified replay is 100/100.
