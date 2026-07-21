# Fresh100 `.205` Generic Direct Scheduler Phase C

Date: 2026-07-21

## Experiment

`.205` changed staged S5 scheduling so an untyped generic Website/Career result
without verified nonempty same-site inventory remained a fallback while the
provider-search wave ran. Typed providers and verified generic inventory still
suppressed search. No company/domain/tenant/job identifier branch was added.

The focused live cohort used seven independent companies from the immutable
`.202` Fresh100 evidence:

- Tyler Technologies
- Mayo Clinic
- The Home Depot
- Necessary Ventures
- OneApp
- Crawford Thomas Recruiting
- Crosby

Run root:
`/private/tmp/fresh100-v205-generic-direct-focused-run1`

All checkpoint, completion, snapshot, evidence, result and replay paths were
new. No `.202` completion or cache was restored.

## Live Result

| Metric | Result |
| --- | ---: |
| Records | 7 |
| Website | 3 |
| Career page | 2 |
| Job list | 4 |
| Exact opening | 0 |
| Network timeout reason occurrences | 5 |

The scheduler transition was observable for four records that reached S5:

- The Home Depot and Crawford Thomas continued search after insufficient generic
  direct evidence, but did not recover Exact.
- OneApp and Crosby selected same-name Ashby search boards and returned
  `OPENING_NOT_FOUND`; neither was Exact and neither established the intended
  first-party Pinpoint/ADP route.
- Tyler, Mayo and Necessary were blocked earlier by transport/budget outcomes.

The search boards did not create a wrong Exact because S7 remained closed, but
they displaced stronger first-party route provenance and produced terminal
no-match claims from the wrong competing route. This is not an acceptable
precision/semantics trade.

## Replay

The same-version capture produced a complete replay bundle:

- selected/exported/replayed: 7/7;
- reproduced: 6;
- existing budget recovery: 1;
- fixture gap: 0;
- mismatch: 0;
- record integrity: passed.

Manifest SHA-256:
`484950a57f85456be2c867143b46b2142d633a9b3f86462cd27242cc35a9297c`.

## Decision

The experiment fails its product acceptance gate: zero Exact recoveries and two
weaker/wrong-route terminal no-match claims. The broad scheduler cluster is not
closed. `.206` removes the `.205` behavior and restores `.204` scheduling.

The retained architectural lesson is narrower: generic first-party and typed
search/provider routes must coexist until S6 verifies their inventories and S7
selects a continuous identity chain. A search result cannot replace a verified
first-party route at S5 merely because its tenant string resembles the company
name. Future work must implement route-outcome competition or strengthen hiring
relationship evidence before retrying this cluster.
