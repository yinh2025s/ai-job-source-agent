# Coordinator `.236/.237` Phase C: Rejected

## Result

The experiment is rejected and its behavior changes are rolled back.

The original five-company trigger was:

```text
S2/S3 success
-> S4 consumes the discovery child deadline
-> S5 never executes
```

`.236` made S4 stop cooperatively and changed the boundary from company-budget
exhaustion to typed fetch-budget exhaustion. It also exposed that the S5 search
wave let Career Surface consume the provider reserve first.

`.237` orders the S5 search wave as:

```text
Provider Search -> Career Surface
```

Direct External Apply and Website/Career candidates retain their existing
direct wave.

## Focused live

The valid `.237` run executes Provider Search for all five companies:

| Company | Provider queries | Career-surface queries | Published board/opening |
| --- | ---: | ---: | --- |
| Caesars Entertainment | 3 | 0 | none |
| Splashlight | 7 | 2 | none |
| Pitch Aeronautics | 7 | 2 | none |
| Nisga'a Tek | 7 | 0 | none |
| FOTOMILL Studios | 3 | 0 | none |

Splashlight and Pitch encountered independent S2 transport failures, but their
provider route still executed. This proves Website resolution is not a global
prerequisite for that route.

No candidate passed provider, tenant and relationship verification, so the run
published no Job List or opening.

The required regression check then reran iClassPro on `.237`. Under `.235`,
iClassPro reaches the official Paylocity board and S7 Exact opening `4331044`.
Under `.237`, the S4 reservation stops Career discovery before that handoff is
found, and provider search does not recover it. The record regresses to
`FETCH_BUDGET_EXHAUSTED`.

Route coverage therefore improved while product correctness regressed. The
stage-v1 reservation and Provider-Search-first ordering are both rolled back;
adapter version returns to `.235`. Coordinator-v2 remains proposed and
disabled.

Two fresh-directory `.235` rollback checks were then attempted with the same
iClassPro input and run configuration. The first stopped in S2 on a TLS
handshake `NETWORK_TIMEOUT`; the second resolved the Website but stopped in S4
on a retryable `FETCH_FAILED`. Both replayed 1/1. Neither reproduced the `.237`
`FETCH_BUDGET_EXHAUSTED` signature, but neither recovered the Exact opening
under the current network state. The rollback is therefore code- and
test-confirmed, while live Exact recovery remains unconfirmed. The Fresh100
projection is not changed.

The earlier run1 crossed a long host suspension and contains multi-thousand
second records; it is retained but invalid for benchmark comparison. Run2
proved the cooperative S4 boundary. Run3 is the authoritative `.237` focused
capture.

## Replay and gates

- Run3 replay: 5/5 reproduced.
- Mismatch: 0.
- Fixture gap: 0.
- Record integrity: 5/5.
- Composition/retry/candidate tests: 101/101.
- Total company, request and opening budgets were unchanged.
- No wrong URL, company, location or tenant was published.
- Regression gate: failed on iClassPro Exact, forcing rollback.

Artifacts:

- `/private/tmp/fresh5-v236-provider-reserve-run1` (invalid: host suspension)
- `/private/tmp/fresh5-v236-provider-reserve-run2`
- `/private/tmp/fresh5-v237-provider-first-run3`
- `/private/tmp/fresh5-v237-provider-first-run3/replay-bundle`
- `/private/tmp/fresh1-v237-iclasspro-run1`
- `/private/tmp/fresh1-v235-iclasspro-rollback-run2`
- `/private/tmp/fresh1-v235-iclasspro-rollback-run3`
