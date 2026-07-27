# Coordinator `.216` Focused Live And Replay

## Scope

This development gate used the first three records from the immutable Fresh100
`.209` input: Loveland Innovations, iClassPro - Class Management Software and
Indica Labs. It did not execute Fresh100 as a cohort and did not inspect either
sealed holdout or the isolated LLM branch.

- Input: `/private/tmp/coordinator-v214-focused3-input.json`
- Input SHA-256: `29684b89610ba5ec502c567129558cc2788ed03feb1ce0e2d6d071cf73da77f4`
- Failed `.214` root: `/private/tmp/coordinator-v214-focused3-run1`
- Failed `.215` root: `/private/tmp/coordinator-v215-focused3-run1`
- Accepted diagnostic `.216` root: `/private/tmp/coordinator-v216-focused3-run1`
- Engine: `coordinator_v2`
- Run configuration schema: `1.6`
- Run configuration digest: `07a984f402b3c7af215e46b64e38159fe2a24424fcf0636384c7c7d56d5ad680`

Every run used new checkpoint, completion, company-evidence, snapshot and replay
roots. No prior completion or candidate cache was restored.

## Integration Defects Closed

`.214` failed 3/3 before candidate discovery because the immutable input binder
accepted only numeric LinkedIn `/jobs/view/<id>` paths, while all three source
records used canonical `/jobs/view/<title>-<id>` paths. `.215` accepted the
strict final numeric slug token but then failed 3/3 when the zero-board terminal
passed an unsupported `retryable` keyword to `make_stage_result`.

`.216` closes both defects. The same three records complete without worker
contract errors and produce typed pipeline terminals.

## `.216` Live Outcome

| Company | Website | Career | Provider queries | Job List | Opening | Terminal |
| --- | --- | --- | ---: | --- | --- | --- |
| Loveland Innovations | yes | no | 0/5 | no | no | company budget exhausted / `JOB_BOARD_NOT_FOUND` |
| iClassPro | no | no | 5/5 | no | no | `NETWORK_TIMEOUT` + `JOB_BOARD_NOT_FOUND` |
| Indica Labs | no | no | 5/5 | no | no | `NETWORK_TIMEOUT` + `JOB_BOARD_NOT_FOUND` |

Raw Job List and Exact recovery are both `0/3`. No URL, company, tenant or
location false positive was published.

The result proves one intended coordinator property: a failed S2 does not
globally prevent provider search. iClassPro and Indica Labs each executed all
five scheduled provider queries despite `website_resolution=NETWORK_TIMEOUT`.

It also rejects phase closure. Loveland reached S5 only after S4 consumed the
usable company deadline. Its provider route reports `completed` with zero
requests and zero queries. A route that received no execution opportunity must
instead report typed budget exhaustion, and S4 must not consume the provider
route's reservation.

## Replay

The automatic same-version replay exported and executed all three records:

- record integrity: passed, 3/3;
- reproduced: 2;
- accepted company-budget normalization: 1;
- mismatch: 0;
- fixture gap: 0;
- tape divergence: 0.

Replay acceptance proves the `.216` outcomes are reproducible. It does not turn
the 0/3 recall result into a successful coordinator release.

## Decision

Keep `stage_v1` as the default. The next backend phase is limited to the shared
budget contract:

1. preserve provider-search `deadline_exhausted` as
   `budget_exhausted/FETCH_BUDGET_EXHAUSTED`;
2. reserve a deterministic S5 provider-search window before S4 can consume the
   downstream phase;
3. retain the existing S6 opening reserve;
4. prove the reservation across at least three latency fixtures and rerun this
   same focused cohort from another clean root;
5. do not change provider, tenant, relationship, title, location or S7 gates.
