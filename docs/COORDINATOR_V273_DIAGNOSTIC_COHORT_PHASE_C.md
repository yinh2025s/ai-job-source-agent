# v273 Backend Diagnostic Cohort - Phase C

## Decision

Accept the frozen `.270` run as deterministic development evidence and stop
opening new cohorts in this release cycle.

The run contains no executable failure cluster that covers at least three
independent companies with one observable trigger, one production code path
and an expected generic recovery of at least three records. No new heuristic,
provider or scheduler change is selected from `.273`.

## Frozen Run

Input:

`/private/tmp/v273-diagnostic-input.json`

- input SHA-256:
  `d10afae92b17c43604192ca8e4240c77f337acf950ce42a77a5ad36aa6a7fc79`;
- records: 30;
- independent companies: 30;
- unique LinkedIn job IDs: 30;
- prior diagnostic company and job-ID overlap: 0;
- sealed holdouts read: false;
- product adapter version: `2026-07-27.270`;
- candidate discovery engine: `stage_v1`.

Artifacts:

`/private/tmp/v273-diagnostic-run1`

Frozen release archive:

`artifacts/releases/v273-diagnostic-20260727-run1.tar.zst`

SHA-256:

`ae9fdc4d23da1ab3493326ab60f39df00b4a7eebb7b513f8295f3ab2027233cc`

## Results

- Website: 26/30;
- Career: 22/30;
- verified Job List: 18/30;
- S7 Exact: 13/30;
- raw Exact rate: 43.3%;
- verified official inventory no match: 3;
- external access blocked: 2;
- retryable transport failure: 2;
- unsupported provider capability: 1;
- unresolved discovery or identity: 9.

This development cohort has no eligibility annotations, so eligible Exact
recall and annotated Exact precision are not reportable. The independent
artifact audit accounts for every URL-bearing record instead.

## Exact Safety Audit

All 13 published openings preserve company, hiring relationship, provider,
tenant, title, location and canonical opening identity:

| Company | Provider / tenant | Result |
| --- | --- | --- |
| Lacoste | DigitalRecruiters `careers.lacoste.com` | Account Executive, New York |
| Reddit, Inc. | Greenhouse `reddit` | Machine Learning Engineer, Ads Optimization |
| Sony Interactive Entertainment | Greenhouse `sonyinteractiveentertainmentglobal` | Software Engineer I, Los Angeles |
| Collective | Ashby `collective` | Software Engineer (New Grad), San Francisco |
| Genius AI | Greenhouse `glossgenius` | Software Engineer - All Levels, New York |
| DHL | Phenom `DPDHGLOBAL` | Legal Counsel, Plantation |
| STEAMe | JazzHR `steamellc` | Product Designer, Chicago |
| EnsoData | Workable `ensodata` | UX Designer, Madison |
| Holland America Line | Oracle HCM `eicl/HAGroup` | UX Designer, Seattle |
| IMG | JazzHR `img` | UX Designer, Indianapolis |
| IAC Group | Breezy `international-automotive-components` | Program Manager, Southfield |
| Draper | Workday `draper/Draper_Careers` | Program Manager II, Clearfield |
| Xylem | Workday `xylem/xylem-careers` | Program Manager, Cheektowaga |

Tessera Labs also produced an exact-title, location-compatible Ashby candidate,
but only an unverified tenant probe connected the board to the company. S7
rejected it with `PROVIDER_RELATIONSHIP_UNVERIFIED`, and no opening URL was
published.

Audit totals:

- safe published Exact: 13;
- correctly rejected URL-bearing candidate: 1;
- wrong URL: 0;
- wrong location: 0;
- cross-company: 0;
- cross-tenant: 0.

## Replay

The automatic same-version bundle exported and replayed all 30 records:

- selected/exported/replayed: 30/30;
- overall replay status: success;
- mismatch: 0;
- fixture gap: 0;
- dropped record: 0;
- request-plan or tape divergence: 0.

## Causal Reclassification

The 17 records without a verified opening split into distinct executable
causes:

| Root cause | Companies | Count |
| --- | --- | ---: |
| Ashby tenant relationship unproven | Tessera Labs, Cape | 2 |
| Official parent/group site requires relationship evidence | Toyota North America, Tardus Wealth Strategies | 2 |
| Website verification timeout | Paradigm Talent, The McKinnon Co., Inc. | 2 |
| Official-host access denial | Fisher Investments, US Army Corps of Engineers | 2 |
| Wrong website accepted before authoritative evidence | SLB | 1 |
| Oracle HCM result cap unsupported | Macy's | 1 |
| SuccessFactors visible-location evidence not reconciled | Hawaiian Electric | 1 |
| iCIMS detail/location evidence incomplete | Great Day Improvements | 1 |
| iCIMS intro route unsupported | Highgate | 1 |
| Custom Humanic/APEX inventory unsupported | Gulf Copper | 1 |
| Corporate LVMH handoff not relationship-qualified | GIVENCHY | 1 |
| Official web inventory unavailable under current contract | CHAMP | 1 |
| Verified official inventory no match | Marvin | 1 |

The apparent stage-level groups are not valid causal clusters. The three
`JOB_BOARD_NOT_FOUND` records use three different protocols. The four
transport/access records split into retryable resolver timeouts and persistent
official-host 403 denials. The three nominal no-match results contain one
genuine no-match and two distinct location/detail evidence gaps. No group meets
the three-company implementation threshold.

## Full Release Gate

The first full-suite run exposed two stale test fixtures and one sandbox
loopback denial. The fixtures were updated to provide the employer and location
evidence required by the already-frozen identity contract; product code did not
change. The full gate was then rerun with loopback permission:

- CPython release baseline: 3.12.6;
- unit tests: 2834 passed, 4 skipped;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 48 native adapters, 0 issues;
- `git diff --check`: passed.

## Closure

`.273` is the final new live cohort in this release cycle. After this report,
work moves directly to ownership review, grouped commits and push. Plugin work,
authenticated External Apply, coordinator-v2 default migration, LLM and sealed
holdouts remain outside this backend release closure.
