# v257 Development Diagnostic Cohort - Phase C

## Frozen Live

Artifacts: `/private/tmp/v257-diagnostic-run1`

- input SHA-256:
  `6ce4f479be3d91dc93b52d216998be2627a48c6bf7246a059b87f66180524e36`;
- product version: `2026-07-27.255`;
- records and independent companies: 30/30;
- prior known public development IDs excluded: 529;
- Website: 27/30;
- Career: 20/30;
- verified Job List: 15/30;
- raw S7 Exact: 10/30;
- audited safe Exact: 9/30;
- elapsed: 780.0 seconds.

The run used public search-card inputs, isolated roots, `stage_v1`, serial
execution and frozen product code. Plugin work, authenticated External Apply,
coordinator-v2, the LLM branch and sealed holdouts remained frozen.

## Exact Safety Audit

Nine published openings pass company, hiring entity, provider, tenant, title,
location, current-state and canonical-URL review:

- Snap Inc.;
- Arca;
- Rain;
- ReachMobi;
- Podium;
- Manna Air Delivery;
- Alma;
- Janicki;
- ADDMAN.

CRG is an unsafe raw Exact. The selected page is a CRG staffing listing that
states that an undisclosed client is hiring. Title and Knoxville location
match, but the page does not establish CRG as the hiring employer. It must be
suppressed or classified `recruiter_client_undisclosed`; raw Exact precision is
therefore 9/10, not 10/10.

Harvey produces two exact-title Ashby candidates. Both are safely suppressed
because tenant `harvey` came only from an unverified probe and lacks an
independent hiring relationship. No wrong tenant, wrong title, wrong city or
captured closed-opening publication was found among the other results.

## Replay

All 30 records were selected, exported, replayed, traced and compared:

| Classification | Records |
| --- | ---: |
| reproduced | 25 |
| budget recovery | 5 |
| mismatch | 0 |
| fixture gap | 0 |

The outcome gate passes. STARK BANK, Chipply, Storyteller Overland, Eaton and
3M normalize from live company-budget exhaustion to replay Career not found;
none recovers an opening.

The Exact audit found location-evidence mutation below the outcome comparator:

- ReachMobi changes `Bonita Springs, Florida` to
  `Bonita Springs, offline-replay-redacted-credential`;
- ADDMAN gains `Statesville, [REDACTED], USA`;
- Janicki benignly normalizes `Layton, Utah` to `Layton, UT`.

ReachMobi and ADDMAN remain the same URL/company/provider/tenant and retain a
compatible location, but byte-level replay evidence continuity is not clean.
These are snapshot-redaction defects and are not folded into provider or
inventory work.

## Causal Audit

No within-cohort non-Exact cluster qualifies:

- five Career-budget records replay as Career not found with 0/5 Exact
  recovery;
- four first-party 403 records have no captured alternate Exact route;
- Backyard Products, Varo, Cricut and Timken share only a Job Board stage
  label and have different underlying paths;
- Stripe, Ott and Crescent reach different opening protocols, and the previous
  v256 broad budget diagnostic already recovered 0/4 Exact;
- the remaining resolver, identity, inventory and provider failures are
  singletons.

One cross-cohort provider-family hypothesis advances separately. Gordian is a
third independent Eightfold PCS X company after HP and Mayo Clinic. All three
publish `pcsx-data` plus `ef-*` assets, omit legacy `smartApplyData`, and are
rejected by the same Eightfold adapter path. The shared PCS X API contract is
handled in `.258`; it is not counted as a v257 Exact recovery.

## Decision

v257 is evidence-only and does not change Fresh100 projection. The unsafe CRG
publication remains an open highest-priority safety defect until at least
three independent staffing/intermediary pages establish one common trigger and
production path. ReachMobi/ADDMAN redaction mutation also remains open pending
a generic replay cluster.
