# Coordinator `.233` Captured Producer-State Replay Phase C

## Result

`.233` closes the record-local scoped replay producer-state gap. When captured
checkpoint events prove that live resumed at S5 and S5 explicitly read
`stored_verified_provider_board`, replay may reconstruct the exact upstream
Website and Career evidence before restoring that provider board.

Reconstruction requires:

- successful authoritative S2 and S4 outputs;
- exact company and LinkedIn identity;
- exact Website and Career URL continuity;
- an exact first-party relationship URL;
- provider, tenant and canonical board agreement with the adapter.

The original immutable evidence objects are copied, preserving source,
verification method and observation time. Missing or conflicting prerequisites
now raise `captured provider producer state is missing or ambiguous` before
OutcomeTape execution.

## Tests

The final integrated backend slice passes 643 tests, including:

- Focus-shaped captured S5 positive reconstruction;
- Website/Career discontinuity rejection;
- missing stored-read marker rejection;
- cross-tenant and non-stored source rejection;
- same-company posting isolation;
- batch-final evidence leakage prevention.

The deterministic provider benchmark passes 25/25, resolver benchmark 6/6 and
the architecture validator reports 46 native adapters with zero issues.

## Replay Validation

An existing legitimate Versana capture from the diagnostic `.230` run was
replayed with `.233`:

- captured stored Lever board restored;
- exact opening, title, provider and tenant reproduced;
- 1/1 reproduced;
- zero mismatch and zero fixture gap;
- no unconsumed tape entry.

Artifact:

`/private/tmp/versana-v233-stored-replay`

The historical Focus capture contains a provider board whose relationship URL
points to the Website rather than the captured Career page. `.233` rejects that
polluted state during preflight with the explicit producer-state error. It no
longer reaches the prior late `unconsumed Ashby request` failure.

A regression replay of the final `.232` four-record capture also remains 4/4
reproduced with zero mismatch or fixture gap:

`/private/tmp/focus4-v233-replay-regression`

## Projection

This deterministic replay repair does not change Fresh100 terminal counts. The
development projection remains 29 Exact, 9 Verified No Match, 1 External
Blocked and 61 unresolved.
