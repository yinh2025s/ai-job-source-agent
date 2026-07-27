# v268 Haley Marketing Tape Body Redaction - Phase A

## Trigger

The `.267` focused live and replay passed behavior and identity gates, but
read-only review found raw HMG `h/t` validation tickets inside shareable replay
`page.html` bodies. URL metadata was already redacted. `.267` therefore remains
behavioral evidence and is not the final privacy closure.

## Root Cause

`sanitize_url()` recognized the exact HMG inventory endpoint, while
`sanitize_snapshot_body()` treated the one-letter `h/t` fields as ordinary
content to avoid corrupting unrelated sites. HMG landing pages and inventory
responses need provider-contract-aware body sanitation.

## Design

Only bodies with the complete HMG observable contract are eligible:

- board HTML contains `hmg-jb.css`, `combobo.js` and either the
  `JBSearchList_form` inventory contract or `jb_search` search-entry contract;
- structured inventory responses contain `ResultSet.list`,
  `ResultSet.list_meta` and a `ResultSet.ticket` object.

Raw tickets are replaced with deterministic inert values that preserve the HMG
token shape:

- `h`: 32 zeroes;
- `t`: `1000000000`.

Shape-preserving placeholders are required because scoped replay must parse the
sanitized board, issue the same sanitized request identity and continue across
ticket refreshes. Generic fields named `h` or `t` remain untouched.

## Acceptance

1. Landing-page `h/t` values are absent from snapshots and replay tapes.
2. Inventory-response refreshed tickets are absent from snapshots and tapes.
3. Sanitization is idempotent and generic `h/t` fields remain unchanged.
4. Sanitized multi-page HMG fixtures remain replayable.
5. Related tests and offline provider/resolver/architecture gates pass.
6. A new isolated `.268` three-record live and replay produces the same safe
   outcomes as `.267`.
7. Recursive body and URL audits find zero raw HMG ticket values.

Plugin work, authenticated External Apply, coordinator-v2, LLM and sealed
holdouts remain frozen.
