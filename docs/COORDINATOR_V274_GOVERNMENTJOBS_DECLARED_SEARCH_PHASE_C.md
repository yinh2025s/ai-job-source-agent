# v274 GovernmentJobs Declared Search Phase C

Date: 2026-07-28
Implementation commit: `42b6bba`
Product adapter: `2026-07-28.274`
Decision: **rejected and rolled back; 0/3 expected recoveries**

## Focused Gate

The frozen three-record input was
`samples/evaluation/v274_governmentjobs_declared_search_focused.json`. Live
state was isolated below:

`/private/tmp/v274-governmentjobs-focused-run1`

The run began with three pending records, zero restored completions and zero
retryable resubmissions. Code remained frozen throughout live and automatic
replay.

| Employer | Verified board | Exact | Terminal |
| --- | --- | --- | --- |
| City of Lubbock | `governmentjobs/lubbock` | No | `SERVER_ERROR` |
| City of College Station | `governmentjobs/cstx` | No | `SERVER_ERROR` |
| State of Hawaiʻi | `governmentjobs/hawaii` | No | `SERVER_ERROR` |

Live completed 3/3 with three verified Job Lists and zero openings. Wrong URL,
wrong location, cross-company and cross-tenant publication remained zero
because no opening was published.

The same-version `.274` replay bundle passed:

- 3/3 selected, exported and replayed;
- 3 reproduced;
- 0 mismatch;
- 0 fixture gap;
- record-integrity gate passed.

Replay correctness reproduces the failed business outcome and does not count as
cluster recovery.

## Shared Root Cause

The new interaction was discovered and dispatched for all three tenants. The
live request identity contains the interaction fingerprint, proving this was
not an undiscovered-form or provider-routing failure.

All three browser attempts failed before filling or clicking:

```text
job-search form is unavailable
```

The frozen interaction used `form_ordinal=3`, derived from the static HTML
parser. The browser DOM contains additional login and OAuth forms after
JavaScript initialization, while the search form still exists with the same:

- class `search-form`;
- input name `keyword`;
- input id `keyword-search-input`;
- same-origin `data-action`;
- Search control.

Therefore the stable semantic identity survived, but the positional identity
did not. The shared causal path is:

```text
static parser form ordinal
-> browser initializes a different form sequence
-> ordinal lookup misses the search form
-> safe interaction aborts before click
-> guessed static XHR still returns HTTP 500
```

This is the same trigger and production path for all three independent
tenants. It is not a tenant or company-specific failure.

## Review Findings Closed Before Live

Pre-live review found and corrected two safety defects:

1. incomplete rendered inventory could otherwise be overwritten by a later
   empty static response;
2. `data-action` was initially absent from the interaction fingerprint and
   browser pre-click revalidation.

Focused tests proved those defects closed. They did not solve the separate
ordinal instability observed only in the real browser DOM.

## Decision

The Phase A acceptance threshold required at least three complete-inventory
recoveries. `.274` recovered zero, so the implementation is rejected rather
than being described as partial closure.

The `.274` behavior commit is rolled back. The next Phase A may retain the
three-company cluster but must replace positional form identity with a stable,
unique, exact form marker bound together with the existing field locator and
declared action. It must not search by arbitrary CSS supplied by page content,
accept multiple matching forms or add tenant-specific ordinals.
