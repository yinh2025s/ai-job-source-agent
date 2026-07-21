# Fresh100 `.212` Stabilization Final Report

## Final Code State

- Commit before final evidence documentation:
  `078b85249d7ddc6a658020bb8275a8994d94ec6c`
- Adapter: `2026-07-21.212`
- `.188` source and Frozen100 release artifacts remain immutable.
- Sealed holdout v2/v3 paths were not opened or executed.
- The unauthorized LLM candidate-reasoning branch was not merged or copied.

## Fresh100 `.209` Cold Run

The clean, frozen `.209` run executed all 100 records with zero restored
completions:

- 19 Exact.
- 78 verified Websites.
- 60 Career pages.
- 56 Job Lists.
- 19 success, 38 partial and 43 failed pipeline outcomes.

All 19 Exact records passed company, hiring entity, provider, tenant, board,
opening URL, title and accepted-location audit. Observed wrong URL,
cross-company, cross-tenant and wrong-location Exact counts were zero.

Full same-version replay executed 100/100 as 95 reproduced, four accepted
budget recoveries, zero fixture gaps, zero tape divergence and one Heritage
identity mismatch. Therefore `.209` replay execution completed but acceptance
failed. The full immutable artifact is archived at:

`artifacts/releases/fresh100-v209-cold-20260721-run1.tar.zst`

SHA-256:
`f9b93172b4731143bc8d8458aacec3215eaedd4cbf932a514a97130f91553607`

## Causal Reclassification

The 81 non-Exact records were exclusively partitioned by causal evidence, not
by stop stage. The largest categories were correct candidate not produced
(31), provider/inventory incomplete (11), candidate identity rejected (10),
verified no-match (9), correct candidate present but transport failed (5), and
budget starvation (4). The complete reconciled ledger is in
`docs/FRESH_100_V209_CAUSAL_ANALYSIS.md`.

Neither the 18 terminal S2 timeouts nor the eight
`OPENING_DISCOVERY_INCOMPLETE` outcomes formed one executable cluster. Their
subcauses include source refusal, endpoint/TLS/DNS variance, candidate
generation, embedded ATS promotion, dynamic inventory, location evidence and
provider coverage.

## Repair Round One

`.210` fixed a provider-independent identity defect: stored first-party board
evidence had projected an opaque ATS tenant into `hiring_entity_name`.
Provider/tenant authorization is now separate from the verified S3 hiring
identity. Paylocity, Ashby and Workday locator forms are covered by tests.

Targeted verification passed 202 tests. A migration replay corrected only the
Heritage hiring identity while preserving its Paylocity tenant and board.
Clean `.210` focused replay passed 3/3 with zero mismatch or fixture gap;
Heritage live was transport-blocked in S2 twice, so live execution of the
stored-board branch was not claimed.

## Repair Round Two

`.211` tested a narrower request-scheduling hypothesis: delay unfiltered
landing-page JS asset discovery until official title routes run. Offline tests
passed, but the frozen four-company live recovered 0/4 Exact:

- Sentar remained network-timeout.
- WENDEL became opening-discovery-incomplete.
- Crawford Thomas became opening-discovery-incomplete.
- Crosby became Job Board portfolio-incomplete.

Same-version replay passed 4/4 with zero mismatch or fixture gap. Since the
predeclared threshold required at least three independent verified opening
recoveries, the cluster was rejected and the behavior was reverted in `.212`.
No third generic architecture repair was started.

## Final Offline Gate

The single final integration gate passed:

- 2574 tests, four skipped.
- Provider benchmark: 25/25.
- Resolver benchmark: 6/6.
- Architecture validation: 46 native adapters, zero issues.
- Runtime: CPython 3.12.6.

## Frozen100 Regression Attempt

The immutable `.188` Frozen100 artifact still records 69 Exact and its original
same-version 100/100 replay. A `.212` cross-version replay was attempted from
the archived snapshots, but strict request identity stopped on one unconsumed
historical Twitch Greenhouse request:

`GET https://boards-api.greenhouse.io/v1/boards/twitch/jobs?content=true`

This is an inconclusive migration gate, not proof of a product regression and
not a pass. The `.188` result was not overwritten; `.212` execution-level
no-regression for all 69 Exact cannot currently be claimed.

## Decision

The stabilization work is internally consistent and fully offline-green, but
the product goal is not achieved. Fresh100 remains 19/100 Exact, SYSTEM_GAP is
not zero, `.209` same-version replay had one mismatch before the `.210` repair,
and Frozen100 `.212` migration replay is inconclusive. Consuming sealed
holdouts would waste their one-shot value before the development and regression
gates are met, so v2/v3 remain sealed.
