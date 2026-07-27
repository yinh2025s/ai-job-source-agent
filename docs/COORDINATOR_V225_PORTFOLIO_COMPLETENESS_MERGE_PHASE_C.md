# `.225` Portfolio Completeness Merge Phase C

## Result

The code-frozen five-record cold run is preserved at
`/private/tmp/fresh5-v225-portfolio-20260723-run1`.

| Company | S5 portfolio result | Terminal |
| --- | --- | --- |
| STEAMe | complete JazzHR portfolio retained | S7 Exact Product Designer in Chicago |
| ARUP Laboratories | complete UltiPro portfolio retained | `INVALID_STRUCTURED_DATA`; current UltiPro response was invalid and the company deadline then expired |
| Crosby | incomplete; legacy success had no explicit source portfolio | `JOB_BOARD_PORTFOLIO_INCOMPLETE` |
| WalkMe | merge path not reached with first-party evidence | S2 `NETWORK_TIMEOUT`; untrusted Lever tenant remained identity-rejected |
| OneApp | merge path not reached with first-party evidence | S2 `NETWORK_TIMEOUT`; untrusted Ashby tenant remained identity-rejected |

The run produced one verified Exact, five verified Job Lists, and no wrong URL,
wrong location, cross-company or cross-tenant publication. STEAMe's selected
JazzHR opening binds the exact title, Chicago location, tenant `steamellc`,
complete three-record inventory and canonical opening URL.

## Implemented Contract

The merge now computes completeness over authorized eligible board identities:

- complete producer portfolios contribute provider + canonical-board coverage;
- every authorized eligible board must be covered by at least one complete
  producer;
- unauthorized diagnostic candidates neither authorize nor poison the covered
  set;
- uncovered authorized boards, incomplete producers and merges above the
  eight-board cap remain incomplete.

The contract is exercised by three independent real company shapes: STEAMe and
ARUP in the cold focused run, plus the existing Sony Interactive Entertainment
multi-board regression fixture. Sony retains all four first-party authorized
brand boards as complete even when a duplicate targeted-search candidate is
present. The related discovery/checkpoint/replay suite passes 256 tests.

## Phase-A Hypothesis Correction

The initial five-company classification was too broad. The frozen live run
proved that S5 stage labels had combined four different causes:

- STEAMe and ARUP exercised the explicit complete-source merge defect.
- Crosby's legacy route published a successful single generic board but no
  source `JobBoardPortfolio`; the new contract correctly did not invent
  completeness evidence.
- WalkMe and OneApp did not reach a verified first-party merge because S2
  timed out. Their typed candidates remained unauthorized.

The implementation cluster therefore closes only the explicit complete-source
accounting defect. It does not claim five-company end-to-end recovery.

## Replay

All five records were exported with complete scoped tapes and replayed. Outcome
identity reproduced for four records, including STEAMe Exact and Crosby's
fail-closed terminal. ARUP mismatched only in the terminal reason:

```text
live:   INVALID_STRUCTURED_DATA
replay: COMPANY_TIME_BUDGET_EXHAUSTED
```

Company, Website, Career, provider, tenant and Job Board identity were equal.
This is a replay/budget-terminal determinism defect, not an opening identity
false positive. Because the exact restored-budget shape currently has one
record in this run, no replay behavior is changed in `.225`.

## Decision

Accept the completeness-preserving merge and keep `.225`. Do not declare the
original five-record cluster closed and do not rewrite the official Fresh100
score from this focused run. Crosby's no-source-portfolio boundary, ARUP's
UltiPro response/budget behavior, and the two S2 timeouts return to causal
clustering. Full Fresh100/Frozen100 and sealed holdouts were not run.
