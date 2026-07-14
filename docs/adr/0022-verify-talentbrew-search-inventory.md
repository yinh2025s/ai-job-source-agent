# ADR-0022: Verify TalentBrew Search Inventory

- Status: accepted
- Date: 2026-07-14

## Context

TalentBrew career sites, now operated by Radancy, expose a first-party SSR job
search behind customer-owned domains. A branded homepage may contain an empty or
personalized job-list module while the authoritative public inventory lives at a
localized `/search-jobs` route.

## Decision

1. A page-aware adapter identifies TalentBrew only when a public HTTPS page has
   mutually consistent tenant and site metadata, a same-origin localized GET
   search form, matching hidden organization identity, and an exact
   `tbcdn.talentbrew.com/company/{tenant}/` asset fingerprint. Radancy analytics,
   homepage text, or a dynamic `postmodule` alone are insufficient.
2. The canonical board is `/{locale}/search-jobs`. Its replay-safe locator stores
   only host, locale, tenant ID, and site ID, each strictly bound to the public
   board URL. No cookie, visitor/session ID, personalization state, geolocation,
   analytics endpoint, or auxiliary Workday/WillHire route is retained.
3. S6 issues a bounded same-origin GET using only the page-declared keyword,
   location, organization, and page parameters. Normal title queries use SSR
   pages; facet, autocomplete, analytics, personalized modules, and JSON POST
   fallback are outside this contract.
4. Every page must expose stable total, page count, current page, and page-size
   metadata plus typed job cards. Job ID, card identity, detail URL tenant, locale,
   and final path ID must agree. Counts and pagination must remain consistent and
   global job IDs must be unique.
5. Filtered empty is authoritative only for a schema-valid zero total with zero
   cards. Complete no-match requires all filtered pages within the cap. Empty
   fragments, `hasJobs`, marketing copy, fetch failure, contradictory metadata,
   duplicates, or a cap produce incomplete inventory.
6. Search responses and detail URLs remain on the exact board origin. Redirects,
   credentials, non-default ports, sensitive query fields, private hosts, and
   tenant/path escapes fail closed. Trace stores only bounded counts, page and
   provider identity, sanitized URLs, and typed errors.

## Consequences

- Acquired-brand handoff can type a parent TalentBrew portal at S5 while S6 still
  independently proves inventory and exact opening evidence.
- The provider name is `talentbrew`, not generic `radancy`, because other Radancy
  products do not share this frozen protocol.
- Large unfiltered inventories may remain incomplete at the page cap; the system
  does not convert bounded partial reads into company-wide no-match.

## Validation

- Provider tests cover strong and spoofed fingerprints, two synthetic tenants,
  replay-safe locator binding, SSR request identity, typed cards, stable
  pagination, exact-title early success, verified zero, malformed/empty fragments,
  duplicate and tenant-mismatched records, redirects, and caps.
- Pipeline tests exercise a generic acquired-brand page into a parent TalentBrew
  board without changing S3 company identity.
- Focused live and scoped replay validate the CyberArk-shaped handoff without
  committing raw pages or adding company-specific mappings.
