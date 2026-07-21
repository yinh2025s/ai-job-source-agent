# Fresh100 `.202` Multi-Board Replay Causal Analysis

Date: 2026-07-21

## Scope

This Phase A analysis is read-only against the code-frozen `.202` Fresh100 run
at `/private/tmp/fresh100-v202-network-rerun-20260721-run3`. It explains the
single `scoped_stage_seed_ambiguous` record that stopped the 100-record replay
before execution. It does not change the live score and does not inspect the
sealed v2/v3 holdouts.

## Trigger And Impact

The executable trigger is:

```text
S5 discovers more than one verified Job Board
-> relationship ranking changes the typed portfolio primary
-> trace and StageResult retain the pre-ranking primary
-> S6 succeeds on the new primary and stops before visiting later boards
-> replay cannot reconstruct one unambiguous S5 context
```

Holland America Line is the only direct occurrence in this Fresh100 run. Its
typed context, top-level Job List, S6 attempt, S7 provider/tenant/board and
Exact URL all identify the Oracle HCM `HAGroup` board. The S5 trace summary and
S5 StageResult provider instead identify the Pinpoint board. One invalid record
correctly stops the full replay preflight, leaving 100 selected, 100 exported
and zero replayed; the other 99 records are not thereby shown to be invalid.

Twenty records contain an S5 portfolio summary and only Holland America has two
eligible boards. Its network snapshots and Oracle S6 API evidence are present,
so this is not a fixture or transport gap.

## Root Cause

`find_job_board_portfolio()` emits a preliminary trace projection. The S5 stage
then applies company/tenant relationship ranking to the typed
`JobBoardPortfolio`, but does not atomically rewrite every projection. In
particular, `provider`, `provider_detection`, `job_list_page_url`, portfolio
summary and `StageResult.provider` can disagree with the final typed primary.

There is a second contract gap. S5 stores only a summary of its eligible
portfolio. Replay therefore tries to recover complete S5 membership from S6
attempts. S6 is allowed to stop after the first Exact, so its attempts are only
the consumed prefix and cannot be treated as the complete S5 candidate set.

## Frozen Repair Contract

Production S5 must project the final typed portfolio through one path after all
ranking and promotion. The same primary must populate:

- top-level and trace `job_list_page_url`;
- trace and `StageResult.provider`;
- `provider_detection`;
- `job_board_portfolio.primary_url` and `primary_provider`;
- typed `discovered_job_board` and `job_board_portfolio` updates.

S5 must also store a versioned, checkpoint-safe portfolio payload produced by
`JobBoardPortfolio.to_checkpoint_payload()`. Runtime-only or secret-bearing
members remain excluded by that existing contract.

Replay must prefer the full S5 payload and continue to reject any disagreement
with the summary, StageResult, top-level URL or provider detection. S6 attempts
describe only the consumed prefix. For legacy artifacts without the payload, a
bounded migration is permitted only when the first S6 attempt is Exact and the
complete verified S7 provider, tenant, board and opening chain agrees with that
attempt. Missing or conflicting evidence remains
`scoped_stage_seed_ambiguous`; validation is not relaxed.

## Acceptance

1. A multi-board S5 reorder changes every external projection to the final
   typed primary.
2. A two-board payload with one successful S6 attempt restores the complete S5
   portfolio and reproduces the Exact result.
3. Payload/summary/provider/URL conflicts still fail closed.
4. Existing legacy complete-attempt replay remains compatible.
5. Holland America scoped replay is 1/1 reproduced with zero mismatch and
   fixture gap.
6. The immutable `.202` Fresh100 snapshots replay 100/100 with zero boundary
   invalid, mismatch, fixture gap, tape divergence or dropped record.

Rollback is straightforward: revert the production projection helper and
replay migration together, advance the adapter version, and retain the original
failed manifest. A partial change that only hides the Pinpoint/Oracle conflict
without restoring complete portfolio provenance must not ship.
