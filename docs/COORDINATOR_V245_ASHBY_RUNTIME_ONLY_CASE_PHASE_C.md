# v245 Ashby Runtime-Only Tenant - Phase C

## Result

The shared contract defect is closed.

`AshbyAdapter.identify_board()` now preserves the case-sensitive tenant and
marks a board replay-safe only when the tenant is lowercase. The central Ashby
durable-locator policy is unchanged. A mixed-case locator may be used for live
provider verification but cannot be serialized into a checkpoint portfolio.

Scoped replay now treats a typed runtime-only provider locator as producer
state. When replay would otherwise start after S5, it replays the captured
website, Career and job-board producer chain. It does not invent a durable
tenant or restore an unsafe checkpoint payload.

## Focused Live

Artifacts: `/private/tmp/v245-ashby-mixed-run1`

| Company | Target | Provider / tenant | Result |
| --- | --- | --- | --- |
| Oso | Software Engineer; New York, NY | `ashby` / `Oso` | S7 Exact |
| Blossom | Software Engineer (All Levels); New York, NY | `ashby` / `Blossom-Health` | S7 Exact |

Both openings came from verified first-party Career handoffs and complete
native Ashby inventory. The selected titles and locations overlap the source
postings, and both opening URLs remain on the verified tenant.

## Replay

Clean bundle: `/private/tmp/v245-ashby-mixed-replay2`

- exported and compared: 2/2;
- reproduced: 2/2;
- mismatch: 0;
- fixture gap: 0;
- tape divergence: 0;
- post-live evidence store reused: no.

The replay preserved company, title, location, provider, tenant, canonical
board and canonical opening URL for both records.

## Gates

- relevant integrated tests: 298 passed;
- production provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 46 adapters, 0 issues;
- `git diff --check`: clean.

The full test suite was not rerun because the change is limited to the Ashby
runtime flag and scoped replay boundary.

## Projection

Blossom restores an existing Frozen100 audited Exact that regressed under
`.244`. Oso is diagnostic evidence. Neither record changes the Fresh100
development projection.

Coordinator-v2, the extension, External Apply, LLM behavior and sealed holdouts
remain unchanged.
