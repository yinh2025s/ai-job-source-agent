# `.221` Detail Location Enrichment Phase A

## Frozen Evidence

Three independent companies have the same executable failure despite already
having the correct specific opening URL:

| Company | Correct opening | Detail evidence | Current failure |
| --- | --- | --- | --- |
| Lorum | `/open-roles/devops-engineer-34274` | page-bound JobPosting: New York | encoded JobPosting is not parsed |
| Sunbird Software | JazzHR `RfBS8vS11O` | page-bound JobPosting: Sioux Falls, SD | typed provider returns before detail enrichment |
| IMG | JazzHR `yc2AIb13kq` | page-bound JobPosting: Indianapolis, IN | typed provider returns before detail enrichment |

Lorum's detail page is already present in the frozen `.220` snapshot. Current
public captures of the two JazzHR details confirm exact URL, title, tenant and
structured location. All three expose schema.org `JobPosting` data bound to
the selected detail URL.

StatRad is excluded from this cluster. Its page exposes San Diego only in
prose, not in the same structured contract, so this phase must not add a broad
free-text location heuristic.

## Root Cause

`JobOpeningMatcher._select_with_verified_detail()` enriches location only for
generic candidates. Native provider candidates return immediately when their
list inventory omits location. In addition, the strict JobPosting reader only
accepts JSON-LD script blocks and misses bounded HTML-encoded JSON objects.

The common code path is: exact-title candidate with no location -> same opening
detail has URL-bound structured location -> detail evidence is not projected
into the selected candidate -> S7 cannot verify location.

## Contract

1. When a target location exists and the selected candidate has no strict
   location evidence, perform bounded detail enrichment for generic and typed
   providers.
2. Generic candidates must remain on the same registrable site as the verified
   Job List.
3. Typed candidates must identify to the same provider and tenant as the
   verified Job List.
4. Accept only a JobPosting whose normalized URL equals the fetched and
   selected detail URL, whose title matches the target, and whose structured
   location matches the target.
5. For generic routes, retain the existing hiring-organization same-site check.
   Typed routes do not derive hiring relationship from detail metadata; the
   already verified provider tenant remains the authority.
6. HTML-encoded JSON is accepted only as a bounded, parseable object containing
   a schema.org JobPosting. Arbitrary prose is not location evidence.
7. A wrong, broad or missing location remains rejected.

## Acceptance

- Lorum, Sunbird and IMG each select the existing exact opening with structured
  location evidence.
- A wrong-city JazzHR detail does not pass.
- A cross-tenant provider detail does not get fetched or selected.
- A same-title detail whose declared URL differs from the selected URL does not
  pass.
- StatRad remains unresolved unless a separate evidence-backed parser contract
  is established.
- Focused fixture tests and frozen three-record replay pass without weakening
  company, provider, tenant, title or location identity.

## Rollback

Revert if typed provider enrichment crosses tenant boundaries, if encoded text
that is not a JobPosting is accepted, or if any wrong-city candidate reaches
S7 Exact.
