# v269 HMG Search-Entry Request Identity - Phase A

## Trigger

The `.268` live business outcomes were correct, but automatic replay diverged
for Kavaliro with two unconsumed entries beginning at:

`POST https://jobs.kavaliro.com/index.smpl`

The sanitized HMG search-entry page emitted an inert `t` placeholder, while the
live POST request fingerprint still included the rotating raw `t` value.

## Root Cause

HMG search-entry POST tickets share the same privacy and replay semantics as
the GET inventory `h/t` tickets, but request identity previously recognized
only:

`/json/index.smpl?arg=list_posts&pid=gwt`

## Design

Extend request-body identity only for the exact observable POST contract:

- HTTPS `/index.smpl`;
- form-encoded body;
- `arg=jb_search_results`;
- `action=1`;
- exactly one `keywords` and one non-empty `t`;
- no fields outside the provider's bounded allowlist.

Only `t` is replaced in the canonical body fingerprint. Title, search fields
and every generic form remain semantic. Ordinary `t` fields, wrong paths and
other `arg` values remain unchanged.

## Acceptance

1. Rotating HMG search tickets produce the same request fingerprint.
2. Different titles produce different fingerprints.
3. Generic `t` forms and wrong paths remain distinct.
4. Related tests and offline provider/resolver/architecture gates pass.
5. A fresh isolated `.269` live/replay reproduces all three Haley outcomes
   with zero tape divergence.
6. URL and page-body audits find zero raw HMG tickets.

Plugin work, authenticated External Apply, coordinator-v2, LLM and sealed
holdouts remain frozen.
