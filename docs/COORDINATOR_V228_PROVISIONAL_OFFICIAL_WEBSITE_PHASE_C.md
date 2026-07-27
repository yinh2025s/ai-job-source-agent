# Coordinator `.228` Provisional Official Website Phase C

## Decision

The original five-record Phase A cluster was too broad. Three records share
the implemented causal path and recovered a verified Career/Job Board:

- North Dakota Information Technology (NDIT)
- State of Montana
- City of Lubbock

NYC Department of Social Services recovered through ordinary S2 resolution,
not provisional evidence. Heritage Companies did not produce a qualifying
LinkedIn-official, homepage-verified provisional candidate. Those two records
are removed from this cluster rather than counted as failures of the new path.

## Implementation

`ProvisionalWebsiteEvidence` retains a currently verified LinkedIn-official
site for exploration without publishing it as the company Website. S4 may
establish hiring identity only through either an exact-host Career page or a
typed `HomepageNavigationEvidence` link observed on that verified homepage.
An observed public HTTP navigation link is converted to an HTTPS candidate
only after credentials, private hosts, non-default ports, query strings,
fragments and unsafe content are rejected. The converted URL is not trusted as
an opening: provider, tenant, inventory and S7 checks remain mandatory.

The first Lubbock run exposed a checkpoint integration defect. The merged S5
portfolio republished the unchanged S4 provisional hiring identity, and the
checkpoint anti-forgery guard correctly rejected a provisional identity being
created outside S4. `.228` avoids writing an unchanged identity in the merge;
the checkpoint rule was not relaxed.

## Focused Live

Artifacts:

- `.227` five-record cold run:
  `/private/tmp/fresh5-v227-provisional-nav-20260723-run2`
- `.228` isolated Lubbock verification:
  `/private/tmp/fresh1-v228-lubbock-20260723-run1`

| Record | Result | Causal assessment |
| --- | --- | --- |
| NYC DSS | S7 Exact | ordinary S2 recovery; not part of this cluster |
| NDIT | verified PeopleSoft Job Board, verified inventory no-match | provisional exact-host Career recovery |
| State of Montana | verified Career and official state Job Board; opening fetch incomplete | provisional exact-host Career recovery |
| City of Lubbock | verified GovernmentJobs board, tenant `lubbock`; opening fetch incomplete | observed homepage HTTP Career handoff upgraded to an HTTPS candidate |
| Heritage Companies | `WEBSITE_NOT_RESOLVED` | no qualifying provisional candidate; reclassified |

NYC's selected opening passed company, title, Brooklyn/New York location,
generic board and canonical opening identity checks. NDIT, Montana and Lubbock
published no opening URL, so no unsupported Exact was introduced. Across both
runs, wrong opening URL, cross-company and cross-tenant publication remain
zero.

## Gates And Remaining Debt

- scoped evidence/discovery/replay suite: 441 tests passed before the focused
  live;
- checkpoint/portfolio regression after `.228`: 111 tests passed;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture gate: 46 adapters, zero issues;
- `git diff --check`: passed.

The earlier `.227` five-record capture replayed 5/5 before the navigation
enhancement. The `.228` Lubbock run reached a typed GovernmentJobs board, but
its full replay currently reports four unconsumed S6 terminal fetch entries.
Live S6 had only the reserved opening window left and ended with
`PROVIDER_FETCH_FAILED`; replay executes the upstream tape faster and does not
consume the same deadline terminals. This is a separate phase-budget/replay
determinism cluster, not provisional identity failure.

No full Fresh100, Frozen100 or sealed holdout was run in this phase.

## Next Cluster

Analyze the common trigger for verified Job Boards whose S6 work reaches the
reserved deadline, then make live and replay consume the same typed phase
boundary. Do not increase global timeouts or weaken provider/tenant/S7 gates as
the first response. Heritage returns to candidate-production analysis; NYC is
closed by its current verified Exact but is not evidence for this feature.
