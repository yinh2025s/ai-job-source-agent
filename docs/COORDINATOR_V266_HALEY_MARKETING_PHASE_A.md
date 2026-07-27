# v266 Haley Marketing Inventory - Phase A

## Scope

This backend-only iteration addresses one provider-family contract shared by
three independent companies. Plugin work, authenticated External Apply,
coordinator-v2, the LLM branch and sealed holdouts remain frozen.

No company name, company domain, LinkedIn job ID or target opening is added to
production code.

## Causal Cluster

The following records reached an official public job board but the generic S6
path could not read its JavaScript-loaded inventory:

| Company | Target | Prior terminal | Official board |
| --- | --- | --- | --- |
| Madison-Davis, LLC | Recruiter; New York, NY | `JOB_BOARD_NOT_FOUND` | `https://careers.madisondavis.com/` |
| Top Prospect Group | Business Analyst; New Jersey | `OPENING_DISCOVERY_INCOMPLETE` | `https://jobs.topprospectgroup.com/` |
| Kavaliro | Quality Engineer; Jacksonville, FL | `OPENING_DISCOVERY_INCOMPLETE` | `https://jobs.kavaliro.com/` |

All three pages expose the same Haley Marketing / HMG Job Board contract:

- `/css/hmg-jb.css`;
- `/js/combobo.js`;
- `JBSearchList_form` with `arg=list_posts`, `pid=gwt` and public request
  ticket fields;
- a same-origin `/json/index.smpl` inventory endpoint;
- canonical detail routes shaped as `/jb/{SEO_PERMALINK}/{POST_ID}`.

This is a common observable trigger, a common provider inventory code path and
a three-company recovery set. It is not a shared stage label.

## Focused Live Evidence

Direct anonymous calls to the page-declared inventory endpoint on 2026-07-27
produced:

- Kavaliro: an active `Quality Engineer` record in `Jacksonville, FL`, with
  `POST_ID=14172225` and provider-published permalink evidence;
- Top Prospect Group: a complete target-title query with `total=0`;
- Madison-Davis: a complete target-title query with `total=0`.

The expected focused outcome is therefore one S7 Exact and two evidence-backed
no-match terminals, subject to the ordinary pipeline availability projection.
The implementation must not fabricate the two absent openings.

The provider emits a known non-empty response variant that ends after the
`ResultSet.list` array without the final two object braces. Repair is allowed
only when the bounded response has the exact HMG envelope prefix, ends at the
complete list array, and becomes valid structured JSON by appending exactly
those two braces. Every other malformed response fails closed as
`INVALID_STRUCTURED_DATA`.

## Implementation Boundary

Add one auto-discovered `haley_marketing` adapter:

1. Page-aware detection requires the combined HMG form, stylesheet/script and
   inventory endpoint evidence.
2. Tenant identity is the HTTPS custom board host captured in a replay-safe
   board locator.
3. `list_jobs` refetches the board to obtain a current public ticket, then
   performs bounded title-filtered pagination against the same-origin JSON
   endpoint.
4. Candidate URLs are constructed only from validated `SEO_PERMALINK` and
   positive `POST_ID` fields on that same tenant host.
5. Exact-title discovery may stop pagination early. A no-match result is
   complete only when the provider reports the full filtered result set.
6. Cross-host redirects, credentials, non-standard ports, duplicate IDs/URLs,
   malformed records and incomplete pagination fail closed.

S5 relationship evidence and S7 company, hiring entity, provider, tenant,
title and location validation remain unchanged.

## Acceptance

1. Unit tests cover three independent page skins, one active target, complete
   empty inventory, pagination, the exact two-brace variant and malformed
   response rejection.
2. Security tests reject cross-host redirect/detail, unsafe URL shapes,
   duplicate records and forged weak page evidence.
3. The three-record focused live runs with fresh isolated state.
4. Same-version focused replay reproduces all three records with zero mismatch,
   fixture gap, extra request or unconsumed tape.
5. Every published Exact passes company, title, location, provider/tenant and
   canonical opening URL audit.
6. Provider benchmark, architecture gate and focused related tests pass.

## Rollback

Remove the adapter module and its tests, restore the preceding adapter version,
and invalidate `.266` checkpoints. No shared stage contract or identity rule
requires rollback.
