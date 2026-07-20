# Fresh 100 `.191` Deterministic-Defect Focused Phase C

## Frozen Run

- Code commit: `92df2826124ba1058e27985484754b9e049f12c8`
- Adapter version: `2026-07-20.191`
- Source cohort: July 18 fresh 100, ordinals 41, 54, 73, and 26
- Records: CHAMP, Milwaukee Tool, NextPlay Jobs, and IGNITE
- Focused input SHA-256:
  `bd58c2f05a5f799bb3785f37d25d766b68b65001dd5ef9927451f282b97d8e20`
- Isolation: new checkpoint, completion, evidence, snapshot, replay, and output
  roots below `/private/tmp/fresh100-v191-focused-20260720-run1`
- Resume policy: disabled; workers: 1

The code stayed frozen for the live run and automatic full-outcome replay. The
run archive is
`artifacts/releases/fresh100-v191-focused-20260720-run1.tar.zst`, SHA-256
`38ea730601d738f7449a051ff66547259b1a75f437f66639ec13caa64bb344ec`.
It does not replace the `.188` fresh or frozen-100 aggregate.

## Result

The serial live completed 4/4 in 105.3 seconds. Same-version replay reproduced
4/4 outcomes with zero mismatch and zero fixture gap.

| Record | Website | Career | Job List | Terminal | Interpretation |
| --- | :---: | :---: | :---: | --- | --- |
| CHAMP | no | no | no | `FETCH_FAILED` | Retryable S2 transport stopped before Freshteam execution. |
| Milwaukee Tool | yes | yes | no | `JOB_BOARD_NOT_FOUND` | The new visible Career priority recovered the authoritative handoff, then exposed a distinct dynamic-inventory gap. |
| NextPlay Jobs | no | no | no | `FETCH_FAILED` | Retryable S2 transport stopped before posting-intermediary execution. |
| IGNITE | yes | yes | yes | `OPENING_NOT_FOUND` | Complete 68-record HRSmart inventory rejected the conflicting `- MID` level before publication. |

There were no published opening URLs, so wrong-opening, cross-company, and
cross-tenant false positives are zero. The audited ledger is one
`VERIFIED_NOT_FOUND` and three `SYSTEM_GAP`; CHAMP and NextPlay remain live
inconclusive rather than failed implementation claims.

## Contract Outcomes

### Passed: strict complete-inventory title semantics

IGNITE now stops at S6 with `verified_inventory_no_match`, full inventory,
strongest title score 92, and `OPENING_NOT_FOUND`. It no longer selects opening
339 and asks S7 to reject the incompatible `MID` level. The invalid opening URL
is never published.

### Passed partially: visible external Career handoff

Milwaukee now preserves the late visible `COMPANY + CAREERS` link and verifies
`https://www.milwaukeetool.jobs` as its Career surface. This closes the
homepage-link-cap defect, but not the full Milwaukee Job List outcome.

### Live inconclusive: Freshteam and intermediary terminal

CHAMP and NextPlay both received `HTTP 451` on LinkedIn evidence after S2 failed
to select a website. Neither reached the changed code path. Their sanitized
fixtures and negative controls pass in the 2480-test offline gate, but another
same-version live retry is required before claiming live closure.

## New Executable Failure Cluster

### First-party literal JSON POST inventory

Milwaukee's verified `/JobSearch` page and fetched application asset jointly
declare one same-origin public transport:

```text
POST https://www.milwaukeetool.jobs/api/jobs/JobListing
Content-Type: application/json
```

The request body is built only from public page attributes
(`Locations`, `Categories`, `HideFacets`, and `UseWorkDay`). The response owns
`Jobs`, `Categories`, `Locations`, and `TotalJobCount`; each opening uses a
stable `Reqnumber` and the first-party detail route
`/Jobdetails?reqNumber=<id>`. Current `js_declared_inventory.py` supports other
bounded declared GET/POST transports but does not recognize this literal
same-origin JSON POST form.

The proposed `.192` contract is generic:

1. Require a verified first-party Job List page and exactly one literal HTTPS,
   same-origin POST endpoint declared by a bounded page asset.
2. Permit only a static JSON object assembled from public page attributes;
   reject credentials, cookies, bearer values, free-form user fields, dynamic
   hosts, redirects, and multiple endpoints.
3. Require a stable list key, title, location, opening ID, total count, and an
   exact same-origin detail template before declaring completeness.
4. Enforce existing response-size, candidate-count, request-budget, snapshot
   sanitization, and replay contracts.
5. Feed resulting candidates through ordinary title/location/status and S7
   identity gates; the declaration produces inventory evidence, never success.

Focused acceptance is Milwaukee reaching a verified first-party Job List and
either its exact current opening or an authoritative complete-inventory
`OPENING_NOT_FOUND`. Negative controls must reject cross-origin endpoints,
multiple endpoints, secret-bearing bodies, redirecting POSTs, malformed totals,
duplicate IDs, and unsafe detail URLs. A recovery below the frozen positive
fixture or any negative-control acceptance invalidates the cluster contract.

## Decision

`.191` is a valid correctness improvement but does not close all four live
records. Do not add a Milwaukee company branch and do not fold the new S5 gap
back into an S2 label. The next implementation change requires version `.192`;
CHAMP and NextPlay same-version retries remain separate from that code change.
