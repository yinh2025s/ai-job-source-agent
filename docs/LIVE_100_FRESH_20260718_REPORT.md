# Fresh 100-Posting Three-Route Live Evaluation

Run date: 2026-07-18

## Cohort

- 100 distinct public LinkedIn job IDs from 95 companies.
- Zero job-ID overlap with the observed 2026-07-17 live100 cohort.
- Five previously unused query families contributed 20 postings each: DevOps Engineer,
  Cybersecurity Analyst, UX Designer, Project Manager, and Human Resources Manager.
- Public detail enrichment exposed no usable External Apply URL in 100 records.

## Product Funnel

| Stage | Count |
| --- | ---: |
| Verified website | 94/100 |
| Career page | 67/100 |
| Verified Job Board | 43/100 |
| S7 exact opening | **25/100** |

Strict seven-stage pipeline states were 25 success, 40 partial, and 35 failed. The
outer result-availability states were 42 success, 52 partial, and 6 failed because a
verified board without an exact opening remains useful output. A retry-stabilized resume
improved exact results from the initial 9 to 25; the final artifacts below are the resumed
results.

## Three-Route Attribution

| Route | Input coverage | Candidate | Relationship verified | Attributed exact |
| --- | ---: | ---: | ---: | ---: |
| external_apply | 0/100 | 0 | 0 | 0/100 |
| provider_search | 100/100 | 1 | 1 | 0/100 |
| website_career | 94/100 | 43 | 42 | 25/100 |

The corrected route evaluator reports a 25/100 OR-union, matching the product pipeline's
25/100 S7 exact openings. The original report under-counted six valid exact results because
a verified same-origin generic board could not attribute its deeper selected board URL.
The evaluator now permits only same-origin ancestor/descendant refinement with a matching
generic tenant identity; cross-host, cross-provider, forged-tenant and sibling paths remain
rejected. All 100 route traces are structurally valid.
Provider-targeted search produced only one verified candidate and no exact result in this
run, a substantial regression from the prior observed cohort that requires failure-cluster
analysis before attributing it to algorithm quality or search transport availability.

## Non-Exact Classification

| Reason | Count |
| --- | ---: |
| CAREER_PAGE_NOT_FOUND | 27 |
| JOB_BOARD_NOT_FOUND | 21 |
| NETWORK_TIMEOUT | 9 |
| OPENING_DISCOVERY_INCOMPLETE | 5 |
| FETCH_FAILED | 3 |
| OPENING_NOT_FOUND | 3 |
| RESULT_IDENTITY_MISMATCH | 2 |
| COMPANY_TIME_BUDGET_EXHAUSTED | 2 |
| HTTP_FORBIDDEN | 1 |
| NO_PUBLIC_OPENINGS | 1 |
| PROVIDER_VARIANT_UNSUPPORTED | 1 |

These are automated outcomes, not manual ground truth. Closed postings, unavailable public
inventory, network failures, and genuine system gaps must be separated during review.

## Post-Run Remediation

The frozen 100-record funnel above was not rewritten after development. Targeted live
regressions demonstrate the following generic recoveries:

- Jushi Holdings: embedded official Lever inventory to exact opening.
- Aramark: explicit Search Jobs page, declared same-origin JSON inventory, exact title and
  location, SuccessFactors `ARAMARKPROD` tenant, and S7 exact opening.
- Stuller: native SaaSHR/UKG Ready inventory to exact opening.
- City of Lubbock and City of College Station: native GovernmentJobs/NEOGOV verified boards.
- OneApp: Pinpoint/Ashby evidence reaches a verified board; the observed target opening is
  not asserted exact.
- Equifax: a reserved, strictly same-site official Careers destination reaches the
  customer-owned portal and exact UX Designer opening.
- Team Royal: an exact `Careers` handoff reaches `/work-with-us/`; matching BambooHR
  `data-domain` and `embed.js` evidence binds tenant `royal`, whose complete inventory
  returns the exact Project Manager opening and passes S7.
- WalkMe and StatRad: bounded semantic first-party job cards recover their exact DevOps
  openings without claiming complete generic inventory.
- Aperia: an observed first-party HTTP Greenhouse anchor is upgraded only to the exact
  HTTPS ATS host and then validated by the native adapter, recovering exact.
- Ivo and Steampunk: strict Ashby posting-API and hosted iCIMS root variants recover exact.
- Adapture: the slugless Paylocity UUID tenant now yields a complete official inventory;
  the target role is absent, so `OPENING_NOT_FOUND` is the correct outcome.

GovernmentJobs, Pinpoint and SaaSHR are provider adapters, not company mappings. The same
round also moves global Search Jobs / View all open roles ahead of department-scoped links,
adds bounded first-party anonymous GET inventory attestation, covers SuccessFactors' legacy
`jobId` detail key, and restores the provider-search query plan's Lever/Ashby/SmartRecruiters
families within the existing five-query budget.

## Replay Note

The original optional full-outcome replay stopped on Hawaiian Electric because cache-derived
S2 website evidence did not reconstruct its producer state. Scoped replay now seeds only
trace-attested cache evidence through the existing atomic store and preserves strict
unconsumed-tape checks. Both Hawaiian Electric records replay without an execution or tape
divergence; no request tape was fabricated.

The next targeted slice adds ApplicantPro, CATS One, PeopleSoft and WP Job Manager adapters plus a strict
same-origin HTML inventory transport declared by first-party JavaScript. Northern Clearing
and Alaska Commercial Company recover exact openings. North Dakota IT reaches its verified
PeopleSoft board without inventing a missing target opening. Conrad reaches its official job
list and executes the declared `Project Manager` search; 48 current candidates are parsed
with location evidence, but none match the frozen Toledo location, so it correctly remains
partial and no Scotland/Michigan URL is published. Dechert reaches its declared official
inventory. SDS International reaches its official WP Job Manager list, executes the
first-party title/location POST contract, and returns `OPENING_NOT_FOUND` from a complete
filtered empty response instead of `JOB_BOARD_NOT_FOUND`. These are targeted validations and
do not rewrite the frozen 94/67/43/25 funnel.

Final offline gates after this targeted round: 1985 tests passed (3 skipped), the provider
benchmark passes 25/25, the resolver benchmark passes 6/6, architecture validation reports
41 native adapters and zero issues, and `git diff --check` is clean.

## Artifacts

- `samples/evaluation/live100_fresh_cohort_20260718.json`
- `samples/evaluation/live100_fresh_three_route_metrics_20260718.json`
- `samples/evaluation/live100_fresh_records_20260718.csv`
- `docs/LIVE_100_FRESH_20260718_MANUAL_REVIEW.md`
