# Fresh100 `.210` Trusted Opening Search Scheduling Phase A

## Reclassification

The 18 terminal S2 timeouts are not one executable cluster. They mix LinkedIn
source timeout, search-source timeout, candidate endpoint timeout, TLS/DNS
failure and exhausted caller deadline. Two additional records already bypassed
S2 through ATS evidence. A blanket timeout increase or retry of speculative
domains is rejected.

The eight `OPENING_DISCOVERY_INCOMPLETE` records are also not one cluster.
They split into embedded ATS promotion, strict detail-location evidence,
provider coverage, rendered inventory and safe pagination. Neither
`max_job_board_attempts=3` nor `evaluate_all_candidate_routes=false` caused the
eight outcomes as a group.

## Selected Trigger

At least Sentar, Crawford Thomas Recruiting and Crosby share a narrower generic
opening-search path:

1. S2-S5 already publish a verified first-party Job List.
2. The reused landing page contains no directly verifiable target opening.
3. `JobOpeningMatcher` performs JS declared-inventory asset discovery on that
   unfiltered landing page before attempting its official full-title query
   routes.
4. Asset fetches consume the bounded S6 deadline; the first useful title query
   receives only about 4-5 seconds or is never attempted with retry capacity.
5. The terminal timeout therefore occurs after the correct Job List candidate
   already exists.

WENDEL also traverses this generic path but performs several slow official page
requests and is not guaranteed to recover. City of College Station reaches a
typed GovernmentJobs board and repeated HTTP 500; it does not belong to this
implementation cluster.

## Contract

- Keep cheap link and structured-data extraction from the reused landing page
  first.
- Defer expensive JS asset/declared-endpoint inventory discovery for only the
  unfiltered reused landing page.
- Attempt direct, declared and provider-fallback full-title routes before that
  deferred JS work.
- Declared GET, interactive and title-filtered routes may perform their own JS
  inventory discovery immediately because their response is already scoped to
  the target query.
- If no verified match is found, run the deferred landing-page JS discovery at
  most once before verified site search, provided the caller deadline remains.
- Preserve canonical URL, same-site, hiring organization, strict title,
  location, provider, tenant and S7 gates. Search order cannot authorize a URL.
- Preserve incomplete/blocked/retryable trace semantics. No company, domain,
  tenant or job-ID exception is allowed.

## Acceptance

Focused unit tests must prove:

1. A full-title direct route executes before landing-page JS assets and can
   return without fetching those assets.
2. If direct routes do not match, deferred landing-page JS executes once and
   retains its prior verified-result behavior.
3. Wrong-location and cross-site candidates remain rejected.

Phase B runs only opening-matcher tests and affected replay fixtures. Phase C
uses a clean focused live cohort containing Sentar, Crawford Thomas Recruiting,
Crosby and WENDEL. Closure requires at least three independent live recoveries
or equivalent verified opening evidence with zero wrong URL/company/location/
tenant result. Otherwise the cluster definition is rejected; no third generic
architecture repair is started under the current goal.
