# Coordinator `.218` Focused Live And Replay

## Scope

The clean three-record development run used isolated roots under
`/private/tmp/coordinator-v218-focused3-run1`, coordinator-v2, run-config schema
1.7 and adapter `.218`. It did not run Fresh100, Frozen100, extension acceptance
or sealed blind cohorts.

## Outcome

- Website: 1/3; Career: 1/3; Job List: 0/3; Exact: 0/3.
- Loveland and iClassPro still completed all five primary provider queries and
  eight tenant probes after S2 timeout.
- All three records attempted the bounded secondary source. DuckDuckGo returned
  a challenge on the first rescue, its circuit opened, and no candidate was
  fabricated from the challenge page.
- Indica found its official Career page and explicit BambooHR handoff but ended
  at the company deadline before the handoff could be verified.

Strict same-version replay passed: 3/3 reproduced, 0 mismatch, 0 fixture gap
and complete record integrity. The `.217` budget-terminal mismatch is closed.

## Decision

Replay determinism is accepted; recall closure is rejected. The trace shows
that serial S5 spent lower-evidence provider-search work before the already
verified Website/Career traversal. `.219` changes only this evidence-priority
scheduling and must be tested without weakening any identity gate.
