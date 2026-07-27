# v261 Career Transport Reservation - Phase A

## Cluster

Four independent companies share one reproducible scheduler defect:

| Company | Blind ATS dispatches before official exhaustion | Live terminal |
| --- | ---: | --- |
| The Naked Market | 11-14 | `FETCH_BUDGET_EXHAUSTED` |
| Motorola Solutions | 11-14 | `FETCH_BUDGET_EXHAUSTED` |
| Daedalus | 11-14 | `FETCH_BUDGET_EXHAUSTED` |
| DataAnnotation | 11-14 | `FETCH_BUDGET_EXHAUSTED` |

Every record has a verified Website. Lower-evidence blind ATS probes consume a
large share of the 24-dispatch Career budget before bounded homepage
navigation and official path candidates can run. Same-version replay
reproduces all four terminals.

## Implementation Boundary

The repair may change only generic Career discovery scheduling/budget policy,
focused tests and adapter version:

- divide one total dispatch budget into evidence-aware phases;
- preserve a bounded reserve for verified Website navigation and official
  homepage/path candidates;
- cap blind ATS probes before the reserve is released;
- allow unused official reserve to become available later;
- keep total transport dispatches at or below the configured limit;
- preserve host denial circuits, typed fetch taxonomy and replay recording;
- do not add company, domain, provider, title or job-ID branches;
- do not change candidate verification, hiring relationship, tenant or S7.

The change must not globally increase network concurrency or the total request
budget.

## Acceptance

1. Contract tests cover blind ATS starvation, reserve release, exact total
   limit and deterministic ordering.
2. The four focused records no longer terminate because blind ATS consumed the
   dispatch budget before official candidates.
3. Every new terminal is evidence-backed and same-version replayable; Exact is
   counted only if S7 passes.
4. Existing official-host denial and speculative-path controls do not gain
   extra requests or false Careers.
5. Relevant Career/pipeline/replay tests, provider benchmark, resolver
   benchmark, architecture gate and scoped `git diff --check` pass.
