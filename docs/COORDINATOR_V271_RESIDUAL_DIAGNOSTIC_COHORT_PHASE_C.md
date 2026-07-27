# v271 Backend Residual Diagnostic Cohort - Phase C

## Decision

Accept the frozen `.270` run as deterministic development evidence. Do not
implement a new heuristic or provider from this cohort alone.

No failure family currently satisfies all three requirements:

- at least three independent companies;
- the same observable trigger and production code path;
- a defensible expected recovery of at least three records.

## Results

Artifacts:

`/private/tmp/v271-diagnostic-run1`

- Website: 15/18;
- Career: 9/18;
- verified Job List: 7/18;
- S7 Exact: 5/18;
- full same-version replay: 18/18 reproduced;
- mismatch: 0;
- fixture gap: 0;
- extra or unconsumed request divergence: 0.

## Exact Safety Audit

All five published openings preserve company, provider, tenant, title and
location:

| Company | Provider / tenant | Result |
| --- | --- | --- |
| SpaceX | Greenhouse `spacex` | verified Financial Analyst, Hawthorne |
| embecta | Workday `embecta/embectacareers` | verified Quality Engineer, Holdrege |
| ektello | Haley `custom:jobs.ektello.com` | verified Business Analyst 2, Plano |
| OURA | Greenhouse `oura` | verified Clinical Research Coordinator, US remote |
| Figma | Greenhouse `figma` | verified Security Engineer with New York location |

Skydio produced a title/location-compatible Ashby candidate but lacked verified
hiring and provider relationship evidence. S7 rejected it with
`RESULT_IDENTITY_MISMATCH`, and no opening URL was published.

Wrong URL, wrong location, cross-company and cross-tenant publication are zero.

## Causal Reclassification

The five `CAREER_PAGE_NOT_FOUND` records are not one root cause:

- Nobel Biocare: regional navigation and missing verified Career handoff;
- V Group: unverified alternate topology plus TLS hostname failure;
- Prestige Staffing: verified first-party Angular job portal with no discovered
  public inventory contract;
- Hawthorn Innovations: repeated TLS/404 failures without a verified handoff;
- Simon Property Group: correct Career/provider candidate not produced.

Additional upstream records:

- The Goodkind Co: intentional parent/group identity rejection;
- Savage X Fenty: repeated official-host HTTP 403, classified as external
  access denial.

The three `OPENING_DISCOVERY_INCOMPLETE` records also split:

- Saint Laurent: Kering first-party inventory/pagination not proven complete;
- Orion Talent: bespoke self-hosted search contract;
- Samtec: official Jobvite handoff without a native Jobvite adapter.

Zing Recruiting produced no verified board candidate. Moment exposed matching
first-party and Ashby UUID routes without enough evidence to unify their
provider/tenant identity; the ambiguity rejection is correct.

## Cluster Decision

The strongest individual gaps are:

1. first-party static-shell job portals with discoverable public API/config;
2. Jobvite inventory;
3. verified regional or alternate-host Career handoffs.

Each has fewer than three confirmed independent examples in this cohort.
Sharing a final stage or generic fallback is not sufficient. The next step is
read-only historical development-artifact mining for additional examples,
followed by implementation only if one exact contract reaches the three-company
threshold.

Plugin work, authenticated External Apply, coordinator-v2, LLM and sealed
holdouts remain frozen.
