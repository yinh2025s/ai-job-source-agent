# Coordinator `.229` S5-S6 Handoff And Typed Budget Phase C

## Decision

The S5-to-S6 handoff and typed provider-error replay defect is fixed. This is
an infrastructure closure, not an Exact-opening recovery claim. Fresh100
aggregate terminal counts remain unchanged.

## Implementation

- GovernmentJobs canonical boards are replay-safe only for exact public
  `https://www.governmentjobs.com/careers/{tenant}` identities whose lowercase
  tenant slug equals the provider identifier.
- The checkpoint stores only provider/tenant/board identity. It stores no
  inventory, opening, token, cookie or private page state.
- A shared `provider_fetch_reason` preserves typed `FetchError.reason_code`
  before using display-string taxonomy. Generic unclassified transport still
  maps to `PROVIDER_FETCH_FAILED`.
- GovernmentJobs, ApplicantPro, Talemetry and PeopleSoft use the shared typed
  contract. Company and cooperative budget reasons no longer degrade to an
  ordinary provider failure.
- The S5 merge no longer republishes unchanged provisional identity, so its
  strict checkpoint remains valid through the split process boundary.

## Gates

- Three canonical GovernmentJobs tenants (`lubbock`, `cstx`, `seattle`) pass
  replay-safe policy tests; wrong host/path/case/identifier and cross-tenant
  locators fail closed.
- Four provider families preserve typed budget reasons.
- Integrated scoped suite: 373 tests passed.
- Provider benchmark: 25/25.
- Resolver benchmark: 6/6.
- Architecture gate: 46 adapters, zero issues.
- `git diff --check`: passed.

## Focused Live And Replay

Artifacts:

- first `.229` Lubbock attempt, upstream network failure:
  `/private/tmp/fresh1-v229-lubbock-20260723-run1`
- second `.229` Lubbock attempt, modified path reached:
  `/private/tmp/fresh1-v229-lubbock-20260723-run2`
- three-tenant cold focused run:
  `/private/tmp/fresh3-v229-governmentjobs-20260723-run1`

On the second Lubbock run, the opening child restored S1-S5 including
`job_board_discovery`; it did not rerun S5. S6 received its full 14.4-second
phase. The current official request still failed, so the safe result is a
verified GovernmentJobs board with retryable opening discovery, not Exact.
The new capture replays 1/1 with zero mismatch, fixture gap or tape divergence.

The three-tenant run reached the GovernmentJobs path for City of College
Station. Lubbock and City of Pharr stopped upstream and are not counted as
provider successes. All three captured outcomes replay 3/3 with zero mismatch
or fixture gap. Across the live runs, no opening URL, cross-company URL or
cross-tenant identity was published.

## Remaining Work

This phase does not solve current external transport failures or missing
upstream candidate production. A native provider request that receives an
ordinary `FETCH_FAILED` may truthfully retain a retryable partial after its S6
window. The next cluster must be selected from at least three companies with a
shared candidate-production or provider transport trigger; increasing the
global company timeout is not this phase's fallback.
