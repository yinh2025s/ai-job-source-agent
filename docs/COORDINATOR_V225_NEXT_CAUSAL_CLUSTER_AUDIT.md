# `.225` Next Causal Cluster Audit

## Decision

Neither of the two highest-ranked labels forms a valid three-company
implementation cluster. No budget, retry, error-precedence or replay behavior
is changed after `.225`.

## Fetch Budget Label Rejected

Diamondback Energy, Prophetic and WICHITA COMPANY LIMITED all ended with a
budget-related terminal, but their causes differ:

| Company | Executable cause |
| --- | --- |
| Diamondback Energy | correct first-party Career paths repeatedly hit read timeout; dispatch count was below the configured transport-call cap |
| Prophetic | company identity is not stable and low-evidence tenant probes mixed with TLS/timeouts; historical inventory outcomes conflict |
| WICHITA COMPANY LIMITED | UK-domain TLS EOF/403 plus speculative ATS 404s; the LinkedIn location and resolved entity require identity review first |

The shared pipeline budget classifier is only a common terminal path. Raising
global time or request limits would not batch-recover these records and could
increase unsafe speculative work.

A future scheduler cluster requires at least three companies where a known
high-evidence candidate exists and the same low-evidence probe class consumes
its reserved budget first.

## Replay Mismatch Label Rejected

ARUP is the only observed record with this exact direction:

```text
live INVALID_STRUCTURED_DATA -> replay COMPANY_TIME_BUDGET_EXHAUSTED
```

The live attempt mixed one UltiPro parser error with three caller-deadline
errors. Replay retained the same company/provider/tenant/board identity but
selected the budget terminal after checkpoint restoration. Other historical
budget transitions run in the opposite direction and are not the same cause.

Reason precedence and replay classification remain unchanged until two more
independent companies reproduce the same mixed-error, same-tape,
same-identity transition.

## Next Action

Run a code-frozen cold focused rerun for the ten records whose previous S2
trace dispatched a historically verified official host but saw simultaneous
timeouts on the official candidate, LinkedIn company source and all search
transports:

- Brown and Caldwell
- STRIKE
- QXO
- ProMach
- System One
- WENDEL Companies (two postings)
- BWXT
- Conrad Consulting
- Salas O'Brien

This run is diagnostic. Recovery without a transport code change rejects a
new implementation; repeatable endpoint-healthy failure in at least three
companies is required before freezing a transport contract.
