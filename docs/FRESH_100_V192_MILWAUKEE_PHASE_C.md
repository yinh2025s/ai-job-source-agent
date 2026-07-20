# Fresh 100 `.192` First-Party JSON POST Phase C

## Frozen Run

- Code commit: `6ada919a02f62f769a9a96ff97c9238b9986cf10`
- Adapter version: `2026-07-20.192`
- Source cohort: July 18 fresh 100, ordinal 54
- Record: Milwaukee Tool, `UX/UI Product Designer`, Brookfield, WI
- Isolation: new checkpoint, completion, evidence, snapshot, replay, and output
  roots below `/private/tmp/fresh100-v192-milwaukee-20260720-run1`
- Resume policy: disabled; workers: 1

The code stayed frozen for live and same-version replay. The complete run archive
is `artifacts/releases/fresh100-v192-milwaukee-20260720-run1.tar.zst`, SHA-256
`2a01cb948a29f054cae496c3881831ddf3a967a0afa91d3546618f9fce3351e9`.
This focused run does not replace or alter the `.188` frozen-100 aggregate.

## Result

The live run completed 1/1 in 36.6 seconds:

| Field | Audited result |
| --- | --- |
| Website | `https://www.milwaukeetool.com` |
| Career | `https://www.milwaukeetool.jobs` |
| Job List | `https://www.milwaukeetool.jobs/JobSearch` |
| Opening | `https://www.milwaukeetool.jobs/Jobdetails?reqNumber=R75328` |
| Terminal | `EXACT` |

The verified anonymous JSON POST returned a complete 294-record inventory. The
selected record has the exact title `UX/UI Product Designer`, location
`Brookfield, Wisconsin, United States of America`, and requisition `R75328`.
The final identity chain verifies Milwaukee Tool as both source company and
hiring entity, a single generic tenant rooted at the canonical Job Search page,
and the same-tenant opening URL.

An independent post-run URL audit received HTTP 200 from the opening and found
the same title, location, company, and requisition in the official page. The
page's Apply action targets Milwaukee's Workday opening for the same requisition.
Wrong-opening, cross-company, and cross-tenant false positives are all zero.

## Replay Gate

Same-version scoped replay reproduced 1/1 outcome and identity chain:

- mismatch: 0
- fixture gap: 0
- replayability drop: 0
- missing result or trace: 0

The bundle manifest records adapter version `.192`, full record integrity, and
`outcome_equal` for the original and replayed Exact result.

## Cluster Decision

The executable cluster was not "S5" in general. Its shared trigger was a
verified first-party listing page plus one same-origin static asset declaring a
public literal JSON POST inventory, where the old declared-inventory layer did
not execute that transport. The generic fixture and negative controls pass, and
the frozen positive record recovers 1/1 without weakening URL or identity gates.
This closes that one causal cluster.

CHAMP and NextPlay remain separate S2 transport records from `.191`; `.192`
makes no closure claim for them. The next implementation round returns to the
`.189` causal ledger, beginning with the bounded verification-allocation cluster
005/029/033 whose acceptance remains strict 3/3 under the same three-slot budget.
