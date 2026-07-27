# v252 Development Diagnostic Cohort - Phase C

## Frozen Live

Artifacts: `/private/tmp/v252-diagnostic-run1`

- product version: unchanged `2026-07-27.251`;
- records and independent companies: 30/30;
- prior LinkedIn job-ID overlap: 0;
- Website: 29/30;
- Career: 17/30;
- verified Job List: 11/30;
- S7 Exact: 6/30;
- elapsed: 753.6 seconds.

The run used public search-card inputs, fresh checkpoint, completion, evidence
and snapshot roots, `stage_v1`, serial execution and frozen product code.
Plugin work, authenticated External Apply, coordinator-v2, the LLM branch and
sealed holdouts remained outside the run.

## Exact Safety Audit

All six captured Exact openings were accepted:

| Company | Provider / tenant | Title and location |
| --- | --- | --- |
| Baton Corporation | Ashby / `batoncorporation` | exact title; New York overlap |
| Vicor | SuccessFactors / `custom:vicorcorpo` | exact title and Andover, MA |
| Meta | Meta Careers / `meta` | exact title; Remote US satisfies US region |
| thyssenkrupp | Workday / `thyssenkruppmaterialsna/1` | exact title; Santa Teresa overlap |
| Warner Music Group | Workday / `wmg/WMGUS` | exact title; New York overlap |
| Blockchain.com | Greenhouse / `blockchain` | exact title; New York overlap |

All six have a verified hiring relationship, provider, tenant, complete
inventory selection, specific canonical opening URL and S7 verdict. Captured
open status is valid for the live observation.

Safety totals:

- wrong or non-specific opening URL: 0;
- cross-company: 0;
- cross-tenant: 0;
- wrong location: 0;
- captured closed-opening publication: 0;
- S7 rejection among published openings: 0.

## Non-Exact Causal Audit

The remaining 24 records do not form an implementation-qualified cluster.
Their shared stage and terminal labels split across:

- current complete-inventory no-match;
- first-party or provider transport blocks;
- company-deadline exits without a proven recoverable candidate;
- custom job surfaces with different parser and transport paths;
- provider-specific incomplete inventories;
- isolated identity or publication gaps.

This cohort adds no third recovery company to the existing hypotheses:

- Workable numeric embed remains American Battery Technology Company and
  ClassWallet, 2/3;
- strict structured job cards gain only the treeNovum singleton whose captured
  Career HTML visibly contains the target title;
- same-origin dynamic GET remains below the three-company path gate.

No product implementation is authorized from a stage label or from any of
these singleton/two-company paths.

## Replay

The full bundle exported and replayed all 30 records:

| Classification | Records |
| --- | ---: |
| reproduced | 20 |
| budget recovery | 8 |
| mismatch | 2 |
| fixture gap | 0 |
| tape divergence | 0 |
| missing snapshot boundary | 0 |

The two mismatches are Barstool Sports and Ichor Systems. In both cases live
and replay preserve the same company, Career and generic Job List identity and
publish no opening. They differ only in opening-portfolio reason aggregation:
live reports `FETCH_FAILED`, while replay reports the already observed
`COMPANY_TIME_BUDGET_EXHAUSTED`. This is one shared replay path but currently
only two independent companies, so it is retained as replay debt rather than
implemented under the three-company rule.

Record integrity passed 30/30. The automatic command exits nonzero because the
outcome gate correctly rejects those two mismatches, not because replay input
or snapshots are missing.

## Decision

`.252` is evidence collection on unchanged `.251`; there is no product version
bump and no Fresh100 aggregate change. Continue backend-only evidence
collection until a third independent company establishes a common trigger,
production path and expected batch recovery. Do not weaken identity, provider,
tenant, title, location, URL or S7 gates.
