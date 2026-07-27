# v250 Development Diagnostic Cohort - Phase C

## Frozen Live

Artifacts: `/private/tmp/v250-diagnostic-run1`

- adapter version: `2026-07-27.246`;
- records: 30;
- independent companies: 26;
- Website: 30/30;
- Career: 23/30;
- verified Job List: 17/30;
- S7 Exact: 8/30.

The input has zero LinkedIn job-ID overlap with Fresh100 and v245-v249. The run
used public search-card inputs, isolated state roots and unchanged product code.

The terminal distribution was:

| Terminal outcome | Records |
| --- | ---: |
| Exact opening | 8 |
| Verified no match | 3 |
| Discovery unresolved | 8 |
| Retryable failure | 7 |
| External blocked | 2 |
| Other non-success | 2 |

## Exact Safety Audit

The eight Exact records represent seven independent companies and seven unique
opening URLs. Both Instagram records correctly resolve to the same multi-location
Meta opening.

| Company | Provider / tenant | Opening identity |
| --- | --- | --- |
| Lilly Pulitzer | Workday / `oxford/LillyPulitzer` | exact title; King of Prussia overlaps the first-party opening location |
| Bumble Inc. | Lever / `bumbleinc` | exact title; New York overlaps the opening location |
| Netflix | Eightfold / `netflix.com` | exact title and Los Angeles location |
| PCCA | UltiPro / `PRO1044PCCA/eb0e667e-d9a7-4107-9d03-1a3c9e8fd34e` | exact title and Houston location |
| Paramount | SuccessFactors / `custom:viacomcbsi` | exact title and New York location |
| Instagram, New York | Meta Careers / `meta` | exact title; New York is in the multi-location opening |
| Notion | Ashby / `notion` | exact title; New York is in the opening locations |
| Instagram, San Francisco | Meta Careers / `meta` | exact title; San Francisco is in the same multi-location opening |

All eight identity assertions have verdict `verified`, no failure codes, a
verified hiring relationship, a canonical provider and tenant, a specific
opening URL, and an exact or overlapping location classification. The
contemporaneous URL audit found all seven unique canonical opening URLs
reachable.

Aggregate safety:

- correct company or verified hiring entity, title, location, provider, tenant
  and URL: 8/8;
- wrong or non-specific opening URL: 0;
- cross-company or cross-tenant publication: 0;
- wrong location or closed-opening publication: 0;
- Exact live/replay opening mismatch: 0.

## Non-Exact Root Causes

The 22 non-Exact records represent 19 independent companies. Their reason-code
distribution is:

| Reason code | Records | Independent companies |
| --- | ---: | ---: |
| `JOB_BOARD_NOT_FOUND` | 6 | 6 |
| `OPENING_NOT_FOUND` | 4 | 4 |
| `COMPANY_TIME_BUDGET_EXHAUSTED` | 4 | 3 |
| `FETCH_FAILED` | 2 | 2 |
| `HTTP_NOT_FOUND` | 2 | 1 |
| `HTTP_FORBIDDEN` | 2 | 2 |
| `FETCH_BUDGET_EXHAUSTED` | 1 | 1 |
| `OPENING_DISCOVERY_INCOMPLETE` | 1 | 1 |

### Correct negative controls

Los Angeles Lakers, hackajob and Meta have verified inventories without the
target opening. They account for three verified-no-match outcomes and must not
be recovered by weakening title or location checks. Bumble's second Graphic
Designer record also ends in `OPENING_NOT_FOUND`, but its trace does not prove a
complete no-match inventory and therefore remains discovery unresolved.

### Job-board discovery paths

The six `JOB_BOARD_NOT_FOUND` records share a stage label, not a causal defect:

- San Diego Padres reaches an explicit first-party Hireology handoff at
  `careers.hireology.com/sandiegopadres`; Hireology is not yet a verified
  provider path in this run.
- Shopbop reaches an Amazon team page rather than a canonical inventory.
- Roku reaches the custom `weareroku.com` career surface.
- TikTok reaches the custom `lifeattiktok.com` career surface.
- Carrot remains on its first-party custom Career page.
- MedReview remains on its first-party custom Career page.

Only San Diego Padres exhibits the Hireology trigger. The other five require
different provider, dynamic-inventory or relationship evidence paths.

### Transport and budget paths

- Ancient Nutrition and Snappt both report `FETCH_FAILED`, but use different
  inventory transports: ADP Workforce Now and `applytojob.com`.
- Plant Professionals, Abacus Solutions Group and two SKIMS records report
  company-budget exhaustion. Their replay behavior is not uniform: the first
  two normalize to `CAREER_PAGE_NOT_FOUND`, while the two SKIMS records expose
  shared-tape divergence.
- Forbes alone exhausts the bounded career-discovery fetch budget.
- Dior and J.Crew are two independent first-party HTTP-forbidden surfaces, still
  below the three-company threshold.

These labels do not establish one shared trigger or one batch-recoverable code
path.

### Singleton downstream defects

- The two Instacart records are one independent company and resolve the
  Greenhouse API path segment `v1` as a tenant before receiving
  `HTTP_NOT_FOUND`.
- Spotify alone reports incomplete discovery on its custom inventory.

Neither singleton authorizes a product change.

## Candidate-Cluster Check

v250 supplies no third independent recovery company for the three previously
tracked hypotheses:

1. Workable numeric embed remains American Battery plus ClassWallet, 2/3.
2. Strict structured job cards remain Opstergo plus Funhouse, 2/3.
3. Same-origin dynamic GET remains Confidential plus sweetgreen, 2/3.

The newly observed Hireology handoff is also only one independent company in
this cohort. No candidate has all three required properties:

- the same observable trigger across at least three independent companies;
- the same production code path;
- an expected batch recovery of at least three companies.

Stage labels and terminal reason codes are therefore not treated as
implementation clusters.

## Replay Integrity

The initial 30-record replay was not accepted as complete. It aborted with one
unconsumed outcome-tape entry, whose first remaining request was
`GET https://skims.com/`. The two SKIMS records share one company but enter with
different website roots (`https://skims.com/` and `https://skims.com/en-sg`).
They are recorded as two tape divergences, not as reproduced outcomes.

The other 28 records were replayed in isolated groups:

| Replay classification | Records | Evidence |
| --- | ---: | --- |
| Strictly reproduced | 24 | 21 in success/partial group, 2 HTTP-forbidden, 1 fetch-budget |
| Budget-normalized | 2 | Plant Professionals and Abacus: company budget to Career page not found |
| Mismatch | 2 | Ancient Nutrition and Snappt: fetch failed to company budget |
| Tape divergence | 2 | both SKIMS records |
| Fixture gap | 0 | all completed isolated groups |

The two mismatches preserve company identity and the absence of a published
opening, but change terminal reason from `FETCH_FAILED` live to
`COMPANY_TIME_BUDGET_EXHAUSTED` in replay. They also traverse different
providers, so they are not one evidenced replay implementation cluster.

The two budget-normalized outcomes are consistent with wall-clock budget exits
collapsing to deterministic exhausted-search results under tape replay. They are
reported separately and are not counted as strict reproductions.

## Decision

No Phase B product-code change is authorized from v250. Exact precision remains
100%, but this cohort does not establish a common trigger, common code path and
expected recovery across at least three independent companies. The observed
singleton and two-company hypotheses remain diagnostic leads only.

Continue backend-only evidence collection on unchanged `.246`. Do not weaken
company, hiring-relationship, provider, tenant, title, location or S7 checks.
The extension, coordinator-v2, LLM branch and sealed holdouts remain frozen.
