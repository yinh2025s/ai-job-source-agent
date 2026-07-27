# v245 Ashby Mixed-Case Runtime Locator - Phase A

## Trigger

The code-frozen `.244` diagnostic run produced the same worker contract failure
for Oso and Blossom:

```text
ValueError: Job board locator is not replay-safe for this provider
```

Both first-party Career pages expose an Ashby tenant whose path is
case-sensitive:

- `https://jobs.ashbyhq.com/Oso/...`
- `https://jobs.ashbyhq.com/Blossom-Health`

`AshbyAdapter.identify_board()` preserves the case-sensitive tenant correctly
but always marks the locator `replay_safe=True`. The central durable-locator
policy intentionally permits only lowercase Ashby tenant paths. Constructing a
`JobBoardPortfolio` therefore rejects the adapter's own board and terminates
the worker.

The same contract contradiction reproduces deterministically for at least six
real company tenants already present in development/frozen evidence: Oso,
Blossom-Health, Fuse, Acorns, Distyl and Zello. Blossom is also an existing
Frozen100 audited Exact, so the `.244` failure is a release-gate regression.

## Contract

1. Ashby tenant case must be preserved; do not lowercase or rewrite its URL.
2. A lowercase Ashby tenant remains `replay_safe=True`.
3. A mixed-case Ashby tenant remains a valid live provider board but is marked
   `replay_safe=False`.
4. Runtime-only boards may participate in the live portfolio and official
   inventory validation.
5. Runtime-only boards must not be serialized as durable checkpoint locators.
6. Replay must reconstruct them from captured first-party page/provider
   evidence rather than trusting a stored locator.
7. Provider, tenant, hiring relationship, title, location and S7 gates remain
   unchanged.

## Acceptance

1. Unit tests cover lowercase and mixed-case board identification.
2. A mixed-case Ashby board can enter a runtime portfolio without an exception.
3. Its checkpoint payload is omitted; a forced `replay_safe=True` mixed-case
   locator remains rejected by the central policy.
4. Oso and Blossom captured snapshots no longer produce
   `batch_worker_contract_failed`.
5. Blossom restores its existing Frozen100 audited Exact or reports a typed
   evidence-backed inventory terminal without an unsafe URL.
6. Oso reaches typed Ashby inventory validation without publishing an
   unverified opening.
7. Focused capture/replay has zero mismatch, fixture gap and tape divergence.
8. Relevant Ashby, portfolio, S5-S7 and replay tests pass.

## Rollback

Revert the adapter replay-safety flag decision. Do not weaken the central
Ashby durable-locator policy and do not lowercase mixed-case tenant paths.

## Out Of Scope

This phase does not change search, budgets, coordinator-v2, the extension,
External Apply, LLM behavior, Fresh100 projections or sealed holdouts.
