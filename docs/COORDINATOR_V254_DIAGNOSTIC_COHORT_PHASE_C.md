# v254 Development Diagnostic Cohort - Phase C

## Frozen Live

Artifacts: `/private/tmp/v254-diagnostic-run1`

- product version: `2026-07-27.253`;
- records and independent companies: 30/30;
- excluded prior development job IDs: 405;
- prior overlap: 0;
- Website: 29/30;
- Career: 21/30;
- verified Job List: 17/30;
- S7 Exact: 9/30;
- elapsed: 660.6 seconds.

The run used public search-card inputs, clean isolated roots, `stage_v1`,
serial execution and frozen product code. Plugin work, authenticated External
Apply, coordinator-v2, the LLM branch and sealed holdouts remained frozen.

## Exact Safety Audit

All nine Exact results pass S7 and live/replay identity comparison:

| Company | Provider / tenant | Title and location |
| --- | --- | --- |
| Notion | Ashby / `notion` | Product Designer; San Francisco |
| Instagram / Meta | Meta Careers / `meta` | Product Designer, Instagram; Menlo Park |
| CTTX Health | Workable / `cttxhealth` | Clinical Research Associate; Cambridge |
| Krew | Ashby / `krew` | Sales Development Representative; San Francisco |
| Giga | Ashby / `gigaml` | Enterprise SDR; San Francisco |
| Middesk | Ashby / `middesk` | Sales Development Representative; New York |
| Decagon | Ashby / `decagon` | SDR; San Francisco |
| KPFF Consulting Engineers | SmartRecruiters / `KPFFConsultingEngineers` | Entry Level Civil Engineer; Sacramento |
| Ardagh Group | Phenom / `ARGRUS` | EHS Specialist; Bridgeton |

Safety totals:

- wrong or non-specific URL: 0;
- cross-company or cross-brand publication: 0;
- cross-tenant publication: 0;
- wrong title or location: 0;
- captured closed-opening publication: 0;
- live/replay Exact identity mismatch: 0.

## Replay

All 30 records exported and replayed:

| Classification | Records |
| --- | ---: |
| reproduced | 20 |
| budget recovery | 7 |
| mismatch | 3 |
| fixture gap | 0 |

The seven budget recoveries preserve the same Website/hiring identity and
publish no opening. Tigermed is a separate live/replay terminal mismatch:
`CAREER_PAGE_NOT_FOUND` versus replay `NO_PUBLIC_OPENINGS`.

The other two v254 mismatches, i-Pharm Consulting and Plaid, share the exact
generic opening-search trace defect already observed for Barstool Sports and
Ichor Systems in v252. Live loses a typed company-budget error and reports
`FETCH_FAILED`; replay retains the typed failure and reports
`COMPANY_TIME_BUDGET_EXHAUSTED`. This four-company cluster is implemented and
accepted separately in `.255`.

## Non-Exact Causal Audit

The remaining records split into distinct causes:

- seven live company-budget exits reach no verified Career candidate; replay
  exhausts captured evidence and returns Career not found;
- Franklin Fitch, InterEx and Chipotle share a Job Board stage label but expose
  different recruiter/custom-site surfaces and no common provider path;
- Roku and Polaris safely reject unverified Greenhouse relationships;
- Planet Pharma and Advanced Recruiting Partners expose two different generic
  incomplete-inventory surfaces;
- Meta, ERG, Insight Global and D&B return provider-backed inventory no-match;
- Tigermed replay status drift is a singleton.

Six no-Career records appeared to prioritize generated locale paths such as
`/en-us/careers` ahead of `/careers`. A targeted first-party check rejected
this as a recovery cluster: only Hawthorne Health returned a real `/careers`
page; aizoOn returned its error surface, Yoda Tech and Tigermed returned 404,
Valence redirected to its homepage, and Ogee returned 403. No three-company
recovery claim is allowed.

## Decision

The four-company typed opening-failure cluster advances to `.255`. No other
v254 group has at least three independent companies with one trigger, one
production code path and expected recovery of at least three records.

This evidence-only cohort does not change Fresh100 aggregate scores.
