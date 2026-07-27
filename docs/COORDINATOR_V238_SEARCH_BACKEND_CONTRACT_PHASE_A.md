# v238 SearchBackend Contract - Phase A

## Scope

This phase addresses the search-source coupling observed in the current
development cohort. The causal ledger contains 12 records across 11 companies
with:

```text
category: correct_candidate_not_produced
trigger: search_results_filtered_to_zero
code_path: job_board_discovery.provider_search_discovery
```

The affected companies are American Fabrication, Caesars Entertainment, CHAMP,
Fabric, Hawaiian Electric, iClassPro, NextPlay Jobs, Pitch Aeronautics,
Prophetic, Systematic Business Consulting, and WICHITA COMPANY LIMITED.

This is not yet a qualified recovery cluster. The current-version
reproductions show that Bing RSS can drift away from quoted and `site:` query
constraints, Bing HTML can return no parseable results, and DuckDuckGo can
return a challenge. Direct provider probes did not recover a valid candidate.
The implementation therefore creates a replaceable search boundary; it does
not claim that these 12 records are fixed.

## Root Cause

`career_search.py` currently owns all of the following:

- search endpoint construction;
- source ordering and fallback roles;
- response parsing;
- challenge detection;
- source-specific rescue decisions.

The resolver also selects sources by literal names such as `bing_rss` and
`duckduckgo_html`. This makes the search route dependent on two anonymous
public endpoints and prevents a reproducible alternative backend from being
introduced without editing the resolver control flow.

## Contract

The new optional `SearchBackend` boundary owns one request construction,
response parsing, response disposition, and privacy-safe configuration
identity. Each `search()` call may dispatch at most one
`fetcher.fetch()` call. It must not implement retry, fallback, candidate
validation, or its own transport.

The resolver continues to own budgets, circuit breakers, candidate filtering,
ranking, provider validation, hiring-relationship validation, and S7 identity
gates. A search backend can only produce candidates; it cannot declare an
Exact result.

When no backend is configured, the existing resolver behavior remains
unchanged:

```text
bing_rss (primary)
bing_html (same_query_fallback)
duckduckgo_html (secondary)
```

This legacy path is deliberately not wrapped into one backend call because its
three separately budgeted requests, source circuit breakers, snapshots, and
replay boundaries are already observable behavior.

An optional SearXNG backend replaces the legacy source plan for each query. It
must:

- use JSON output;
- accept HTTPS endpoints, or HTTP only for loopback development;
- reject endpoint credentials, query strings, and fragments;
- tolerate malformed responses by returning no results;
- expose only a backend identifier and endpoint SHA-256 digest to deterministic
  run configuration and trace data.

No paid search service, API key, plugin code, coordinator-v2 behavior, or LLM
branch is part of this phase.

## Determinism And Privacy

Selecting a backend changes candidate production and must therefore change the
versioned run-configuration digest and checkpoint compatibility fingerprint.
Stored configuration may contain:

```text
search_backend
search_backend_endpoint_sha256
```

It must not contain a raw endpoint, credentials, cookies, tokens, or response
content. Legacy run-configuration payloads remain replayable with the built-in
backend.

## Acceptance

Implementation acceptance for this foundation requires:

1. Legacy parser and source-order contract tests.
2. SearXNG endpoint-validation and JSON-parser tests.
3. Resolver tests proving that `search_backend=None` leaves the built-in path
   unchanged and that an injected backend still uses the existing fetcher,
   budget, trace, filtering, and ranking boundaries.
4. Composition and run-configuration tests proving the selected backend is
   injected and fingerprinted without leaking its endpoint.
5. Focused provider-search tests showing candidates still pass through the
   existing provider, tenant, relationship, title, location, and S7 gates.
6. Scoped test and architecture gates plus `git diff --check`.

The 11-company failure cluster can only be marked recovered after a real
SearXNG endpoint is configured and a frozen focused live run demonstrates a
nonzero batch recovery with zero wrong URLs, cross-company matches, or
cross-tenant matches. If the recovery is only one or two records, the cluster
definition must be revisited rather than declared closed.

## Rollback

The built-in backend remains the default. Rolling back consists of selecting
`builtin`; no candidate, identity, provider, or stage contract changes are
required.
