# ADR-0018: Verify First-Party Dynamic Job Inventory

- Status: accepted
- Date: 2026-07-14

## Context

Some verified career pages render no job links in HTML. Their first-party route
bundle instead performs a public GET for a complete inventory whose records link
to a supported ATS. Blind ATS search can miss that board even though the website
already publishes stronger evidence.

## Decision

1. S5 may inspect at most three first-party JavaScript assets already selected by
   the bounded provider-asset probe and one statically imported client dependency.
   Each body is scanned only through five megabytes.
2. The route must be a literal client `.get("/api/...")` call with job/career,
   posting, or position semantics plus an explicit list token. Computed routes,
   POST requests, queries, fragments, traversal segments, and arbitrary scripts
   are unsupported.
3. The API base must be a literal HTTPS URL on the exact career-page hostname,
   default port, no credentials, query, or fragment, and an explicit `api` or
   `api-proxy` path. Redirects are rejected.
4. A direct `/api` endpoint uses no Authorization. A same-origin `/api-proxy`
   endpoint receives only the deterministic public site marker
   `Bearer <exact page hostname>`. Bundle Authorization values are never read or
   forwarded, so private tokens cannot enter trace, snapshots, or requests and
   sanitized bundle replay remains deterministic.
5. The response is at most five megabytes and 5,000 rows. It must be an array or
   `{data: array}` with scalar metadata. Every row must contain a bounded nonempty
   title and URL, and every URL must resolve through a listing-capable native
   adapter to the same provider and canonical board. A malformed row or mixed
   tenant invalidates the whole inventory.
6. A schema-valid empty array is explicit first-party empty evidence. Fetch
   failures remain typed incomplete evidence. Invalid, redirected, mixed, or
   oversized payloads never become empty/no-match evidence.
7. Trace stores only method, bounded asset/endpoint URLs, status, count, provider,
   canonical board, response source, and sanitized fetch classification. Exact
   opening URLs and response rows remain outside trace. Snapshot/replay uses the
   normal request-aware GET identity; Authorization is excluded as sensitive.
8. This is a generic first-party evidence probe. It must not branch on company
   name, hostname, provider name, or known opening ID.
9. S6 still verifies the native inventory. A score threshold alone cannot bind
   the LinkedIn title to an opening: normalized target tokens must occur in
   order, or both titles must have the same normalized token multiset. `Sr` and
   `Jr` normalize to their full level names; a one-token target requires title
   equality after normalization.

## Consequences

- A JavaScript-only official career page can hand S5 a native ATS board without
  web search or browser automation.
- Dynamic code outside the frozen literal-GET shape fails closed until a separate
  contract is justified.
- Current inventory, rather than a stale benchmark URL, decides whether a title
  is still publicly open.
- High token overlap cannot turn a different role family into a successful
  opening URL.

## Validation

- Tests cover successful same-origin discovery, deterministic public headers,
  no-auth endpoints, ignored bundle authorization, cross-origin bases and dependencies,
  redirects, mixed tenants, malformed and oversized payloads, explicit empty
  inventory, retryable fetch classification, bounded asset traversal, and
  ordered title identity.
- Hostinger focused live verifies 77 first-party records and its canonical Ashby
  board. The current inventory does not contain the frozen exact `AI Engineer`
  role; `Full Stack Engineer (Automation & AI Agents)` is rejected as a
  different title identity. Fresh scoped replay reproduces the verified no-match
  without a fixture gap or request divergence.
- Final gates pass 1266 tests, 25/25 provider cases, 6/6 resolver cases, and 24
  adapters / 0 architecture issues.
