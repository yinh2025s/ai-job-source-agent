# Backend Release Completion Audit After `.279`

Date: 2026-07-28
Audited release commit: `779fe84204566430fa3408e04007996aaf847b9c`
Product adapter: `2026-07-28.279`
Decision: **release clean; no current failure cluster authorizes Phase B**

## Scope

This audit follows the code-frozen Fresh100 `.278` cold measurement and the
`.279` replay-budget repair. Four independent read-only workstreams inspected
the three remaining company-count causal labels, Frozen100 replay assets and
the raw-capsule privacy residual. No network request, sealed cohort, plugin
operation, coordinator-v2 migration or LLM integration was used.

## Authoritative Current Evidence

Fresh100 `.278` completed 100/100 records from new state:

- 90 Websites;
- 76 Career pages;
- 69 verified Job Lists;
- 31 authoritative S7 Exact openings;
- 21 evidence-backed Verified No Match terminals;
- 2 evidence-backed External Blocked terminals;
- 46 unresolved records;
- 31/31 Exact records passed company, title, location, provider, tenant and
  opening-URL review;
- zero wrong URL, wrong location, cross-company or cross-tenant publication.

The initial strict replay was 93 reproduced, two budget recoveries, five
mismatches and zero fixture gaps. `.279` restored the recorded Career
transport-budget semantics for Caesars Entertainment, ProMach and Systematic
Business Consulting. The same immutable capsule now replays as 96 reproduced,
two budget recoveries, two mismatches and zero fixture gaps.

## Executable Causal Reclassification

The current ledger's largest labels do not represent shared repairable causes.

| Ledger label | Companies | Shared repair evidence | Decision |
| --- | ---: | ---: | --- |
| `search_results_filtered_to_zero` | 17 | 0 | Reject |
| `transport_dispatch_budget_exhausted` | 6 | 0 | Reject |
| `eligible_board_portfolio_incomplete` | 3 | 0 | Reject |

### Search Results Filtered To Zero

The 17 records produced 1,042 raw search results. None was an ATS URL or a URL
on the already resolved company domain. Across 77 `site:` queries, none returned
a result on the requested site. Twelve records used
`CareerSurfaceCandidateDiscovery`; five used
`ProviderSearchCandidateDiscovery`.

The current causal projector merges them because it sees non-empty raw results
and an empty candidate list. The filter did not remove a known-correct
candidate. Relaxing it therefore has zero evidenced recoveries and would admit
unrelated companies, tenants and URLs. A new search backend can be reconsidered
only after it produces correct candidates for at least three independent
companies and those candidates pass the existing identity and S7 gates.

### Transport Dispatch Budget Exhausted

The six records share only the fact that the counter reached zero:

| Record | Actual cause |
| --- | --- |
| Caesars Entertainment | one rejected final dispatch after an Imperva challenge |
| Nisga'a Tek | captured official page contains an iCIMS handoff that parsing missed |
| Splashlight | captured official Career inventory lacks the target DevOps role |
| FOTOMILL | Website and Career paths fail TLS |
| ProMach | no dispatch rejection; all 32 calls were consumed without a correct candidate |
| Systematic Business Consulting | no dispatch rejection; site, sitemap and search produced no Career candidate |

Increasing or reordering the budget has zero evidenced recoveries. Future
diagnostics should distinguish `limit_reached`, `global_dispatch_rejected` and
`speculative_reservation_rejected`; that reporting improvement does not itself
recover a product terminal.

### Eligible Board Portfolio Incomplete

The three records have different roots:

- WalkMe has a correct first-party detail page, but visible title/location
  evidence does not satisfy the current structured `JobPosting` identity
  contract.
- Heritage Companies has the correct Paylocity opening, but `HR` is not
  normalized to `Human Resources`.
- Crosby's captured official inventory contains no matching role; an Ashby
  tenant with the same name belongs to another company and is correctly
  rejected.

WalkMe and Heritage each support one possible Exact recovery through different
code paths. Crosby may support a Verified No Match only after inventory
completeness is proven. No single change can recover all three safely.

## Replay And Privacy Debt

Versana and Brown and Caldwell remain separate singleton replay mismatches.
Diamondback Energy and State of Montana remain budget recoveries. They do not
authorize a new implementation under the three-company/three-recovery rule.

The historical Frozen100 archive is complete and preserves its `.228` evidence:
69 Exact and a historical 100/100 replay. A current `.279` migration replay was
attempted from an isolated extraction. Current privacy validation rejected the
historical snapshot set at line 34 because a snapshot body is not fully
sanitized. Therefore the historical replay cannot be treated as current-version
no-regression evidence, and weakening the validator is not allowed.

The `.278` raw capsule remains non-shareable. One company, Crawford Thomas
Recruiting, carries one Google browser-key-shaped value through the raw-page
link extraction path into trace, checkpoint and completion output (ten
serialized occurrences). Results, authoritative snapshot index and `.279`
replay output do not contain the value. This is one company and a different
path from the already-closed snapshot-body cluster, so it does not authorize a
new implementation.

## Completion Matrix

| Requirement | Current status |
| --- | --- |
| Fresh100 cold live 100/100 | Proven at `.278` |
| Fresh100 Exact publication safety | Proven for 31/31 Exact |
| Fresh100 evidence-backed terminal 100/100 | Not met: 46 unresolved |
| Fresh100 strict replay 100/100 | Not met: 96 reproduced, 2 recoveries, 2 mismatches |
| Shareable privacy-clean Fresh100 capsule | Not met |
| Frozen100 historical 69 Exact | Preserved at historical version |
| Frozen100 current-version no-regression | Not proven |
| Two unseen cohorts at acceptance threshold | Not run; sealed v2/v3 remain unopened |
| LLM isolation | Preserved |

## Decision And Next Gate

No current Fresh100 label satisfies one observable trigger, one production
path, at least three independent companies and at least three evidenced
terminal recoveries. Phase B is therefore rejected. Do not add a heuristic,
raise transport budgets, relax URL filtering, weaken identity validation or
implement singleton fixes from this cohort.

The next useful evidence must come from one of these serial gates:

1. a non-sealed diagnostic cohort designed to collect at least three examples
   of one currently singleton root cause;
2. a current-version Frozen100 cold live plus strict replay using entirely new
   roots;
3. after development gates are accepted, the two sealed holdouts under
   `docs/BLIND_HOLDOUT_PROTOCOL.md`.

The full product goal remains open. This audit makes no product behavior change
and does not rewrite the immutable `.278` measurement.

