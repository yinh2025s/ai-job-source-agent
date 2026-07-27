# v269 HMG Search-Entry Request Identity - Phase C

## Decision

Behavioral and HMG-specific gates passed; artifact privacy closure superseded
by `.270`.

The `.266` title/location defect, `.267` page-body privacy defect and `.268`
search-entry request-identity divergence are now closed without changing
matcher thresholds, stage scheduling, company identity, provider/tenant
continuity or S7 rules.

A later read-only review found an unrelated `mapsApiKey` credential in the
Kavaliro homepage snapshot and scoped tape. `.269` remains valid HMG behavior,
identity and ticket evidence, but the artifact set is not shareable. `.270`
owns the generic prefixed API-key sanitation and final closure.

## Focused Live

Final isolated run:

`/private/tmp/v269-haley-focused-run1`

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

## Exact Audit

Kavaliro:

- source company and hiring entity: `Kavaliro`;
- target and selected title: `Quality Engineer`;
- target and selected location: `Jacksonville, FL`;
- provider: `haley_marketing`;
- tenant: `custom:jobs.kavaliro.com`;
- board: `https://jobs.kavaliro.com`;
- canonical opening:
  `https://jobs.kavaliro.com/jb/Quality-Engineer-Jobs-in-Jacksonville-Florida/14172225`;
- S7 verdict: `verified`;
- location classification: `exact`.

## Replay

All three scoped outcome tapes exported and replayed under `.269`:

- replayed: 3/3;
- reproduced: 3;
- expected transition: 0;
- budget recovery: 0;
- fixture gap: 0;
- mismatch: 0;
- tape divergence: 0;
- record-integrity gate: passed;
- outcome gate: passed.

## Privacy Audit

The final live, snapshot, trace, checkpoint and replay artifacts were audited
separately:

- HMG inventory URL ticket fields: 54, invalid or raw: 0;
- HMG board/search HTML ticket fields: 26, invalid or raw: 0;
- HMG inventory refresh ticket fields: 6, invalid or raw: 0.

HTML and inventory bodies contain only deterministic inert, shape-preserving
values. Generic fields named `h` or `t` remain semantic and are covered by
negative tests.

## Offline Gates

- focused provider/request/snapshot/tape/job-board/registry/checkpoint tests:
  122/122;
- deterministic provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 48 native adapters, 0 issues;
- `git diff --check`: passed.

The full test suite was intentionally not run for this focused cluster. It
remains a release integration gate.

## Frozen Product Hashes

The live run used:

- `snapshot.py`:
  `c462e078baa82bcee6dbe53d4c71d255ae51c1b08ab496175fc77add4e36f8e3`;
- `haley_marketing.py`:
  `43add8092457c9593ae13ccb57cc59ee8c4adfcb9c6ffc29566d7b1ba8286636`;
- `job_board.py`:
  `54d1629e12c208e70f3aaafb2d6c39886870571843aad0f275af8df01792fefa`;
- `request_identity.py`:
  `b068ce3756dc1a0b3734c62cc06678c8775b59f20743b9bbbff5c28abcef7900`;
- `checkpoint.py`:
  `59f83edd7705b5a8e45285eb40871ecdb582568bc48cfa35991ceff29c2a2bb3`.

## Follow-up

HMG-specific behavior is accepted at `.269`; final artifact privacy and
provider closure move to `.270`.

Plugin work, authenticated External Apply, coordinator-v2, LLM and sealed
holdouts remain frozen.
