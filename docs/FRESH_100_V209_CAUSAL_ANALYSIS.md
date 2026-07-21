# Fresh100 `.209` Causal Analysis

## Frozen Run

- Runtime commit: `dd1de61e2ec97ca0e6ab679736040e782a433c11`
- Adapter: `2026-07-21.209`
- Run root: `/private/tmp/fresh100-v209-cold-20260721-run1`
- Input SHA-256: `fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`
- Source archive SHA-256: `e1d21ec4b61e8b44e55467ef8ccea80f3f3b5651f7057619e0b9b469f56cb8e8`

The run used clean checkpoint, completion, evidence, snapshot and output roots;
zero records were restored. Code was frozen during the live run.

## Outcome

- 100 total, 19 Exact, 38 partial and 43 failed.
- 78 verified Websites, 60 Career pages, 56 Job Lists and 19 openings.
- All 19 Exact identity chains passed company, title, accepted location,
  provider, tenant, board and opening URL audit.
- Observed wrong URL, cross-company, cross-tenant and wrong-location Exact: 0.

Full same-version replay executed 100/100 records: 95 reproduced, four accepted
budget-recovery transitions, zero fixture gaps, zero tape divergence and one
outcome mismatch. Failure replay executed 81/81 with the same one mismatch.
Consequently `.209` replay execution is complete, but replay acceptance failed.

## Exclusive Causal Partition Of 81 Non-Exact Records

| Root cause | Count | Records |
| --- | ---: | --- |
| Correct candidate present; transport failed | 5 | Sentar; WENDEL Companies (Project Manager); Crawford Thomas Recruiting; Crosby; City of College Station |
| Company budget starvation | 4 | Diamondback Energy; North Dakota Information Technology; ARUP Laboratories; HP |
| LinkedIn/search source refused | 1 | ProMach |
| Correct candidate not produced | 31 | Loveland Innovations; iClassPro; Indica Labs; Caesars; Altec; Splashlight; Tapestrii/Investigative Case Management; NYC DSS; City of Lubbock; Nisga'a Tek; Hawaiian Electric (2); FOTOMILL; University of Oklahoma; CHAMP; Frost; Milwaukee Tool; Fabric; QXO; American Fabrication; System One; Team Royal; Conrad Consulting; Rider Levett Bucknall; NextPlay Jobs; B&D Industries; WICHITA COMPANY; Jushi; State of Montana; Ken Garff |
| Candidate identity rejected | 10 | Sunbird; Target Hospitality; Mayo Clinic; STEAMe; IMG; Steampunk; Arkema (2); Aramark; Cintas |
| Provider/inventory incomplete | 11 | WalkMe; Tyler Technologies; Lorum; StatRad; Cretex; Home Depot; Necessary Ventures; OneApp; Equifax; WENDEL Companies (second record); Heritage Companies |
| Verified inventory no match | 9 | Matlen Silver; SDS International; IGNITE; PACS; Dechert; Brown and Caldwell; STRIKE; Adapture; DSV |
| Verified no public openings | 3 | Pitch Aeronautics; Prophetic; Systematic Business Consulting |
| External access blocked | 4 | Sunwest Bank; City of Pharr; Benefis; City of Sioux Falls |
| Input identity ambiguous | 2 | EnsoData; Focus |
| Unsupported provider variant | 1 | Vertiv |

The counts reconcile exactly to 81. These labels describe causal evidence, not
the stage where execution stopped.

## Executable Clusters

| Cluster | Count | Shared path | Batch expectation |
| --- | ---: | --- | --- |
| Website-resolution transport timeout | 18 | Resolver transport/deadline path | Recover up to 18; only a shared retry/deadline defect qualifies |
| Career candidate absent | 5 | S4 first-party candidate production | Recover up to 5 with broader evidence-backed discovery |
| Job-board candidate absent | 7 | S5 candidate production | Recover up to 7 without guessed-board success |
| Opening inventory incomplete | 8 | Bounded S6 portfolio evaluation | Recover up to 8 by evaluating eligible verified routes |
| Portfolio truncated | 3 | S6 `JOB_BOARD_PORTFOLIO_INCOMPLETE` | Recover up to 3 while preserving provider/tenant identity |
| Company budget exhaustion | 4 | Shared scheduling/deadline path | Recover up to 4 with bounded budget allocation |

Identity rejection, verified no-match and external blocking are not repair
targets without new valid evidence. Their shared terminal labels do not imply a
safe code change.

## Replay Mismatch Root Cause

Heritage Companies was partial with `JOB_BOARD_PORTFOLIO_INCOMPLETE` in both
live and replay. Live incorrectly published the Paylocity technical locator
`2b1b...|heritage-restaurant-group` as `hiring_entity_name`; replay recomputed
the valid source identity `Heritage Companies`.

The provider-independent `_stored_provider_relationship` fallback treated a
stored first-party handoff as proof that the provider tenant was a parent
employer. A tenant is only a provider locator. The safe repair is to preserve
verified upstream hiring identity while retaining the exact tenant solely in
`ProviderIdentity`. This applies to Paylocity, Ashby, Workday and any other
registered provider using the same stored-board path; it adds no company,
domain or job-ID exception.

## Decision

`.209` remains immutable and is not a replay-accepted release. `.210` addresses
only the generic identity-projection defect. It must pass targeted tests and a
scoped migration replay before another behavior cluster is selected. The next
recall repair must come from a cluster spanning at least three independent
companies and must state its expected batch recovery before implementation.
