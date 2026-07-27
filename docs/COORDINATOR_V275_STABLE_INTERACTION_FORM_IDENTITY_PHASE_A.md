# v275 Stable Interaction Form Identity Phase A

Date: 2026-07-28
Base: `.274` rejected focused artifacts
Decision: **qualified second and final implementation attempt for this cluster**

## Evidence

The `.274` focused live produced the same browser failure for City of Lubbock,
City of College Station and State of Hawaiʻi:

```text
job-search form is unavailable
```

In all three rendered page snapshots, the GovernmentJobs search form remains
present and preserves the same normalized semantic identity:

```text
form marker: search-form
query name: keyword
query id: keyword-search-input
declared action: https://www.governmentjobs.com/careers/Home/SearchByKeyword
submit text: Search
```

Only the form ordinal differs between the static parser and initialized browser
DOM because browser-only login/OAuth forms alter the sequence. This gives one
shared trigger, one shared browser executor path and three expected recoveries.

## Revised Contract

`JobSearchInteraction` may carry an optional structured `form_marker`. It is
allowed only when discovered from the form's existing id, class, aria-label or
data-testid values. The descriptor includes the attribute name, so values from
different attributes cannot collide. For the field-declared `data-action`
path, the marker is required.

The marker must:

- contain no control characters;
- be bounded in length;
- prefer exact `id`, `data-testid` or `aria-label` values;
- use one deterministic class token when no stronger marker exists;
- enter the interaction fingerprint and replay request identity.

When a marker is present, the browser executor examines at most 32 forms using
fixed attribute names. Exact-value markers must match exactly; class markers
must remain a member of the initialized form's class-token set, allowing class
order changes and additional state classes. Exactly one form must match. The
executor then applies the existing exact query-field, declared-action and
submit-control checks inside that form.

It must fail closed when:

- zero or multiple forms have the marker;
- the query field, `data-action` or submit control changed;
- the declared action is no longer same-origin;
- the result crosses origin or tenant;
- rendered inventory counts are incomplete or contradictory.

No page-provided CSS selector, JavaScript expression, company name, tenant,
opening URL or benchmark ID may enter production behavior. Interactions without
a marker retain the existing ordinal contract unchanged.

## Scope

Phase B may change:

- `job_source_agent/browser_interaction.py`;
- `job_source_agent/job_search_actions.py`;
- `job_source_agent/opening_matcher.py`;
- `job_source_agent/rendered_fetcher.py`;
- `job_source_agent/providers/governmentjobs.py`;
- focused tests;
- adapter version and governance documentation.

The provider candidate, identity, location and S7 publication contracts remain
unchanged.

## Acceptance

Offline tests must prove:

1. a static/browser form-order difference still resolves exactly one form by
   marker;
2. duplicate, absent and changed markers fail before fill or click;
3. marker, field and declared-action changes alter request identity;
4. incomplete interactive inventory cannot become verified no-match;
5. existing ordinal-only interactions remain backward compatible;
6. provider, snapshot and scoped replay tests pass.

The same three records then run once with frozen `.275` code and fresh isolated
state. Acceptance requires all three to gain complete inventory evidence. Exact
is published only if the opening is still present and passes title, location,
company, provider and tenant verification. Same-version replay must reproduce
3/3 with zero mismatch or fixture gap.

If fewer than three records gain complete inventory evidence, this cluster is
reclassified again and no third implementation iteration is permitted without
new independent evidence.
