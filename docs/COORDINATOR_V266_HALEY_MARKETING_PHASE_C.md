# v266 Haley Marketing Inventory - Phase C

## Decision

Superseded by `.267`.

The three-company Haley Marketing / HMG Job Board cluster met its focused
recovery gate and remains valid causal evidence. Post-implementation review
then found that `.266` could stop after an exact title in the wrong location,
so `.266` is not the final accepted provider version. The corrected acceptance
is recorded in
`docs/COORDINATOR_V267_HALEY_REVIEW_HARDENING_PHASE_C.md`.

## Implementation

Added an auto-discovered, page-aware `haley_marketing` provider adapter.

The adapter:

- requires the combined HMG stylesheet, script, form, JSON inventory and
  detail-route contract;
- binds a custom board to the exact public HTTPS hostname;
- uses a replay-safe `custom:{hostname}` locator with a strict central policy;
- refetches the public board to obtain current HMG request fields;
- supports the search-entry POST variant before reading inventory;
- performs bounded title-filtered `/json/index.smpl` pagination;
- validates total/offset/page-size continuity, duplicate IDs and every detail
  slug;
- constructs only same-tenant `/jb/{SEO_PERMALINK}/{POST_ID}` opening URLs;
- stopped early after an exact normalized title, which was later identified as
  insufficient when the source posting included a location;
- reports no match only from a complete filtered inventory;
- fails closed on malformed payloads, redirect drift or incomplete pagination;
- redacts public HMG `h/t` ticket values from normal adapter trace.

The known non-empty HMG response variant missing exactly the final two object
braces is repaired only after the strict provider envelope and complete list
array are present. Empty and otherwise malformed variants remain invalid.

Adapter version:

`2026-07-27.266`

## Focused Live

Final isolated run:

`/private/tmp/v266-haley-focused-run3`

| Company | Before | `.266` result |
| --- | --- | --- |
| Madison-Davis, LLC | `JOB_BOARD_NOT_FOUND` | verified HMG Job List + `OPENING_NOT_FOUND` |
| Top Prospect Group | `OPENING_DISCOVERY_INCOMPLETE` | verified title-filtered HMG inventory + `OPENING_NOT_FOUND` |
| Kavaliro | `OPENING_DISCOVERY_INCOMPLETE` | S7 Exact |

Aggregate:

- Website: 3/3;
- Career: 3/3;
- verified Job List: 3/3;
- Exact: 1/3;
- evidence-backed no match: 2/3;
- wrong URL: 0;
- wrong location: 0;
- cross-company: 0;
- cross-tenant: 0.

The two no-match records returned provider-declared `total=0` for the target
title. They were not converted to fabricated openings.

## Exact Audit

Kavaliro:

- source company and hiring entity: `Kavaliro`;
- official website: `https://www.kavaliro.com`;
- provider: `haley_marketing`;
- tenant: `custom:jobs.kavaliro.com`;
- board: `https://jobs.kavaliro.com`;
- target title: `Quality Engineer`;
- target and provider location: `Jacksonville, FL`;
- opening:
  `https://jobs.kavaliro.com/jb/Quality-Engineer-Jobs-in-Jacksonville-Florida/14172225`;
- S7 verdict: `verified`;
- location classification: `exact`.

## Replay

The run exported and replayed all three scoped outcome tapes under the same
`.266` version. These figures remain valid for the `.266` evidence run, but do
not replace the corrected `.267` acceptance:

- reproduced: 3;
- expected transition: 0;
- budget recovery: 0;
- fixture gap: 0;
- mismatch: 0;
- record-integrity gate: passed.

## Offline Gates

- focused provider/job-board/registry/checkpoint tests: 49/49;
- deterministic provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 48 native adapters, 0 issues.

The full test suite was intentionally not run in this focused provider round.
The live end-to-end path, scoped replay and related contracts were exercised
instead; a broader suite remains an integration gate before a release commit.

## Residual Notes

Top Prospect Group keeps its earlier generic provider identity at the top-level
partial result because there is no selected opening to publish. Its S6 trace
nevertheless records page-evidenced `haley_marketing`, complete filtered
inventory and zero candidates. This projection difference does not change the
verified no-match terminal and does not justify a shared identity-rule change.

Plugin work, authenticated External Apply, coordinator-v2, the LLM branch and
sealed holdouts remain untouched.
