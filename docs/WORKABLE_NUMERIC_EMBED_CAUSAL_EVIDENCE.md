# Workable Numeric Embed Causal Evidence

## Hypothesis

Some first-party Career pages load Workable with a numeric
`whr_embed(<account_id>)` call but do not expose a canonical Workable board in
ordinary anchors. A generic numeric-embed resolver could recover those boards
without company-specific rules.

## Recovery Cases

Three independent companies reproduce the missing-path trigger:

- American Battery Technology Company: `whr_embed(708590)`;
- ClassWallet: `whr_embed(564001)`.
- Mention Me: `whr_embed(149632)`.

On frozen adapter version `2026-07-27.246`, both reach the official Career page
but do not publish a verified Workable Job List.

## Positive Controls

The focused four-company input is
`/private/tmp/workable-embed-gate-input.json`; artifacts are in
`/private/tmp/workable-embed-gate-run1`.

- ESR Group exposes `whr_embed(682995)` and a direct `apply.workable.com`
  handoff. It reaches Exact through the existing direct path.
- Symmetrio exposes `whr_embed(576621)` and a direct Workable handoff. It also
  reaches Exact through the existing direct path.

The focused run therefore produces:

- American Battery: Career found, no verified Job List;
- ClassWallet: Career found, no verified Job List;
- ESR Group: S7 Exact through the existing direct Workable link;
- Symmetrio: S7 Exact through the existing direct Workable link;
- replay: 4/4;
- wrong URL, cross-company and cross-tenant publication: 0.

iClassPro is another positive control: its page contains `whr_embed(608643)`,
but the existing Paylocity/direct evidence path already recovers the Exact
opening. It is not a numeric-embed recovery case.

## Decision

Mention Me supplies the third independent numeric-only recovery case, so the
provider-family implementation gate is satisfied. `.253` implements a
runtime-only `widget:<account_id>` board backed by the official Workable widget
inventory and strict provider-published employer evidence.

Accepted artifacts:

- recovery: `/private/tmp/v253-workable-numeric-accepted-run3`;
- positive controls: `/private/tmp/v253-workable-positive-controls-run2`.

Both sets are 3/3 Exact and 3/3 replay. The complete contract and safety audit
are recorded in `docs/COORDINATOR_V253_WORKABLE_NUMERIC_EMBED_PHASE_C.md`.
