# Coordinator `.235` Next Causal Cluster Audit

## Decision

No remaining Fresh100 development failure group currently satisfies the
three-company implementation gate:

- the same executable trigger;
- the same production code path;
- a shared general fix;
- and a nonzero expected batch recovery.

The backend therefore remains on `.235`. No new heuristic, provider special
case, budget increase or identity relaxation is authorized by this audit.

## Rejected Five-Company Budget Label

Caesars Entertainment, Splashlight, Pitch Aeronautics, Nisga'a Tek and
FOTOMILL Studios originally shared this stage sequence:

```text
S2/S3 success
-> S4 consumes the Career discovery window
-> S5 Provider Search does not execute
```

The `.236/.237` experiment made Provider Search execute for all five, with
3/7/7/7/3 queries and 5/5 replay. It recovered zero Job Boards and zero Exact
openings, then regressed iClassPro from a previously verified Paylocity Exact
to `FETCH_BUDGET_EXHAUSTED`.

The shared stage sequence was therefore not the shared causal root. Expected
batch recovery is zero, so this is not an implementation cluster.

## Other Insufficient Groups

| Candidate cause | Companies | Decision |
| --- | --- | --- |
| Structured detail identity/location not consumed | Lorum, StatRad | Two companies only |
| Declared GET search with incomplete inventory evidence | System One, DSV | Two companies only |
| Replay retry-reason drift | B&D Industries HR, City of Lubbock | Two companies only |
| Repeated records under one employer | WENDEL | One company, not a three-company cluster |
| Evidence-backed Career URL transient failure followed by speculative fallback starvation | iClassPro | One company only |
| Remaining provider, identity and transport cases | Conrad, Aramark, Hawaiian Electric, Home Depot and others | Distinct code paths or singletons |

## iClassPro Rollback Evidence

Two fresh-directory `.235` checks used the same input and frozen run
configuration:

1. Run2 stopped in S2 on TLS handshake `NETWORK_TIMEOUT`.
2. Run3 resolved the Website, produced the first-party `/careers` candidate,
   then the candidate hit retryable SSL EOF and S4 returned `FETCH_FAILED`.

Both runs replayed 1/1. Neither reproduced the rejected `.237`
`FETCH_BUDGET_EXHAUSTED` terminal signature, but neither recovered the Exact
opening under the current network state. The earlier `.235` capture proves the
Website, Career, Paylocity board and opening adapter path can reach opening
`4331044`.

The possible generic defect is:

```text
high-evidence Career candidate transient failure
-> no delayed retry slot retained
-> lower-evidence fallback consumes the remaining transport budget
```

It remains observation-only until three independent companies reproduce all
three conditions.

## Next Evidence Gate

The next backend cycle is causal evidence collection, not implementation:

1. Keep `.235` code frozen.
2. Select unresolved development records without reading sealed blind v2/v3.
3. Capture candidate provenance, exact failed request, transport taxonomy,
   fallback request allocation and final budget owner.
4. Group by trigger and code path, not S2/S4/S5 stage label.
5. Start implementation only when at least three independent companies share
   the same cause and the proposed fix predicts nonzero batch recovery.

Network-wide TLS failures remain environmental observations. They must replay
deterministically, but they do not authorize production retry or budget changes
by themselves.

## Gates

- Composition/retry/candidate scoped tests: 99/99.
- Provider benchmark: 25/25.
- Resolver benchmark: 6/6.
- Architecture validation: 46 adapters, 0 issues.
- `.235` rollback live run2 replay: 1/1.
- `.235` rollback live run3 replay: 1/1.
- `git diff --check`: clean.
