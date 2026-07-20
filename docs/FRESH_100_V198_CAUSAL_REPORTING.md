# Fresh 100 `.198` Causal Reporting Gate

## Purpose

This iteration changes evaluation terminology and selection only. It does not
change website, provider, title, location, tenant, or opening validation.

Automatic aggregation can observe that records failed at the same stage with
the same reason code. It cannot prove that those records share a trigger or
code path. The old `failure_clusters` output therefore violated the causal
classification contract and could include records that eventually recovered an
Exact opening through another route.

## Contract

- Automatic `(stage, provider, reason_code)` output is named
  `stage_failure_groups`.
- Exact-opening records are excluded from stage failure groups even when an
  upstream stage retains diagnostic failure evidence.
- Stage funnel and reason-code counts continue to expose all attempted-stage
  diagnostics.
- A causal cluster is documented only after request-tape evidence establishes
  one trigger, one shared code path, and a batch acceptance floor.

## Evidence

The focused `.197` Versana run 2 is the motivating regression case. S2 retained
a LinkedIn HTTP 451 diagnostic, while the independent provider route verified
Lever tenant `Versana`, full inventory, exact title, exact `Raleigh, NC`
location, and canonical opening URL. Its terminal outcome is Exact and must not
appear in a failure cluster.

Focused live root:

`/private/tmp/fresh100-v197-lever-case-20260720-run2`

Replay outcome: `1/1 reproduced`, `0 mismatch`, `0 fixture gap`.

Read-only archive:

`artifacts/releases/fresh100-v197-lever-case-20260720-run2.tar.zst`

SHA-256:

`358a793bd42891e2ead045d79b9a29a5f3be7f3da02658f46df0329046a15fbc`

## Causal Ledger Correction

The former seven-record public/institutional cluster is invalid:

| Cluster | Records | Acceptance |
| --- | --- | --- |
| Authoritative public-domain registry | 011, 041, 043, 045 | 4/4 source-backed government roots; zero identity collisions. |
| Nested public agency namespace | 008, 023 | 2/2 agency-specific identities from verified parent namespace evidence. |
| Education identity unconfirmed | 024 | No implementation or score until authoritative ground truth exists. |

The old `5/7` acceptance target is retired because success across unrelated
paths would not demonstrate a common repair.

## Gate

The evaluation and Markdown report tests must prove that recovered Exact
records remain visible in stage diagnostics but are absent from
`stage_failure_groups`. Before the next behavioral implementation, two sealed,
zero-overlap blind holdout cohorts must exist, and the selected development
cluster must reproduce across at least three companies.

Phase B offline gates pass: 2512 tests (4 skipped), 25/25 provider benchmark,
6/6 resolver benchmark, and 46 native adapters with zero architecture issues.
The first sandboxed run was invalid because loopback socket binding was denied;
the complete gate was rerun without that restriction and passed without
skipping the extension bridge HTTP test.
