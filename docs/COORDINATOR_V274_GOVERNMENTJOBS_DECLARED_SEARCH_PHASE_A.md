# v274 GovernmentJobs Declared Search Phase A

Date: 2026-07-28  
Base commit: `ce295c8`  
Product adapter: `2026-07-27.270`  
Decision: **qualified for one bounded Phase B implementation**

## Cluster Contract

This cluster contains three independent employers and three distinct verified
GovernmentJobs tenants:

| Employer | Tenant | Target | Existing terminal |
| --- | --- | --- | --- |
| City of Lubbock | `lubbock` | Information Security and Compliance Analyst | `SERVER_ERROR` |
| City of College Station | `cstx` | HR Operations and Services Manager | `SERVER_ERROR` |
| State of Hawaiʻi | `hawaii` | REGISTERED NURSE II, III, IV, V, VI | `SERVER_ERROR` |

Lubbock and College Station come from the current Fresh100 cold artifacts.
State of Hawaiʻi comes from the non-sealed v245 development artifacts. All
three records already have a verified GovernmentJobs board and tenant before
S6. This change does not inspect or consume a sealed holdout.

The exact sanitized board-page evidence inspected in Phase A is frozen by
SHA-256:

| Tenant | Page SHA-256 |
| --- | --- |
| `lubbock` | `c983bbcc81c6e26ff65720004f32c3ba761cc5d4c863ac318191df261bb67c1a` |
| `cstx` | `23520f7da8a32f3f24a2de6536b555bad1c09759d4019794ae6744813620be7e` |
| `hawaii` | `30e2134e5a9042ecb9ee242e6b13f8b5bdfbda4c23374d27a19b13b994037562` |

Running the production parser against those three exact blobs produces zero
ordinary GET actions and one bounded interaction per tenant. Each interaction
binds the normalized declared action
`https://www.governmentjobs.com/careers/Home/SearchByKeyword` into its request
fingerprint.

## Shared Trigger And Code Path

The three saved board pages have the same observable transport declaration:

- canonical `https://www.governmentjobs.com/careers/{tenant}` board;
- exact `data-agency-folder-name="{tenant}"` page identity;
- one visible input named `keyword`, with id `keyword-search-input`;
- a same-origin `data-action="/careers/Home/SearchByKeyword"`;
- a Search submit control;
- a JavaScript-populated `job-list-container`.

The existing generic form parser classifies each form as a normal GET action
because the form omits an explicit `action` and HTML defaults its method to
GET. The declared JavaScript transport on the input is ignored. The native
GovernmentJobs adapter then requests a guessed
`?sort=PositionTitle%7CAscending` XHR, which returns HTTP 500 for all three
tenants. The generic fallback only tries guessed `q`, `search` and `query`
parameters; all saved responses remain JavaScript shells with no opening
links.

This is one causal cluster:

```text
field-declared same-origin JavaScript search
-> parsed as ordinary GET form
-> no bounded browser interaction emitted
-> guessed static inventory request returns 500
-> S6 remains incomplete
```

The trigger, parser path, provider adapter path and expected correction are
shared across three independent companies.

## Implementation Boundary

Phase B may change only:

- `job_source_agent/browser_interaction.py`;
- `job_source_agent/job_search_actions.py`;
- `job_source_agent/opening_matcher.py`;
- `job_source_agent/rendered_fetcher.py`;
- `job_source_agent/providers/governmentjobs.py`;
- their focused tests;
- adapter version and release documentation owned by the main line.

The generic parser may treat a form as interactive only when the query field
itself declares one unambiguous, normalized, same-origin `data-action`.
Sensitive fields, cross-origin actions, multiple search fields, ambiguous
buttons and missing job context must continue to fail closed.
The normalized declaration must be part of the interaction fingerprint and
must be revalidated against the live DOM immediately before filling or
clicking.

The GovernmentJobs adapter may execute at most one discovered interaction for
a non-empty target title. Its output is only inventory evidence after all of
the following hold:

1. the rendered final URL remains the same canonical tenant board;
2. the page identity remains the same tenant;
3. every opening URL is canonical and belongs to that tenant;
4. the displayed total is unique and equals the number of parsed openings;
5. duplicate job IDs, pagination truncation and JavaScript shells remain
   incomplete rather than becoming verified no-match.

The existing static JSON/HTML transport remains as a compatibility fallback.
No company name, domain exception, tenant, LinkedIn job ID or expected opening
URL may appear in production logic.

## Acceptance

Focused offline tests must prove:

- all three saved form shapes produce one interaction and no guessed GET
  action;
- a rendered, title-filtered same-tenant result becomes complete inventory;
- unsupported interaction capability falls back without crashing;
- cross-origin `data-action`, cross-tenant render, duplicate IDs, incomplete
  counts and unchanged shells fail closed;
- existing complete JSON and HTML fixtures remain accepted.

Focused live must run only the three cluster records with fresh, isolated
state. Expected recovery is three evidence-backed terminals, with Exact
published only when title, location, provider, tenant and canonical opening
identity pass S7. Closed or no-longer-listed jobs may end as evidence-backed
non-Exact, but do not count as implementation recovery without complete
inventory evidence.

Same-version replay must reproduce all three records with zero mismatch,
fixture gap, tape divergence or missing snapshot boundary. A focused success
must not rewrite the unified Fresh100 score.

## Rollback

Revert the parser and provider changes together if fewer than three records
gain complete inventory evidence, if the interaction cannot be replayed, or if
any wrong URL, wrong location, cross-company or cross-tenant publication
appears. Such a result means this cluster definition or transport contract was
insufficient and must return to Phase A.
