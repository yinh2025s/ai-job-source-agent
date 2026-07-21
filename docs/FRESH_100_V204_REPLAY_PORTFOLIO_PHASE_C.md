# Fresh100 `.204` Multi-Board Replay Phase C

Date: 2026-07-21

## Change

`.204` makes the final typed S5 `JobBoardPortfolio` the single projection source
after relationship ranking. It writes one consistent Job List URL, provider,
provider detection, StageResult provider, primary summary and typed context.
The S5 trace also stores the existing versioned checkpoint payload: complete
when every member is replay-safe, otherwise an explicitly incomplete safe
prefix. A partial payload cannot claim complete membership.

Replay prefers that complete S5 payload and treats S6 attempts as the consumed
prefix. The legacy migration remains fail closed: a stale primary is replaced
only when provider detection, top-level Job List, the first Exact attempt, S6
StageResult and the complete verified S7 source-company/provider/tenant/board/
opening/title/location chain all agree. The migrated typed provider remains
authoritative over stale StageResult metadata. No company, domain, tenant or
job identifier branch was added.

## Focused Replay

The immutable `.202` Holland America Line record originally had Pinpoint in the
S5 summary/StageResult and Oracle HCM in the typed context, top-level Job List,
S6 and S7. `.204` replays that original capture as:

- selected/exported/result/trace/comparison: 1/1;
- reproduced: 1;
- mismatch: 0;
- fixture gap: 0;
- verified provider: `oracle_hcm`;
- canonical opening: Oracle HCM `HAGroup/job/13555`.

Focused manifest SHA-256:
`a7363c429b21c7123c7d2b86be0ce169dab83999383e41730e0a3def5a72306a`.

## Full Fresh100 Replay

The final `.204` code replayed the unchanged `.202` Fresh100 snapshots from a
new output root:

| Gate | Result |
| --- | ---: |
| Source/filter/selected/exported | 100/100 |
| Results/traces/comparisons | 100/100 |
| Reproduced | 96 |
| Explicit company-budget recovery | 4 |
| Expected transition | 0 |
| Fixture gap | 0 |
| Mismatch | 0 |
| Dropped/omitted record | 0 |

The four bounded budget normalizations are Diamondback Energy, North Dakota
Information Technology, ARUP Laboratories and HP. They use the pre-existing
`company_budget_replay_normalized` classification and do not change identity or
opening output.

- Final review run root: `/private/tmp/fresh100-v204-full-replay-20260721-run4`
- Manifest SHA-256:
  `da74ec33fb2c7174640f63bf07c488c00f2ffe27013402dd9dcbedd457d8d268`
- Read-only archive:
  `artifacts/releases/fresh100-v204-full-replay-20260721-run4.tar.zst`
- Archive SHA-256:
  `8bcc80c0327927ac9c7d32d5b1f49ab28d3dda34ab168c195c3a666e323ea673`

## Offline Gates

- CPython release runtime: 3.12.6
- Unit/integration tests: 2557 passed, 4 skipped
- Provider benchmark: 25/25
- Resolver benchmark: 6/6
- Architecture validation: 46 native adapters, 0 issues
- `git diff --check`: passed

The first sandboxed full-test attempt exposed seven real projection/checkpoint
regressions plus one loopback bind permission error. A later adversarial review
found three additional contract edges: mixed replay-safe/runtime-only payloads,
legacy migration without full source/title/location binding, and stale provider
overwrite after migration. All were fixed with fail-closed negative tests. The
final full gate was rerun with local loopback permission and passed completely.

## Decision

The Holland replay failure cluster is closed. This closes replay determinism for
the `.202` development run; it does not improve or reinterpret its 20/100 live
Exact score. Product discovery work continues from the remaining causal
Fresh100 clusters.
