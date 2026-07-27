# v243 Next Causal Evidence - Phase C

## Scope

This report audits the code-frozen eight-record run at:

```text
/private/tmp/fresh8-v243-causal-evidence-run1
```

The run used isolated checkpoints, snapshots and replay output. It produced
one Exact, seven non-Exact records and an 8/8 offline replay. No sealed blind
cohort was inspected and no aggregate Fresh100 score is changed by this
focused evidence run.

## Per-Record Causal Result

| Record | Observed terminal | Causal result |
| --- | --- | --- |
| iClassPro / DevOps Engineer | Exact | Verified first-party Paylocity handoff, matching tenant, title and Longview location. |
| Pitch Aeronautics / DevOps Engineer | Fetch budget exhausted | No correct candidate was produced. The S4 transport budget was consumed by common paths, sitemap, bundle and blind ATS probes before S4 search could dispatch. A later independent search wave also returned zero candidates. |
| Hawaiian Electric / Cyber Operations Analyst | HTTP forbidden | The LinkedIn-official website identity is valid, but the official host denied homepage and Career requests. This proves access blocking, not absence of a Career page. |
| Hawaiian Electric / Information Assurance Analyst | HTTP forbidden | Same company and same official-host denial as the preceding record; it is one independent-company cause, not two. |
| American Fabrication / Project Manager | Job Board not found | `amfab.us/careers` is an identity-consistent Career candidate, but no first-party cross-domain handoff or adapter-verified Job Board was established. |
| NextPlay Jobs / Project Manager | Job Board not found | The selected LinkedIn company page is an organization/social link, not a Career or Job Board. NextPlay also appears to be a recruiting intermediary, so the client employer remains undisclosed. |
| Wichita Company Limited / Assistant Director HR | Fetch budget exhausted | No correct candidate was produced before the same 18-call S4 transport cap. The independent search wave also returned zero candidates. |
| Systematic Business Consulting / HR Manager | Fetch budget exhausted | No correct candidate was produced before the same 18-call S4 transport cap. The independent search wave also returned zero candidates. |

## Cluster Decision

Pitch Aeronautics, Wichita Company Limited and Systematic Business Consulting
share one deterministic implementation path:

```text
career candidate verification
-> stage_transport_dispatch_budget reaches 18
-> later S4 search requests are rejected
-> FETCH_BUDGET_EXHAUSTED
```

This is a valid failure-taxonomy cluster across three independent companies,
but it is **not yet an actionable recall cluster**. In the same code-frozen
run, the independent S5 search wave executed equivalent Career/provider
queries for all three companies and returned zero valid candidates. Reserving
three more S4 requests would therefore change the terminal label without a
reviewed nonzero recovery expectation.

The project must not claim cluster closure or implement a budget-only change
until saved evidence demonstrates that a reserved S4 search window produces a
correct candidate for a multi-company cohort.

## Other Findings

- Hawaiian Electric is one-company external blocking. A saved
  `careers.hawaiianelectric.com` surface identifies Hawaiian Electric and
  SuccessFactors, but it has not yet formed an accepted first-party
  Career/provider relationship.
- American Fabrication and NextPlay share only the final
  `JOB_BOARD_NOT_FOUND` label. Their evidence and identity causes differ, and
  the group contains only two companies.
- No wrong company, cross-tenant Job Board, wrong-location opening or unsafe
  public URL was published.

## Decision

No product-code change is justified by this eight-record run. Keep `.243`
frozen, preserve the 36 Exact / 10 Verified No Match / 1 External Blocked /
53 unresolved development projection, and select the next backend task from a
larger evidence-backed cluster with a nonzero batch-recovery expectation.
