# `.225` Known-Host Transport Rerun Phase C

## Result

The code-frozen ten-record cold rerun is preserved at
`/private/tmp/fresh10-v225-transport-20260723-run1`.

These records previously shared one precise observation: the resolver had
dispatched a historically verified official host, but the official candidate,
LinkedIn company source and all search transports timed out in the same run.
No transport code changed before this rerun.

| Outcome | Count |
| --- | ---: |
| Website recovered | 9/10 |
| Career recovered | 8/10 |
| Job List recovered | 8/10 |
| S7 Exact | 2/10 |
| Replay reproduced | 10/10 |

## Per-Record Outcome

| Company | Current terminal |
| --- | --- |
| Brown and Caldwell | verified UltiPro inventory, `OPENING_NOT_FOUND` |
| STRIKE | verified JazzHR board, `JOB_BOARD_PORTFOLIO_INCOMPLETE` |
| QXO | verified iCIMS inventory, `OPENING_NOT_FOUND` |
| ProMach | Website recovered, `CAREER_PAGE_NOT_FOUND` |
| System One | persistent S2 `NETWORK_TIMEOUT` |
| WENDEL Companies, Albany | first-party Job List, `OPENING_DISCOVERY_INCOMPLETE` |
| BWXT | S7 Exact Project Manager, Idaho Falls |
| WENDEL Companies, Williamsville | first-party Job List, `OPENING_DISCOVERY_INCOMPLETE` |
| Conrad Consulting | first-party Job List, `OPENING_DISCOVERY_INCOMPLETE` |
| Salas O'Brien | S7 Exact Project Manager, Evansville |

BWXT binds SuccessFactors tenant `custom:C0011463572P` and opening
`1397555300`. Salas O'Brien binds UltiPro tenant
`SAL1016SALO/3347ce03-ba60-4bdc-8af2-26369c80b18f` and opportunity
`70990d1a-1b25-410b-b250-fad9bc60a425`. Both Exact results match the source
title and location. No wrong URL, wrong city, cross-company or cross-tenant
result was published.

## Replay

The full replay bundle exported and replayed all ten records:

- 10 reproduced
- 0 mismatch
- 0 fixture gap
- 0 replayability drop
- complete result/trace/comparison coverage

## Decision

Reject a new transport implementation for this cluster. Nine official
Websites recovered without any transport code change, proving that the prior
simultaneous timeout wave was primarily external network state. System One is
one persistent sample, not a three-company implementation contract.

The recovered records are reclassified by their new executable downstream
causes. This focused run adds two proven Exact outcomes to the conservative
Fresh100 projection but does not replace the required future 100-record cold
benchmark.
