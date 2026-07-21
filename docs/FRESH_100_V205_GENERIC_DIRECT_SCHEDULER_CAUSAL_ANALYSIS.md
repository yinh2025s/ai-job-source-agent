# Fresh100 `.205` Generic Direct Scheduler Phase A

Date: 2026-07-21

## Root Cause

S5 currently treats any successful legacy Website/Career execution as
authoritative and skips the provider-search wave. That is correct for a typed,
verified provider board and for a first-party page with verified opening
inventory. It is not correct for an untyped `generic` Career/Jobs page whose
inventory has not been verified.

The shared trigger is:

1. direct provider candidates are absent or unverified;
2. the legacy Website/Career route returns `success`;
3. its selected provider is absent/`generic`;
4. its trace contains no verified nonempty first-party listing inventory;
5. the scheduler records `verified_website_direct_candidate` and never runs the
   search wave.

This is one common condition and one common return path in
`JobBoardDiscoveryStage._from_candidate_portfolio`. It is not a stage-label
cluster.

## Affected Independent Companies

The immutable `.202` Fresh100 evidence confirms the trigger for at least seven
independent companies:

- Tyler Technologies: a Jobvite Talent Network form was promoted as a generic
  board; no opening inventory was verified.
- The Home Depot: a valid CWS page was not typed after an empty `sortby` reset;
  the dynamic shell had no verified inventory.
- Necessary Ventures: a Consider portfolio shell was treated as a generic
  board and suppressed provider search.
- OneApp: a first-party page declared a Pinpoint widget/tenant, but the generic
  Career result returned before search could produce the board.
- Crawford Thomas Recruiting: a Bullhorn iframe was observed but not traversed;
  the outer generic page contained no verified inventory.
- Crosby: partial first-party rows eclipsed an explicit ADP handoff, while the
  generic inventory remained incomplete.
- Mayo Clinic: a templated Oracle route/custom Eightfold shell degraded to a
  generic page and suppressed alternative provider discovery.

Each company has additional local parser/provider work. This scheduler change
does not claim those local defects are one cluster and does not predeclare any
new Exact result.

## Contract

A successful Website/Career direct result may suppress search only when at
least one condition holds:

- the final projected provider is typed and non-`generic`; or
- the trace contains verified, nonempty, first-party opening inventory.

Otherwise the Website/Career result remains a fallback candidate, search runs,
and the normal provider-candidate relationship/identity gates decide whether a
typed board is usable. If search produces no verified candidate, the original
generic result is preserved.

## Negative Safety Cases

- A typed Lever/Greenhouse/Workday/etc. direct handoff must still skip search.
- A generic first-party page with verified nonempty title/URL inventory must
  still skip search.
- An untyped page with only Career semantics, an empty shell, a talent-network
  form, or unverified/empty inventory must not suppress search.
- Search candidates still require provider adapter validation and hiring
  relationship evidence; ranking alone cannot produce success.
- No company, domain, tenant or job identifier branch is allowed.

## Phase C Expectation

Focused replay must show the search wave running for at least three independent
affected companies while preserving the legacy fallback when search finds
nothing. Full tests, provider/resolver benchmarks, architecture validation and
Fresh100 replay must retain zero wrong URL, cross-company or cross-tenant
acceptance. Live results are evaluated only after the deterministic replay gate.
