# `.224` SuccessFactors Detail Continuity Phase C

## Result

The code-frozen three-company cold run is preserved at
`/private/tmp/fresh3-v224-successfactors-20260722-run2`.

| Company | Terminal | Verified result |
| --- | --- | --- |
| Arkema | Exact | Beaumont opening `1401455133`; SuccessFactors tenant `custom:ARKEMA`; exact title and location |
| Cintas | Exact | Fort Myers opening `1373711200`; SuccessFactors tenant `custom:cintasP2`; exact title and location |
| Aramark | Identity rejected | Official inventory returned an Indianapolis opening, but the durable S5 checkpoint did not retain the transient discovered-board object required by the declared-inventory S7 helper |

The live run completed with 3/3 Website, Career, Job Board and opening-match
stages, 2/3 S7 Exact, and zero published wrong URL, wrong location,
cross-company or cross-tenant result. Same-version replay reproduced 3/3
outcomes and its outcome gate passed. The one-record automatic failure bundle
also replayed successfully.

## Implemented Contract

- SuccessFactors verifies at most three exact-title detail pages.
- A detail must preserve candidate canonical URL and board host/tenant
  continuity, and publish one bounded JobPosting company and location.
- One provider-owned terminal `Job` display token is normalized only inside
  the SuccessFactors adapter.
- A relationship-verified generic identity can be promoted only from a
  complete native-adapter trace that binds the selected opening, provider,
  tenant and canonical board.
- Provider inventory evidence URLs and legacy replay identity URLs are
  canonicalized before strict identity objects are constructed.

The related scoped gate passes 308 tests. A truncated Cintas capture remains
rejected; the successful live used a complete 89,119-byte detail response.

## Remaining Aramark Boundary

Aramark's first-party JSON endpoint returned the exact title and location and
an official cross-host SuccessFactors URL. The S5 checkpoint retained the
verified generic provider identity but not `discovered_job_board`; after the
phase boundary, `_trace_binds_declared_inventory()` therefore rejected the
otherwise continuous trace. This is not a SuccessFactors parsing failure.

The current development artifacts contain only one company with this exact
`verified_declared_inventory -> cross-host opening -> restored checkpoint`
shape. Under the three-company cluster rule, no gate is relaxed yet. The next
Phase A must find at least three independent examples or retain the current
fail-closed behavior.

## Decision

The SuccessFactors native-detail implementation is accepted for Arkema and
Cintas. The original three-company cluster is not declared fully closed
because Aramark remains rejected. Full Fresh100/Frozen100 and sealed holdouts
were not run in this focused phase.
