# v267 Haley Marketing Review Hardening - Phase C

## Decision

Behavioral gate passed; privacy closure superseded by `.268`.

The review defect from `.266` is closed without changing matcher thresholds,
stage scheduling, company identity, provider/tenant continuity or S7 rules. A
later read-only artifact review found that raw HMG tickets remained inside
replay `page.html` bodies even though URL metadata was clean. `.267` therefore
remains valid behavior and identity evidence, but is not the final privacy
closure. See `.268` Phase A and Phase C.

## Product Changes

The `haley_marketing` adapter now:

- continues bounded pagination when an exact title is found in the wrong
  location;
- stops early only after exact normalized title and exact normalized location
  when the source posting supplies a location;
- requires provider-published `POST_SEO_URL`;
- verifies exact HTTPS tenant, `/jb/{slug}/{POST_ID}`, slug and ID continuity;
- rejects cross-tenant, query-bearing and mismatched canonical URLs;
- excludes explicitly archived records;
- records expiration metadata without wall-clock decisions;
- exposes an incomplete candidate set only when the exact target title and
  location have already been found;
- redacts HMG `h/t` tickets from adapter trace, sanitized snapshot request
  identity and replay tapes for the exact
  `/json/index.smpl?arg=list_posts&pid=gwt` contract.

Adapter version:

`2026-07-27.267`

## Focused Live

Final isolated run:

`/private/tmp/v267-haley-focused-run2`

| Company | Final result |
| --- | --- |
| Madison-Davis, LLC | verified Haley Job List + `OPENING_NOT_FOUND` |
| Top Prospect Group | verified Haley inventory + `OPENING_NOT_FOUND` |
| Kavaliro | S7 Exact |

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

Madison-Davis and Top Prospect Group returned complete title-filtered provider
inventory with no matching record. They remain honest verified no-match
outcomes and are not converted to fabricated opening URLs.

## Exact Audit

Kavaliro:

- source company and hiring entity: `Kavaliro`;
- official website: `https://www.kavaliro.com`;
- provider: `haley_marketing`;
- tenant: `custom:jobs.kavaliro.com`;
- board: `https://jobs.kavaliro.com`;
- target and selected title: `Quality Engineer`;
- target and selected location: `Jacksonville, FL`;
- canonical opening:
  `https://jobs.kavaliro.com/jb/Quality-Engineer-Jobs-in-Jacksonville-Florida/14172225`;
- S7 verdict: `verified`;
- location classification: `exact`.

## Replay And Privacy

The final run exported and replayed all three scoped outcome tapes under the
same `.267` version:

- reproduced: 3;
- expected transition: 0;
- budget recovery: 0;
- fixture gap: 0;
- mismatch: 0;
- record-integrity gate: passed;
- outcome gate: passed.

An independent recursive audit inspected 54 HMG inventory URL fields across
live snapshots, trace, replay checkpoints and scoped tapes. Every URL `h` and
`t` value was `[redacted]`; raw or missing URL ticket redaction count was zero.
This audit did not cover `page.html` bodies, where the follow-up review found
the remaining privacy defect.

## Offline Gates

- related provider/job-board/registry/checkpoint/request/snapshot tests:
  103/103;
- deterministic provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 48 native adapters, 0 issues.

The full test suite was intentionally not rerun for this focused provider
hardening round. A broader suite remains a release integration gate.

## Follow-up

Behavioral correctness is accepted at `.267`; full provider closure moves to
`.268` after provider-contract-aware body sanitation and a new isolated
live/replay gate.
