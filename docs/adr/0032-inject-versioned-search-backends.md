# ADR-0032: Inject Versioned Search Backends

Status: accepted

Date: 2026-07-27

## Context

Provider-targeted and same-site opening discovery depended directly on
anonymous Bing RSS, Bing HTML, and DuckDuckGo HTML endpoints. Live evidence
showed ignored query constraints, empty parsable HTML, and search challenges.
Endpoint construction, parsing, challenge detection, fallback, filtering, and
ranking were coupled inside `career_search.py`.

Replacing all three legacy requests with one opaque component would change
transport budgets, circuit breakers, snapshots, and replay boundaries.
Persisting an alternative backend endpoint in deterministic artifacts would
also expose private deployment information.

## Decision

`SearchBackend` is an optional injected contract. One `search()` call may
dispatch at most one request through the existing `Fetcher`; it cannot retry,
validate candidates, establish a hiring relationship, or publish an Exact
opening.

With no backend, the legacy Bing RSS, Bing HTML, and DuckDuckGo schedule remains
unchanged. The first alternative implementation is SearXNG JSON. It accepts
HTTPS endpoints or HTTP loopback endpoints, rejects credentials/query/fragment,
and returns privacy-safe hashed provenance instead of the raw endpoint.

S4/S5 candidate discovery and S6 same-site opening search share the same
backend instance. All hits still pass existing URL cleaning, provider adapter,
tenant, hiring-relationship, title, location, inventory, and S7 identity gates.

Backend selection is part of deterministic run configuration schema `1.8`:

```text
search_backend_kind
search_backend_contract_version
search_backend_profile_digest
```

The raw endpoint and credentials are runtime-only. Replay of a SearXNG run
requires the endpoint again and rejects it unless its profile digest matches the
recorded configuration. Production/focused runs also supply a public SHA-256 of
the SearXNG image/settings server profile so changing the metasearch runtime
invalidates incompatible checkpoints without storing the server configuration.

## Consequences

- Default behavior and legacy replay remain available without configuration.
- A backend/profile change invalidates incompatible checkpoints and completion
  records.
- Search source quality can be evaluated independently of candidate and
  identity verification.
- A configured SearXNG service is still required before any live recall
  improvement can be claimed.
- This decision does not enable coordinator-v2, browser extension work, paid
  search APIs, or the isolated LLM branch.
