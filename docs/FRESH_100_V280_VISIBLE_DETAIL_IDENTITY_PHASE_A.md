# Fresh100 `.280` First-Party Visible Detail Identity - Phase A

Date: 2026-07-28
Source release: `2026-07-28.279`
Decision: **qualified for one bounded Phase B**

## Root Cause

Four independent companies reach the same production path:

1. an already verified first-party generic Job Board produces a specific job
   detail URL;
2. the detail URL remains on the same registrable site;
3. the page is fetchable and does not indicate a closed opening;
4. the page visibly identifies the exact title and target location;
5. no supported structured `JobPosting` or page-bound JSON record exists;
6. `JobOpeningMatcher._select_with_verified_detail()` records
   `jobposting_identity_not_verified` and rejects the opening.

This is one observable trigger and one code path, not a shared stage label.

| Company | Target | Captured visible evidence | Current terminal | Expected transition |
| --- | --- | --- | --- | --- |
| WalkMe | DevOps Engineer; Detroit, MI | unique matching H1; Detroit in the role's multi-location line; same-site detail | `JOB_BOARD_PORTFOLIO_INCOMPLETE` | S7 Exact |
| StatRad | DevOps Engineer; San Diego, CA | unique matching H1; `Work Location and Conditions` names San Diego; same-site application | `OPENING_DISCOVERY_INCOMPLETE` | S7 Exact |
| Aiken House | Data Scientist; Pittsburgh, PA | unique matching role H1; `Full Time, Pittsburgh`; same-site Apply Now | `OPENING_DISCOVERY_INCOMPLETE` | S7 Exact |
| Canva | Enterprise Customer Success Manager; Austin, TX | unique matching H1; `Where and how you can work` names Austin, Texas; same-site detail | `OPENING_DISCOVERY_INCOMPLETE` | S7 Exact |

Evidence:

- WalkMe and StatRad:
  `/private/tmp/fresh100-current-v278-cold-20260728-run1`
- Aiken House: `/private/tmp/v246-diagnostic-run1`
- Canva: `/private/tmp/v256-diagnostic-run1`

All four captures contain `jobposting_identity_not_verified` under the same
detail-enrichment path. The implementation expectation is four terminal
recoveries.

## Selected Contract

Add one bounded visible-detail fallback after all existing structured parsers
return no verified posting. It may produce one page-bound posting only when:

- the provider is `generic`;
- the board relationship is already verified first-party;
- the candidate and fetched final URL are on the board's registrable site;
- the fetched URL is the candidate detail URL;
- the page has exactly one visible H1 whose title passes the existing strict
  title identity matcher;
- visible non-navigation job content contains the target city/location either
  near that H1 or in an explicitly labelled location/workplace context;
- the observed location passes the existing strict location identity matcher;
- the page has no closed-opening evidence.

The fallback returns the fetched canonical detail URL, the observed H1 title
and the observed location phrase. It does not use an Apply button as identity
evidence and does not authorize an external provider or tenant.

## Safety Controls

The implementation must reject:

- title-only pages with no location;
- a target location appearing only in header, navigation or footer content;
- multiple matching H1 role titles;
- a visible location that conflicts with the LinkedIn target;
- cross-site detail URLs and redirects;
- pages with closed-opening evidence;
- generic listing/search pages instead of specific detail pages.

Required real negative controls:

- RLB `Project Manager` page states Singapore, not Honolulu;
- WENDEL's page states Eau Claire, WI, not Albany or Williamsville, NY;
- System One's page states Pittsburgh, PA, not Beaumont-Port Arthur;
- a synthetic page with the target city only in navigation;
- a synthetic page with duplicate matching H1 titles.

Existing provider, tenant, hiring relationship, title, location and S7 gates
remain unchanged.

## Ownership

Mainline owns:

- `job_source_agent/opening_matcher.py`;
- adapter-version invalidation in `job_source_agent/checkpoint.py`;
- shared governance and integration.

The isolated test workstream may add only a new visible-detail matcher test
file. No provider adapter, registry, schema, sealed cohort, plugin,
coordinator-v2 or LLM branch is in scope.

## Acceptance

Phase B is accepted only if:

1. all four captured positive pages produce the expected exact canonical detail
   URL offline;
2. all real and synthetic negative controls remain rejected;
3. no existing structured `JobPosting` path changes;
4. focused scoped replay has zero fixture gap and no unsafe URL/company/tenant/
   location publication;
5. full tests, provider benchmark, resolver benchmark, architecture gate,
   credential-shape scan and `git diff --check` pass.

If fewer than three positive records recover through this one contract, the
cluster is rejected and the change must be reverted.
