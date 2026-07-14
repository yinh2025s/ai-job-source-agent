# ADR-0011: Bound Career Discovery Transport Calls

Status: Accepted

Date: 2026-07-14

## Context

S4 career discovery can inspect a homepage, navigation bundles, sitemaps, search results, and
the candidates each source produces. The per-company deadline is necessary but is a volatile,
coarse outer limit: it does not bound the number of underlying transport attempts, and retries
make a logical fetch an unreliable accounting unit. A frozen, explicit S4 transport-call budget
is required to make this work predictable without changing the evidence and validation gates.

The existing persisted configuration schema is 1.0. Its payload and digest are already part of
replay and checkpoint identity, so adding a defaulted field to that schema would silently change
legacy identity.

## Decision

`AgentConfig.max_career_discovery_transport_calls` is an `int | None` in configuration schema
1.1.

- The CLI and live-runner default is `32`; library construction remains unbounded by default with
  `None`.
- `None` means no S4 transport-call limit. A finite value is a non-negative limit on actual
  delegated S4 transport attempts, scoped to one `find_career_page` invocation.
- Schema 1.0 remains supported and unbounded. It preserves its exact serialized payload and
  digest; it is not normalized into a synthetic 1.1 payload.
- Schema 1.1 serializes the explicit policy as part of configuration identity so budgeted runs
  cannot replay or resume as unbounded runs.

The composition root places the S4 counter below `PageCache`, `Snapshot`, and
`RetryingFetcher`, at the delegate-dispatch boundary:

```text
PageCache -> Snapshot -> RetryingFetcher -> S4 transport counter -> transport delegate
```

Consequently, a cache hit consumes zero calls, each retry attempt consumes one call, and a
request rejected before delegate dispatch consumes zero calls. The counter does not rewrite
request identity, snapshot behavior, retry policy, page validation, or candidate scheduling.

When a finite limit has no remaining calls, the counter rejects the next dispatch with typed
`FETCH_BUDGET_EXHAUSTED`. S4 retains the normal error taxonomy and evidence rules; a budget
rejection must not become a claim that a career page or public jobs do not exist.

Trace records only privacy-safe accounting fields:

- `policy`
- `limit`
- `dispatched`
- `remaining`
- `exhausted`
- `rejected`
- `by_phase`
- `cache_hits`

The allowed phases are `homepage`, `bundle_navigation`, `sitemap_discovery`,
`search_discovery`, and `{schedule_source}_candidates`, where the final name identifies the
already-selected scheduling source rather than URLs, page content, credentials, or request
headers. Trace does not persist volatile timing, page bodies, cookies, tokens, or login state.

## Consequences

- A finite S4 run has a deterministic upper bound on dispatched transport attempts, including
  retries, while cache reuse remains free.
- CLI and live users receive a bounded default; library callers retain prior unbounded behavior
  unless they opt in.
- Checkpoint and replay compatibility remain conservative: schema 1.0 preserves historical
  identity, and schema 1.1 distinguishes the new policy.
- The S4 counter is owned by career-discovery composition and trace aggregation. Page cache,
  snapshot, retry, provider, and scheduler modules retain their existing responsibilities.
- This ADR does not define a cross-process handoff of S2 LinkedIn website evidence into S4.
  Any durable S2-to-S4 homepage-evidence reuse requires a separate contract; the current
  process-local cache and existing stage evidence rules remain unchanged.

