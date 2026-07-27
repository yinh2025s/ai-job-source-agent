# v256 Development Diagnostic Cohort - Phase C

## Frozen Live

Artifacts: `/private/tmp/v256-diagnostic-run1`

- input SHA-256:
  `009bd616f23a7bbfc04e64d98aef1b25695aa6e14e3eff80b9a08d3ff61e8533`;
- product version: `2026-07-27.255`;
- records and independent companies: 30/30;
- excluded prior development job IDs: 435;
- prior overlap: 0;
- Website: 29/30;
- Career: 22/30;
- verified Job List: 13/30;
- S7 Exact: 5/30;
- elapsed: 815.1 seconds.

The run used public search-card inputs, clean isolated roots, `stage_v1`,
serial execution and frozen product code. Plugin work, authenticated External
Apply, coordinator-v2, the LLM branch and sealed holdouts remained frozen.

## Exact Safety Audit

All five published openings pass company, hiring entity, provider, tenant,
title, location, current-state and canonical-URL review:

| Company | Provider / tenant | Opening |
| --- | --- | --- |
| Discord | Greenhouse / `discord` | Data Scientist, Analytics; San Francisco Bay Area |
| Hadrian | Ashby / `hadrian-automation` | Machine Learning Engineer - LLMs; Los Angeles |
| INNERGY | SmartRecruiters / `INNERGY1` | Customer Success Manager; Austin |
| Tembo Health | Ashby / `Tembo-health` | Nurse Practitioner, Full Time; Remote US |
| Murphy Company | Paycor / `recruitingbypaycor.com\|8a7883c6800617450180478389011c13` | Construction Project Manager; St. Louis |

Safety totals are zero for wrong URL, wrong company or brand, cross-tenant,
wrong title, wrong location and captured closed-opening publication.

Williams produced a sixth exact-title Workday candidate under the correct
`williams/PowerExternal` tenant. Its list response exposed only `2 Locations`,
so S7 rejected it against `New Albany, OH` and cleared the opening URL. The URL
path and frozen response contain New Albany evidence, making this a
conservative singleton false negative, not an unsafe publication.

## Replay

All 30 records were selected, exported, replayed, traced and compared:

| Classification | Records |
| --- | ---: |
| reproduced | 25 |
| budget recovery | 4 |
| mismatch | 1 |
| fixture gap | 0 |

The 180 scoped tapes and 488 snapshot blobs pass digest and presence checks.
The four budget recoveries are SoTalent, Chelsea Cannabis, Guernsey and
Passport Health. Replay exhausts their captured evidence and changes the
reason from company budget to Career not found, but none recovers an opening.

Great Value Hiring is the sole mismatch: live is `failed` and replay is
`partial` while both retain the failed Career stage. Replay additionally
classifies downstream absence as `NO_PUBLIC_OPENINGS`. Bundle construction and
record integrity pass, but `outcome_gate.status=failed`; this run is therefore
not a replay closure.

The configured company-discovery evidence source is missing from the exported
bundle, so zero frozen provider inputs are restored. This is a provenance
limitation, not a fixture or snapshot-integrity gap.

## Rejected Candidate Clusters

Three count-qualified signatures fail the required expected-recovery gate:

1. SoTalent, Chelsea Cannabis, Guernsey and Passport Health reach the Career
   search caller deadline before the first query. Replay normalizes all four
   to Career not found and recovers 0/4 Exact.
2. Starbucks, C&S Wholesale Grocers, CRB and Peachtree Immediate Care enter
   the same generic opening-search budget path after a verified Job List.
   A same-version, isolated 90-second diagnostic at
   `/private/tmp/v256-opening-budget-run2` changes all four to
   `OPENING_DISCOVERY_INCOMPLETE` but recovers 0/4 Exact. The true causes are
   missing inventory/search protocols, not the deadline itself.
3. TikTok, CoreLife Healthcare, UCI Police, Hermès and Harry Winston execute
   six provider-search queries, receive 50 search results, produce zero valid
   provider candidates and reject every tenant probe. Captured results contain
   search noise rather than three filtered valid ATS candidates, so no common
   recovery is supported.

The remaining non-Exact records are distinct singletons or valid evidence
terminals: LinkedIn transport timeout; LHH search deadline; Core Home HTTP 999;
Williams coarse Workday location; Crossing Hurdles no public recruiting
surface; Mercor and Handshake full-inventory title no-match; Great Value Hiring
clock-dependent replay state; SundaySky undeclared generic inventory; Bubble
provider-search deadline; Canva unsafe next URL; and Tesla first-party 403.

## Decision

No implementation cluster qualifies for Phase B. No product code or adapter
version changes in this phase, and the Fresh100 projection remains unchanged.
The next backend iteration must collect another zero-overlap development
cohort and may implement only a newly demonstrated three-company common
trigger, code path and expected recovery of at least three Exact records.
