# Fresh 100 `.193` Allocation Trace Phase C

## Frozen Run

- Code commit: `2f63a75df11e4c14610ca17d5b70b805d51d6962`
- Adapter version: `2026-07-20.193`
- Source cohort records: 005, 029, and 033 from the July 18 fresh cohort
- Inputs: two Versana postings and one Slant CRM posting
- Isolation: new checkpoint, completion, evidence, snapshot, replay, and output
  roots below `/private/tmp/fresh100-v193-allocation-trace-20260720-run1`
- Resume policy: disabled; workers: 1; `verify_limit=3`

The run archive is
`artifacts/releases/fresh100-v193-allocation-trace-20260720-run1.tar.zst`,
SHA-256 `5ef2096329fca00c967828a6decb037e710799b637f30e0b21990165c10dbbed`.
The code stayed frozen. This trace-only run does not alter any aggregate score.

## Gates

Full offline gates pass 2491 tests (4 skipped), 25/25 provider cases, 6/6
resolver cases, and 46 native adapters with zero architecture issues. Focused
live completed 3/3 and intentionally retained all three S2 `FETCH_FAILED`
outcomes because `.193` does not change allocation behavior. Same-version replay
reproduced 3/3 with zero mismatch, fixture gap, or dropped record.

## Allocation Evidence

Both Versana records produced identical decisions.

The `fast_candidates` phase selected three `versanatech` domains:

1. `versanatech.com` by `source_reservation:linkedin_slug`
2. `versanatech.ai` by `score_fill`
3. `versanatech.io` by `score_fill`

The `merged_search_candidates` phase selected `versana.tech`, `versana.com`,
and `versana.org` by `score_fill`. The correct `versana.io` candidate was
present in both phases and explicitly recorded as `slot_limit_reached`. This
confirms one shared resolver path for record-level 2/2, while also showing that
the two records represent only one unique company-host recovery.

Slant CRM did not reproduce the old search pool. Its current merged phase
contained only six speculative `slantcrm.*` candidates; `slant.app` was never
generated and therefore had no allocation decision. The prior search result was
unstable, so record 033 is a candidate-generation/search-source defect in the
current run, not a verification-allocation or identity-rejection defect.

## Decision

The original 005/029/033 allocation cluster is rejected. The next behavior work
must remain separate:

- Versana: obtain stronger source evidence or introduce a general bounded
  family-allocation rule that recovers record-level 2/2 under `verify_limit=3`
  and preserves collision negatives. A hard-coded `.io` preference is invalid.
- Slant CRM: use a stable source that produces and proves the employer candidate,
  or use the independently verified Ashby provider-first route through S5-S7.
  Increasing S2 slots cannot recover a candidate that was not generated.

The `.193` observability cluster is closed. No Versana or Slant behavior cluster
is yet claimed closed.
