# `.219` Paylocity Detail Bootstrap Phase A

## Evidence

The clean three-record run at
`/private/tmp/coordinator-v219-focused3-run2` retained zero Exact results and
replayed 3/3 without mismatch or fixture gap. Loveland's provider-search route
nevertheless produced two source-backed URLs for opening `4232544`:

- `https://recruiting.paylocity.com/Recruiting/Jobs/Apply/4232544`
- `https://recruiting.paylocity.com/Recruiting/Jobs/Details/4232544`

Both were rejected as `provider_not_listable`. The Paylocity adapter currently
recognizes only `/Recruiting/Jobs/All/{tenant}/{slug}` boards, while a public
detail URL contains no tenant in its URL.

A bounded read of the provider-owned detail page proved that it publishes all
required bootstrap evidence in one response: the exact detail ID and title,
`window.pageData.moduleName`, and links to one canonical Paylocity tenant UUID
with an optional board slug. The search result itself is not success evidence.

## Contract

Add an optional provider candidate-bootstrap contract. A bootstrap may return a
canonical board only when the fetched provider page proves:

1. the final URL remains the same provider-owned detail and opening ID;
2. exactly one tenant exists in every embedded canonical board locator;
3. zero or one consistent board slug exists for that tenant;
4. provider structured data contains the exact requested title;
5. provider structured data names an employer that passes the existing strict
   employer matcher;
6. the returned opening URL, evidence URL, provider and board are continuous.

The generic portfolio builder may invoke this optional adapter extension once
for a targeted opening candidate. It must not invent a board, infer a UUID from
a company name, or authorize a relationship from a search title/snippet.

## Acceptance

- Paylocity detail fixture resolves to its exact canonical tenant board and
  immutable provider-employer evidence.
- Apply/detail aliases deduplicate to one board after verification.
- Cross-tenant links, conflicting slugs, wrong titles, wrong employers,
  redirects, malformed page data and fetch failures remain rejected.
- Existing first-party Paylocity board behavior and all provider/tenant/S7
  contracts remain unchanged.
- Focused Loveland live must reach a verified Paylocity board and may return
  Exact only if current provider inventory, employer, title, location and S7 all
  pass. One-company recovery does not establish a three-company recall claim.

## Rollback

Revert the bootstrap if it accepts a board without provider-owned tenant and
employer evidence, changes ordinary board canonicalization, introduces a
cross-tenant result, or cannot replay from its captured detail page.
