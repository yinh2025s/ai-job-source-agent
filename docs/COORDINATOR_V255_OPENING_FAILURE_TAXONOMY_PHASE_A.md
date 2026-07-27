# v255 Opening Failure Taxonomy - Phase A

## Hypothesis

Typed `FetchError` metadata is lost when generic opening search records a
non-interactive fetch failure. Live execution therefore reduces a real
`COMPANY_TIME_BUDGET_EXHAUSTED` error to its message string and later
reclassifies it as generic `FETCH_FAILED`. Outcome-tape replay retains the typed
reason and returns `COMPANY_TIME_BUDGET_EXHAUSTED`, producing a deterministic
live/replay terminal mismatch.

This is a failure-taxonomy and replay contract defect. It is not a provider,
company, title or URL heuristic.

## Recovery Cohort

Frozen input: `/private/tmp/v255-opening-reason-input.json`

- SHA-256:
  `f46f2acaf8e32c9eadee0cc018ac0b8250272ee162c48772305aca8000f0634a`;
- independent companies: 4;
- Barstool Sports;
- Ichor Systems, Inc.;
- i-Pharm Consulting;
- Plaid.

All four reach a verified first-party or generic Job List and execute the same
generic opening-search path. Their live trace records:

```text
error = "company time budget exhausted at caller deadline"
availability reason = FETCH_FAILED
```

Their scoped replay consumes the captured typed failure and returns:

```text
availability reason = COMPANY_TIME_BUDGET_EXHAUSTED
```

The mismatch appears in two independent development cohorts, v252 and v254.

## Proposed Contract

Every `FetchError` converted into opening-discovery trace data must preserve,
when present:

- canonical `reason_code`;
- `retryable`;
- HTTP `status`;
- transport phase.

The human-readable message remains diagnostic text only. Availability
aggregation must prefer typed metadata and use text classification solely as a
legacy fallback.

No identity, provider, tenant, title, location, opening publication or S7 rule
changes.

## Focused Acceptance

1. Add unit tests proving typed budget/network/provider reasons survive generic
   search and availability aggregation.
2. Run the four frozen records with clean `.255` roots.
3. Require live and replay to agree on the same typed terminal for 4/4.
4. Require zero Exact publication for these records unless the ordinary
   pipeline independently verifies an opening.
5. Require zero fixture gap, tape divergence or missing snapshot boundary.
6. Regress existing opening matcher, availability, outcome-tape and replay
   tests.

## Rollback

Revert the trace metadata preservation if it changes provider selection,
candidate ranking or publication behavior. A reason-code-only change must not
create or remove an opening URL.
