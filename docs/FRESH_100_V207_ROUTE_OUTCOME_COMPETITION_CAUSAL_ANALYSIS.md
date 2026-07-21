# Fresh100 `.207` Route-Outcome Competition Causal Analysis

Date: 2026-07-21

## Phase A Status

This document freezes the causal cluster and acceptance contract before code
changes. It uses only the observed Fresh100 development artifacts. Blind
holdouts v2/v3 remain sealed, and the LLM experiment branch remains isolated.

## Shared Trigger

The selected cluster is:

`FIRST_PARTY_GENERIC_AND_EMBEDDED_TYPED_ROUTE_COEXIST_UNTIL_S6`

The shared trigger is present for at least three independent companies:

| Company | First-party route | Typed route | Current failure |
| --- | --- | --- | --- |
| OneApp | verified Career/Job List shell | page-declared Pinpoint tenant | generic route stops search or a same-name Ashby search board displaces stronger evidence |
| The Home Depot | verified dynamic first-party Job List | page-declared CWS inventory protocol | S5 exposes only one route; S6 cannot preserve the other route's identity/outcome |
| Crosby | verified first-party partial inventory | explicit ADP open-position handoff | partial generic inventory can suppress or be displaced by an unrelated search board |

All three have the same executable cause: S5 chooses one global provider
identity before S6, while `JobBoardPortfolio` stores boards without board-bound
hiring-relationship evidence. S6 returns the first opening before route-local
S7 verification. If the selected opening is later rejected, S6 cannot continue
to another route. A complete no-match on an unauthorized same-name tenant can
also incorrectly dominate an incomplete first-party route.

Necessary Ventures, Tyler Technologies, Mayo Clinic, and Crawford Thomas are
not counted in this cluster. Their prerequisites are respectively hiring-
entity extraction, Talent Network classification, Eightfold/Oracle variant
support, and Bullhorn transport/provider support.

## Frozen Contract

1. S5 retains each canonical board together with immutable route provenance and
   a `HiringRelationshipEvidence` bound to that provider, tenant, and board.
2. Route kinds are `external_apply`, `website_career`, and `provider_search`.
3. Same-name provider/search tenant similarity may rank a lead but cannot, by
   itself, authorize Exact or company-level no-match.
4. External Apply, first-party ATS handoff, verified first-party inventory, and
   provider-published employer evidence may authorize a route when their
   provider/tenant/board binding is continuous.
5. Generic first-party and typed ATS routes coexist until S6. A route cannot
   borrow another route's hiring or provider identity.
6. S6 reads a canonical board once, constructs route-local hiring, provider,
   opening, and selection identities, and runs the existing identity-chain and
   title/location validators before accepting an Exact.
7. An identity-rejected opening is recorded and S6 continues to the next route.
8. Equivalent verified Exact outcomes deduplicate. Two non-equivalent verified
   Exact outcomes fail closed as ambiguous.
9. One verified Exact wins over no-match, incomplete, blocked, or rejected
   alternatives. Without an Exact, incomplete/retryable/blocked authorized
   routes take precedence over verified no-match.
10. Company-level `OPENING_NOT_FOUND` or `NO_PUBLIC_OPENINGS` is allowed only
    when every eligible authorized route is attempted and complete.
11. Unverified search routes never establish company-wide absence.
12. Runtime-only portfolio membership cannot be silently truncated in a saved
    checkpoint. A mixed portfolio is saved completely or S5 is recomputed from
    its replayable producer boundary.

## Schema And Ownership

- `JobBoardPortfolio` payload advances from schema `1.0` to `2.0` and carries
  route evidence.
- `HiringRelationshipEvidence` gains strict checkpoint round-trip methods.
- Pipeline contract advances from `1.6` to `1.7`.
- Deterministic run configuration advances from `1.4` to `1.5`.
- Adapter version advances to `.207`, invalidating incompatible S5/S6
  checkpoints and batch completions.
- Main owns `job_board.py`, `identity_continuity.py`, `contracts.py`,
  `stages/discovery.py`, `checkpoint.py`, `run_configuration.py`, and final
  integration.
- Replay tests, stage-scenario tests, and documentation may fan out only after
  these contracts are frozen and must use disjoint files/worktrees.

## Acceptance

Offline contract scenarios must prove:

1. Pinpoint Exact plus same-name Ashby no-match returns the Pinpoint Exact.
2. Pinpoint incomplete plus Ashby no-match remains incomplete.
3. First-party partial plus ADP Exact returns the ADP Exact.
4. ADP blocked plus Ashby no-match remains blocked/incomplete.
5. A first opening rejected by route-local S7 does not prevent a later Exact.
6. Exact plus verified no-match returns Exact.
7. Verified no-match plus incomplete remains incomplete.
8. Only all-authorized-complete no-match may return `OPENING_NOT_FOUND`.
9. Two different verified Exact identities fail closed as ambiguous.
10. Route ordering does not change the accepted canonical Exact.
11. Route provenance and outcomes survive checkpoint/replay without mismatch.
12. An output URL with a non-verified identity verdict is never counted Exact.

Focused Phase C uses new isolated roots for OneApp, The Home Depot, and Crosby.
All three must expose both first-party and typed route provenance. OneApp must
recover its existing Pinpoint Exact evidence; Home Depot and Crosby may remain
incomplete if current public inventory cannot be captured, but neither may
publish an unauthorized no-match or wrong URL. Scoped replay must be 3/3 with
zero mismatch and fixture gap.

The change is rejected if it restores no Exact, weakens a terminal outcome,
produces any wrong URL/company/location/tenant, loses route membership on
replay, or regresses the original Frozen100 69 Exact. A generic repair that
recovers fewer than three independent companies may not be declared cluster
closure; the cluster must be reclassified or the architecture revised.
