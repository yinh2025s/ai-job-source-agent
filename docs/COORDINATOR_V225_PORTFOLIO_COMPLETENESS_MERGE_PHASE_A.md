# `.225` Portfolio Completeness Merge Phase A

## Frozen Evidence

Five independent companies were initially grouped under one S5-to-S6
accounting hypothesis:

| Company | Authorized inventory | Diagnostic candidate | Current terminal |
| --- | --- | --- | --- |
| ARUP Laboratories | complete UltiPro inventory | none material | `JOB_BOARD_PORTFOLIO_INCOMPLETE` |
| STEAMe | complete JazzHR board discovery | none material | `JOB_BOARD_PORTFOLIO_INCOMPLETE` |
| WalkMe | first-party generic board | untrusted Lever candidate | `JOB_BOARD_PORTFOLIO_INCOMPLETE` |
| OneApp | first-party generic board | untrusted Ashby candidate | `JOB_BOARD_PORTFOLIO_INCOMPLETE` |
| Crosby | first-party generic board | untrusted Ashby candidate | `JOB_BOARD_PORTFOLIO_INCOMPLETE` |

All five ran `candidate_discovery_coordinator_v2`, completed or explicitly
skipped every candidate route, reported `truncated=false`, and attempted every
retained authorized board in the frozen trace. Pre-implementation analysis
treated their legacy Website/Career outputs as equivalent bounded portfolio
evidence. The code-frozen Phase C run later disproved that equivalence for
Crosby and did not reach the merge path for WalkMe or OneApp; the correction is
preserved in `COORDINATOR_V225_PORTFOLIO_COMPLETENESS_MERGE_PHASE_C.md`.

## Root Cause

`_merge_legacy_website_route()` deduplicates the candidate-coordinator and
legacy Website/Career boards, then unconditionally constructs the merged
`JobBoardPortfolio` with `eligible_set_complete=False`.

That assignment discards producer-owned completeness evidence. S6 sees zero
unattempted eligible boards but still emits `JOB_BOARD_PORTFOLIO_INCOMPLETE`.
The failure is therefore portfolio inventory accounting, not provider parsing,
title matching, or a company-specific discovery gap.

## Frozen Contract

### Completeness is scoped to authorized eligible boards

- Build the merged authorized eligible board set from route evidence. If route
  evidence is absent, treat every retained board as eligible.
- A source portfolio may cover boards only when its own
  `eligible_set_complete` flag is true.
- The merged portfolio is complete only when every authorized eligible board is
  present in the union of complete source portfolios.
- An incomplete source does not poison a board whose same provider and
  canonical URL are independently covered by a complete source.
- An unauthorized targeted-search or stored candidate remains diagnostic. It
  cannot authorize an opening, establish a hiring relationship, or make an
  otherwise complete authorized set incomplete.
- A diagnostic candidate that deduplicates to an independently authorized
  board does not create a second eligible board or invalidate a complete
  first-party multi-board portfolio.

### Fail-closed boundaries

- More than eight deduplicated boards keeps the merged portfolio incomplete;
  capping must never hide an eligible board.
- A truncated, retryable, failed, unfinished, or otherwise incomplete producer
  contributes no completeness coverage.
- Any authorized board that appears only in an incomplete producer keeps the
  merged portfolio incomplete.
- Provider, tenant, company relationship, title, location, opening state and S7
  identity gates are unchanged.
- Complete inventory may support only a verified no-match or no-public-opening
  terminal. It cannot manufacture Exact.

## Acceptance

- Complete legacy coverage plus an unauthorized diagnostic candidate preserves
  completeness for the authorized board set.
- The same candidate with verified hiring relationship keeps the merge
  incomplete unless a complete source covers it.
- An incomplete legacy portfolio remains incomplete.
- A merge that would cap any board remains incomplete.
- ARUP may reach verified no-match only after its complete UltiPro inventory is
  attempted; no Exact is expected from this accounting repair.
- STEAMe is re-evaluated through current detail/location gates. Exact is allowed
  only with URL-bound JazzHR title and Chicago location evidence.
- WalkMe, OneApp and Crosby remain `OPENING_DISCOVERY_INCOMPLETE` when their
  authorized first-party inventories are themselves incomplete. Their
  unrelated Lever/Ashby candidates remain rejected.
- Focused unit tests, all five live records, scoped replay and URL identity audit
  pass before Phase C closure.

## Excluded Shapes

- HP and Mayo Clinic share an Eightfold PCS X parser variant, not this merge
  accounting path.
- Home Depot has a single CWS/m-cloud bounded-inventory issue.
- Cretex has a single iCIMS/WordPress recognition issue.
- Aramark has a single restored-checkpoint declared-inventory continuity issue.

None currently satisfies the three-company implementation rule.

## Ownership

- Merge contract: `job_source_agent/stages/discovery.py`.
- Contract tests: `tests/test_parallel_candidate_stage.py`.
- Main line: adapter version, focused live/replay, closure matrix, changelog and
  Phase C report.

## Rollback

Revert if an unauthorized route can make an opening eligible, if a board absent
from all complete source portfolios is treated as covered, if a capped portfolio
is declared complete, or if any wrong-company, wrong-location or cross-tenant
opening reaches Exact.
