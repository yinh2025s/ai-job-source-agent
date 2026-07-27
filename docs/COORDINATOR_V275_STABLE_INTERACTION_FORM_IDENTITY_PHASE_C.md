# v275 Stable Interaction Form Identity Phase C

Date: 2026-07-28
Implementation commit: `00c17a9`
Product adapter: `2026-07-28.275`
Decision: **accepted; 3/3 complete-inventory recoveries**

## Scope

This gate reused only the three frozen GovernmentJobs records qualified in
Phase A:

- City of Lubbock;
- City of College Station;
- State of Hawaiʻi.

It did not create or open a new cohort. Code was frozen before live, and the
run used new checkpoint, completion, evidence, snapshot, failure and replay
roots under:

`/private/tmp/v275-governmentjobs-focused-run1`

## Focused Live

All three records completed. Each retained its verified GovernmentJobs board
and tenant, discovered `class:search-form`, executed the declared same-origin
search action, and recorded:

```text
interaction status: submitted
change kind: route
inventory variant: governmentjobs_public_xhr_html
inventory scope: title_filtered
inventory complete: true
```

| Employer | Tenant | Inventory | Current title result |
| --- | --- | --- | --- |
| City of Lubbock | `lubbock` | complete | 0 matching records |
| City of College Station | `cstx` | complete | 0 matching records |
| State of Hawaiʻi | `hawaii` | complete | 0 matching records |

The three current official title-filtered inventories therefore produced
evidence-backed `OPENING_NOT_FOUND`. No opening URL was published. Wrong URL,
wrong location, cross-company and cross-tenant publication remained zero.

This satisfies the Phase A recovery contract: `.274` failed before browser
interaction on 3/3 records, while `.275` reached complete official inventory
on 3/3. The gate did not require a closed or absent posting to be fabricated
as Exact.

## Replay

The automatic same-version replay bundle passed:

- 3 selected, exported and replayed;
- 3 reproduced;
- 0 expected transitions;
- 0 fixture gaps;
- 0 mismatches.

The replay identity chains preserved the three distinct tenants and canonical
boards. Marker-bearing interactions are fingerprinted in request identity and
the scoped outcome tape.

## Safety Review

An independent read-only review initially found unstable concatenated markers,
positional-constructor compatibility, incomplete fallback trace and replay
coverage gaps. Before live, the implementation was revised to:

- prefer exact `id`, `data-testid` or `aria-label` markers;
- use one deterministic class token when no stronger marker exists;
- require exactly one matching form across at most 32 forms;
- revalidate the exact query field, declared action and submit control before
  fill or click;
- preserve legacy markerless ordinal behavior and fingerprint identity;
- retain interaction evidence across static fallback failures.

The review then reported no rollout blocker. Scoped tests passed 367/367.

## Integrated Offline Gate

The one permitted full release gate passed after focused acceptance:

- CPython 3.12.6 runtime check;
- 2,839 tests passed, 4 skipped;
- provider benchmark 25/25;
- resolver benchmark 6/6;
- architecture validation 48 adapters / 0 issues;
- `git diff --check` passed.

## Artifact

The immutable focused archive is:

`artifacts/releases/v275-governmentjobs-focused-20260728-run1.tar.zst`

SHA-256:

`6d10d57b63855b271b173a94afa8317a5e6e5e942657d3f864166ff6338f723f`

The archive payload is intentionally ignored by Git; its checksum is tracked.

## Stop Decision

The `.275` GovernmentJobs cluster is accepted. No additional live cohort,
Fresh100 rerun, Frozen100 rerun or sealed holdout is started in this release
cycle. The remaining work is limited to one integrated offline release gate,
governance synchronization, grouped commits and push.

This focused recovery does not rewrite the Fresh100 `.270` score and does not
close the overall product goal.
