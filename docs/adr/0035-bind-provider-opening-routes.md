# ADR-0035: Bind Provider Aggregate-to-Child Opening Routes

Status: accepted

Date: 2026-07-29

## Context

Some providers expose an official aggregate board on one tenant and publish
individual openings on child tenants. iCIMS does this for at least Cretex,
Emory Healthcare and Ho-Chunk. The current identity contract correctly rejects
a detail URL whose tenant and canonical board differ from S5, but it has no
typed way to represent a provider-declared aggregate-to-child route.

Trusting an arbitrary sibling provider host, a shared parent domain, title
similarity or a matching route parameter would create cross-company and
cross-tenant publication risk.

## Decision

`ProviderOpeningRouteEvidence` is immutable candidate-scoped evidence. It
binds:

- provider, source tenant and canonical source board;
- target tenant and canonical target board;
- canonical opening URL and provider opening ID;
- exact aggregate response URL;
- source and detail provider-customer identities;
- bounded provider route identity;
- extraction method, detail evidence URL and verification state.

An unverified route may exist only between provider extraction and detail
attestation. It cannot contain detail evidence and cannot pass S7. A verified
route requires identical source/detail customer identity and exact detail URL
continuity.

The opening match service returns this object as first-class typed data through
`OpeningMatchOutcome`; downstream stages never reconstruct authority from a
trace dictionary. Trace contains a serialized copy for diagnostics and replay
comparison only.

Ordinary openings still require equal provider, tenant and board identities.
When a verified route is present:

1. its source identity must exactly equal `ProviderIdentity`;
2. its target identity must exactly equal `OpeningIdentity`;
3. its opening URL must exactly equal the selected and published URL;
4. selection keeps the target tenant and child board;
5. provider and hiring-entity continuity remain hard requirements.

For iCIMS, an aggregate claim additionally requires one safe customer runtime
marker, one positive `hub`, a safe numeric detail route, and title/location/
anchor evidence from the same job card. The child detail independently
revalidates customer marker, ID, canonical route, title, location and
provider-published employer. When the canonical child URL is an iCIMS shell,
the matcher may follow exactly one same-host, same-opening
`?in_iframe=1` payload. Extra query parameters, redirects, another host/path
or conflicting evidence fail closed. Child membership comes from the current
aggregate response, never a host allowlist.

If a legacy generic shell and a native board produce the same canonical
opening, the fully route-bound identity supersedes only that alias. Different
verified opening URLs remain ambiguous and are not published.

## Compatibility

Identity contract becomes `1.2`, result schema `2.3`, checkpoint schema `1.9`
and adapter version `2026-07-29.285`. Incompatible checkpoints fail closed.
Legacy public results remain readable but cannot synthesize route evidence.

## Consequences

- Verified provider-owned aggregate inventories can publish exact child
  openings without weakening tenant isolation.
- A valid provider suffix, route parameter or title remains insufficient.
- Provider adapters and detail matchers do more bounded validation work for
  routed candidates.
- Trace tampering cannot authorize a route because S6 passes the typed object
  directly into the opening identity.

## Validation

- Positive provider controls: Cretex, Emory Healthcare and Ho-Chunk.
- Negative controls: undeclared child, cross-company child, marker mismatch,
  malformed/duplicate hub, card-local mismatch, redirect, ID/title/location/
  employer conflict and serialized-payload mutation.
- Cretex must pass snapshot-backed S1-S7 live and empty-checkpoint replay.
- Same-tenant iCIMS controls must remain unchanged.
- Wrong URL, wrong location, cross-company and cross-tenant publication remain
  zero.
