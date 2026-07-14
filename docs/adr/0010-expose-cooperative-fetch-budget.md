# ADR-0010: Expose Cooperative Fetch Budget To Paginated Providers

Status: Accepted

Date: 2026-07-14

## Context

The live runner enforces a hard per-company process deadline and gives the inner
fetch stack a slightly earlier deadline. `RetryingFetcher` prevents requests
after that inner deadline and clamps each socket timeout, but provider adapters
could not see the remaining budget. A paginated adapter could therefore start a
final request with only a few seconds left, consume all publication time, and be
terminated before returning a valid partial inventory or stage checkpoint.

The frozen 30-company cohort reproduced this with Akkodis: Sitecore published
the durable career and job-list stages, then the final inventory page consumed
the remaining S6 budget. The parent correctly recovered S4/S5, but the adapter
never had a chance to publish its 80 already verified records as an incomplete,
retryable result.

## Decision

`FetchClient` remains the minimal required network interface. A separate,
runtime-checkable `FetchBudget` capability exposes only
`remaining_fetch_seconds()`:

- `None` means no cooperative deadline is configured.
- Bounded clients return a non-negative duration.
- Absolute monotonic deadlines and clock domains are not exposed.

`RetryingFetcher` implements the capability. Cache and snapshot wrappers define
explicit forwarding methods because Python 3.12 runtime protocol checks use
static attribute lookup and do not discover capability methods through
`__getattr__`. The capability therefore remains visible through the production
fetch stack without expanding every fixture or browser client.

Provider infrastructure supplies shared reserve helpers. Before starting a
subsequent pagination request, an adapter may require the current request
timeout plus a small publication reserve. If the remaining budget is not
sufficient, it must stop cooperatively and return:

- `FETCH_BUDGET_EXHAUSTED`
- `retryable=true`
- all already verified candidates
- `inventory_complete=false`
- a stable stop cause in trace

When request-aware snapshots are active, the shared reserve guard records the
rejected request as a terminal `FETCH_BUDGET_EXHAUSTED` outcome before returning
the partial result. The request is not sent. Its sanitized URL, method, body
digest and allowlisted semantic headers use the same ADR-0006 identity contract
as real fetch failures. This lets offline replay reproduce the budget boundary
without persisting remaining wall-clock time or rebuilding a request from trace.

The adapter may still use positive candidates from an incomplete inventory. It
must not use an interrupted inventory to assert an authoritative empty result,
no-match, closed opening, or absence of public jobs.

The first provider migration is Sitecore/Next JobSearch because the frozen live
failure supplies direct evidence. Other paginated providers migrate only when
provider tests or live failure clusters justify the change; they share this
contract instead of creating provider-specific deadline APIs.

## Consequences

- Paginated providers can publish useful partial evidence before the hard kill.
- Existing `FetchClient` implementations and fixtures remain unchanged.
- Provider recall can decrease near a deadline because an unfetched final page
  may contain the target opening; the result remains explicitly retryable and
  incomplete instead of becoming a false negative.
- Reserve policy is based on deterministic timeout configuration and a small
  publication allowance. Volatile remaining-time values are not durable trace
  or checkpoint identity.
- Full live benchmarks remain serialized. Offline contract and provider tests
  verify reserve behavior without wall-clock sleeps.

## Implementation Note: Iteration .69

The frozen-cohort follow-up showed that a first native inventory request can
also consume the publication reserve, and that an interrupted native result
must not start generic fallback work. Sitecore therefore applies the same
request-timeout-plus-publication-reserve guard before every page, including the
first. The opening matcher treats a native `FETCH_BUDGET_EXHAUSTED` result as a
cooperative terminal for the current attempt: positive candidates remain
usable, but a miss stays retryable and no generic fanout is started. Career
search uses the optional capability only to stop new source requests; it does
not persist remaining time or alter deterministic run identity.
