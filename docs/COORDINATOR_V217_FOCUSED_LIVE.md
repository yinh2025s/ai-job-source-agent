# Coordinator `.217` Focused Live

## Scope

The immutable development slice contains Loveland Innovations, iClassPro and
Indica Labs. The clean run used coordinator-v2, run-config schema 1.7, a
10-second provider-search reservation and isolated checkpoint, completion,
evidence, snapshot and replay roots under
`/private/tmp/coordinator-v217-focused3-run1`.

No Fresh100, Frozen100, extension acceptance or sealed blind cohort was run.

## Result

- 3 records completed without worker-contract failure.
- Website: 1/3; Career: 1/3; Job List: 0/3; Exact: 0/3.
- Loveland and iClassPro each executed 5/5 provider queries and 8 tenant probes
  after S2 timeout, proving the S4 reservation released a usable S5 window.
- Their RSS sweeps returned 41 and 50 raw results respectively, with zero
  recognized ATS leads. The existing filters correctly rejected those results.
- Indica reached its verified Career page and found an official BambooHR
  handoff, but the request ended at the company deadline.

Replay record integrity passed 3/3. Outcome replay reproduced Loveland and
iClassPro, but Indica changed from `COMPANY_TIME_BUDGET_EXHAUSTED` to
`NETWORK_TIMEOUT`, so the outcome gate correctly failed with 1 mismatch.

## Decision

The reservation cluster is accepted, but coordinator-v2 is not promoted. `.218`
must add bounded secondary search for all-empty RSS buckets and persist typed
budget ownership so replay cannot issue requests that live skipped. The same
three records must then rerun from clean roots before cohort expansion.
