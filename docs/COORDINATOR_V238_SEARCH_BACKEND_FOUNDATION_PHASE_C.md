# v238 SearchBackend Foundation - Phase C

## Result

The ordinary non-LLM backend now has a versioned, injectable search boundary.
The browser extension remains frozen, coordinator-v2 remains proposed and
disabled, and no LLM implementation was merged.

`SearchBackend` is deliberately narrow: one call can dispatch at most one
request through the existing fetch stack and returns untrusted hits. It cannot
retry, rank trusted identity, establish a hiring relationship, or publish an
opening. The existing Bing RSS, Bing HTML, and DuckDuckGo schedule remains
unchanged when no backend is configured.

The optional SearXNG adapter:

- consumes the JSON search API;
- allows HTTPS or HTTP loopback endpoints;
- rejects credentials, query strings, fragments, and unsafe HTTP hosts;
- maps only `url`, `title`, and `content`;
- treats malformed payloads as `invalid_response`;
- emits hashed trace provenance rather than the raw endpoint or query;
- fingerprints endpoint and fixed search behavior without including secrets.

S4/S5 Career and provider search share one injected backend, and S6 same-site
opening search uses the same instance. Every hit still passes the existing URL,
provider, tenant, hiring-relationship, inventory, title, location, and S7
identity gates.

## Determinism And Replay

Run-configuration schema `1.8` adds:

```text
search_backend_kind
search_backend_contract_version
search_backend_profile_digest
```

Schema `1.7` and older payloads retain their exact serialized form and default
to `legacy`. Adapter version is `2026-07-27.238`.

A SearXNG replay requires the runtime endpoint again. Replay reconstructs its
public profile and rejects any digest mismatch. The raw endpoint is not written
to result, trace, summary, checkpoint, or run-configuration payloads.

## Verification

The implementation passed:

- 286 scoped unit and integration tests covering SearchBackend, SearXNG,
  Career Search, provider search, composition, CLI, S6 opening search,
  run configuration, pipeline/checkpoint compatibility, live-runner
  configuration, and replay profile validation;
- production provider benchmark 25/25;
- resolver benchmark 6/6;
- architecture validation with 46 native adapters and 0 issues;
- Python compilation checks and `git diff --check`.

## Cohort Status

No live recall gain is claimed in this phase because no real SearXNG endpoint
was configured. The 12 `search_results_filtered_to_zero` records across 11
companies remain unresolved and the Fresh100 development projection remains:

```text
36 Exact
10 Verified No Match
1 External Blocked
53 unresolved
```

The failure cluster may only close after a frozen focused live run demonstrates
batch recovery with zero wrong URLs, wrong locations, cross-company matches, or
cross-tenant matches. A one- or two-record recovery is insufficient evidence
that the cluster was correctly defined.
