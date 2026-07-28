# Fresh100 `.280` First-Party Visible Detail Identity - Phase C

Date: 2026-07-28
Release adapter: `2026-07-28.280`
Decision: **accepted**

## Implemented Contract

The generic opening matcher now has one bounded fallback after every existing
structured posting parser returns no verified result. It can construct a
page-bound opening candidate only when:

- the selected route is a generic first-party board and the candidate remains
  on the same registrable site;
- the candidate URL has a specific job-detail shape rather than a listing or
  search-page leaf;
- the page is at most 2 MB and its bounded visible-content parser completes;
- exactly one non-navigation H1 passes the existing publication title identity
  matcher;
- the target city occurs near the role heading or after an explicit workplace
  or location label;
- an immediately adjacent state name or code, when present, agrees with the
  target location;
- the page does not contain existing closed-opening evidence.

`head`, navigation, header, footer, scripts, styles, templates, hidden and
`aria-hidden` content do not contribute visible identity evidence. The matcher
only constructs a candidate; the existing S5 route authorization and S7
company, provider, tenant, board, opening, title and location chain still own
publication.

No company, domain, provider or job-ID special case was added.

## Captured-Page Gate

The production matcher was exercised against the already captured public
detail HTML for all four positive records and three real location controls.

| Record | Expected | Result |
| --- | --- | --- |
| WalkMe - DevOps Engineer; Detroit | Exact detail | passed |
| StatRad - DevOps Engineer; San Diego | Exact detail | passed |
| Aiken House - Data Scientist; Pittsburgh | Exact detail | passed |
| Canva - Enterprise Customer Success Manager; Austin | Exact detail | passed |
| RLB - Project Manager; Honolulu vs Singapore page | reject | passed |
| WENDEL - Project/Construction Manager; Albany vs Eau Claire page | reject | passed |
| System One - Project Manager; Beaumont vs Pittsburgh page | reject | passed |

Synthetic controls additionally reject location text present only in the
document head, header, navigation or footer; duplicate matching H1 elements;
same-city conflicting-state evidence; closed postings; cross-site details; and
generic listing/search URLs.

## Focused Live And Replay

A new code-frozen four-record run used isolated checkpoint, completion,
evidence, snapshot, failure-bundle and replay roots:

`/private/tmp/v280-visible-detail-focused-live-20260728-run2`

| Metric | Result |
| --- | ---: |
| Live records | 4/4 |
| Website | 4/4 |
| Career | 4/4 |
| Verified Job List | 4/4 |
| S7 Exact | **4/4** |
| Full replay coverage | 4/4 |
| Reproduced | **4** |
| Mismatch | **0** |
| Fixture gap | **0** |
| Budget recovery | **0** |
| Replayability drop | **0** |

All four live results have `identity_assertion.verdict=verified`. Their
canonical openings are:

- `https://www.walkme.com/jobs/devops-engineer-2`
- `https://www.statrad.com/career/devops-engineer`
- `https://aikenhouse.com/job/data-scientist`
- `https://www.lifeatcanva.com/en/jobs/6000000001099097/enterprise-customer-success-manager`

Company, title, target city, provider, tenant, canonical board and opening URL
were checked for every record. Wrong URL, wrong company, wrong location,
cross-site and cross-tenant publication are zero.

The historical `.278` WalkMe tape correctly diverges when replayed under
`.280`, because the new Exact stops before 14 requests belonging to the old
failure path. It is not counted as a passing replay. The fresh `.280` capture
above supplies the required same-version tape and reproduces 4/4.

## Release Gates

- focused matcher/S7/stage slice: 280/280;
- full suite: 2,857 passed, 4 skipped;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 48 native adapters, 0 issues;
- focused artifact JWT/Google/AWS credential-shape scan: 0;
- tracked-source credential-shape scan: 0;
- `git diff --check`: passed.

The first full-suite invocation reproduced the known sandbox denial for a
temporary loopback extension-bridge bind. The identical permission-enabled
offline run passed; no external service was used by that test.

## Measurement Boundary

WalkMe and StatRad belong to the current Fresh100 development cohort, so this
focused gate proves two expected recoveries in that cohort. Aiken House and
Canva provide the third and fourth independent companies from separate
development captures. The immutable `.278` Fresh100 score remains 31 Exact;
this focused result does not rewrite it as 33.

No sealed holdout, plugin, coordinator-v2 or LLM branch was opened. A future
code-frozen full measurement is required before reporting a new Fresh100 raw
score.
