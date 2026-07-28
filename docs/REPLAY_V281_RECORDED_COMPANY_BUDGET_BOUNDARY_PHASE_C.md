# `.281` Recorded Company-Budget Boundary Phase C

Date: 2026-07-29

Release adapter: `2026-07-29.281`

Decision: **accepted replay-determinism fix; full product goal remains open**

## Implementation

Scoped replay now restores a recorded outer company-budget terminal after the
captured tape has been fully consumed when:

- source first fails with `COMPANY_TIME_BUDGET_EXHAUSTED`;
- replay first fails in the same stage;
- replay's semantic failure is non-retryable;
- the authoritative upstream identity prefix is unchanged;
- no expected transition is declared.

The source public fields and typed stage outcomes at and after that boundary
are restored. Replay run configuration and record identity remain current.
The unprojected replay outcome and stages are retained under:

```text
trace.replay.recorded_company_budget_boundary
```

Retryable transport drift, fixture gaps, identity drift and later-stage
advancement remain unprojected. The live pipeline, providers, matching and S7
identity gates did not change.

Legacy/scoped bundle schemas advance from `5/7` to `6/8`. The manifest and
summary expose the projected-boundary count.

## Focused Fresh100 Replay

Source:

`/private/tmp/fresh100-current-v278-cold-20260728-run1`

Accepted `.281` output:

`/private/tmp/fresh100-current-v281-recorded-company-budget-focused-run2`

| Metric | Result |
| --- | ---: |
| Selected records | 2 |
| Recorded boundaries projected | 2 |
| Reproduced | 2 |
| Budget recovery | 0 |
| Mismatch | 0 |
| Fixture gap | 0 |

Diamondback Energy and State of Montana reproduce their original
Career-stage `COMPANY_TIME_BUDGET_EXHAUSTED` terminal. Their underlying
same-stage `CAREER_PAGE_NOT_FOUND` replay outcome remains in trace.

## Current-Version Negative Controls

Source:

`/private/tmp/frozen100-current-v280-cold-20260728-run1`

Accepted `.281` output:

`/private/tmp/frozen100-current-v281-recorded-company-budget-focused-run2`

Two Haystack records and one Randstad USA record already reproduced their
Opening-stage company-budget terminal without projection:

| Metric | Result |
| --- | ---: |
| Selected records | 3 |
| Independent companies | 2 |
| Recorded boundaries projected | 0 |
| Reproduced | 3 |
| Budget recovery / mismatch / gap | 0 / 0 / 0 |

This proves the new projection does not rewrite an already deterministic
company-budget terminal.

## Cross-Company Diagnostic Evidence

The unsealed `.255` diagnostic capsule had five independent
`company_budget_replay_normalized` records after a successful scoped replay:

- STARK BANK;
- Chipply;
- Storyteller Overland;
- Eaton;
- 3M.

Current production projection and outcome-gate code was applied to those five
serialized source/replay pairs. It projected 5/5 and produced:

```text
5 reproduced
0 budget recovery
0 mismatch
0 fixture gap
```

A current-version full tape replay of that historical capsule was also
attempted, but the existing recorded Career transport-reservation parity gate
rejected the old configuration. The parity check was not weakened, and this
report does not call that historical full replay current-version compatible.

## Full-Capsule Boundary

A full current-code replay of the `.278` Fresh100 capsule was attempted from a
new output root. It stopped on an unrelated WalkMe tape divergence because
`.280` changed visible-detail behavior after the `.278` source capture.

Therefore:

- the two affected Fresh100 records are proven fixed by focused scoped replay;
- the immutable `.278` full result is not rewritten;
- no `.281` Fresh100 `100/100` replay is claimed;
- Versana and Brown and Caldwell remain separate replay mismatches;
- a new same-version full live/replay gate is still required later.

## Product Causal Audit

Two independent read-only audits reclassified all current unresolved Fresh100
records before and after Job Board verification. Neither found a new product
recall cluster satisfying one trigger, one production path, at least three
independent companies and at least three evidenced terminal recoveries.

Notable non-qualifying groups:

- repeated official-host 403: six companies, but zero durable provider
  relationship evidence and zero safe terminal recoveries;
- GovernmentJobs inventory fetch: two companies;
- DSV static first-party card extraction: one company;
- Aramark S7 provider projection: one company;
- iCIMS handoff parsing for Nisga'a Tek: one company;
- Home Depot/Versana action transport: two companies with different transport
  failures.

No provider heuristic, company special case or identity relaxation was added.

## Release Gates

- focused replay/unit slice: 122 tests before integration;
- full suite: 2,862 tests, 4 skipped;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 48 native adapters, 0 issues;
- focused artifact and tracked credential-shape scan: 0;
- `git diff --check`: passed.

The first full-suite run was blocked only by sandbox denial of a temporary
loopback extension-bridge bind. The identical permission-enabled offline run
passed; no external network was used.

## Decision

Accept `.281` as a replay-only deterministic contract. Fresh100 and Frozen100
live scores, Exact counts and URL-safety claims remain unchanged. Sealed
cohorts, plugin, coordinator-v2 and the isolated LLM branch remain untouched.
