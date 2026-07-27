# v253 Workable Numeric Embed - Phase C

## Decision

Accepted as a generic three-company provider-family closure.

American Battery Technology Company, ClassWallet and Mention Me expose the
same observable trigger on independent first-party Career pages:

- the exact Workable-owned `https://www.workable.com/assets/embed.js`;
- one executable, unambiguous numeric `whr_embed(<account_id>)` call;
- the `whr_embed_hook` target;
- no stronger canonical Workable board anchor required for recovery.

No company, domain, account ID, job ID or title is present in production code.

## Implemented Contract

The Workable adapter now creates a runtime-only board identity
`widget:<account_id>` from the verified first-party page. It reads the official
bounded widget inventory endpoint and accepts it only when:

- request and final URL remain exact HTTPS Workable host/path/query;
- JSONP callback, JSON structure, account response and record fields are
  structurally valid;
- every opening URL, shortlink and application URL agrees with its shortcode;
- inventory records are unique and provider-published employer evidence exists
  for every opening.

Generic `apply.workable.com/j/<shortcode>` URLs no longer produce the false
tenant `j`. They can reach S7 only through a complete native inventory trace
that binds the selected URL to the same runtime board and employer evidence.
Runtime-only boards are not checkpointed; replay reconstructs them from the
captured first-party Career page.

## Iteration Evidence

The first `.253` live run proved the adapter path but ended 0/3 Exact:

- Job List: 3/3;
- opening selected: 3/3;
- S7: 0/3, all `RESULT_IDENTITY_MISMATCH`.

The common root cause was the generic-to-native provider promotion contract:
it accepted detail-verified openings but not a complete provider inventory
bound to a runtime page-evidenced board. The contract was extended generically
with `board_identity`, `inventory_verified_opening_urls` and per-opening
employer evidence. Identity checks were not relaxed.

## Accepted Live And Replay

Recovery artifact:
`/private/tmp/v253-workable-numeric-accepted-run3`

| Company | Runtime tenant | Exact opening | Location |
| --- | --- | --- | --- |
| American Battery Technology Company | `widget:708590` | `/j/1D9265951C` | Reno, Nevada, United States |
| ClassWallet | `widget:564001` | `/j/E0BED61A9E` | Remote; United States |
| Mention Me | `widget:149632` | `/j/EA1650B1D6` | London, England, United Kingdom |

Result:

- verified Website/Career/Job List: 3/3;
- S7 Exact: 3/3;
- automatic same-version replay: 3/3;
- wrong URL, company, tenant, title or location: 0;
- closed-opening publication: 0;
- fixture gap, tape divergence or missing boundary: 0.

Positive-control artifact:
`/private/tmp/v253-workable-positive-controls-run2`

ESR Group, Symmetrio and iClassPro remain 3/3 Exact and replay 3/3. ESR and
Symmetrio retain their stronger direct Workable paths; iClassPro retains its
Paylocity path.

## Offline Gates

- scoped adapter, identity, pipeline, board and snapshot tests: 169 passed;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 47 native adapters, 0 issues;
- scoped `git diff --check`: passed.

The full test suite was not run for this bounded adapter and identity-trace
contract change. The existing shared fetcher still applies response-size
checks after download; moving the cap into streaming transport remains a
separate cross-provider hardening item and is not justified by this
three-company recall cluster.

## Scope

This development cohort does not belong to Fresh100, so no Fresh100 aggregate
score changes. Plugin work, authenticated External Apply, coordinator-v2, the
LLM branch and sealed holdouts remain frozen.
