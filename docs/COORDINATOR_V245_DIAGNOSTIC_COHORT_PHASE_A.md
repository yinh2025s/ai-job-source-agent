# v245 Non-Sealed Diagnostic Cohort - Phase A

## Purpose

The reconciled `.244` Fresh100 development ledger has 50 unresolved records but
zero implementation-qualified causal clusters. Four deterministic singleton
recoveries are known, while the only three-company budget cluster has an
evidenced recovery expectation of 0/3.

The next safe step is evidence collection, not a product-code change.

## Cohort Contract

1. Discover public LinkedIn search cards through the existing anonymous backend.
2. Use four role families to avoid another one-title/provider sample:
   Software Engineer, Registered Nurse, Account Executive and Mechanical
   Engineer.
3. Exclude every LinkedIn job ID already present in the Fresh100 development
   ledger.
4. Freeze 24 records, balanced across the role families when public results
   allow.
5. Label the cohort `development_diagnostic`; it is not a blind holdout and
   cannot be used for final product acceptance.
6. Do not enrich authenticated External Apply, use the extension, inspect
   sealed blind v2/v3 or reuse Fresh100 caches.

## Run Contract

- Adapter version: `.244` until the run finishes.
- Candidate discovery engine: `stage_v1`.
- Search backend: `legacy`.
- Fresh checkpoint, completion, evidence, snapshot, manifest and replay roots.
- Bounded serial/low-concurrency network use.
- Code frozen for the complete live run and replay.
- Every result must retain typed terminal, trace and replay boundary.
- No code modification during the benchmark.

## Acceptance

1. Freeze exactly 24 zero-overlap records or report the discovery shortfall.
2. Complete the live run without mixing artifacts into Fresh100.
3. Replay all captured records with zero mismatch, fixture gap and tape
   divergence, or classify each integrity failure separately.
4. Audit Exact URLs for company, title, location, provider and tenant.
5. Reclassify non-Exact records by causal trigger and production code path.
6. Enter Phase B only if a cluster has at least three independent companies,
   observed correct-candidate evidence and expected recovery of at least three.

## Out Of Scope

This phase does not change product code, budgets, retries, provider adapters,
identity gates, coordinator-v2, plugin behavior, LLM behavior or sealed
holdout state.
