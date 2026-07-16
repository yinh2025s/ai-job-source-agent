# ADR-0027: Bind Provider Candidates To Hiring Relationships

- Status: accepted
- Date: 2026-07-17

## Context

ADR-0025 made External Apply, provider-targeted search, and website/Career
evidence produce untrusted provider candidates. Adapter recognition established
provider and tenant identity, but candidate ordering reused the company-level S3
identity. Once S3 had verified the source company, an unrelated search tenant
could therefore appear authorized for ordering and consume a bounded board
attempt before the correct tenant. S7 rejected an exact opening from that board,
but a relationship-unverified job list could still be published and counted.

## Decision

1. Keep `ProviderCandidate` untrusted and unchanged in meaning. Search rank,
   title similarity, and company-level S3 evidence never authorize a tenant.
2. After adapter recognition, evaluate every candidate into an immutable
   `HiringRelationshipEvidence` containing source company, hiring entity,
   provider, tenant, evidence type, evidence URL, strength, and verification.
3. Only LinkedIn's exact External Apply handoff, a verified first-party Career
   handoff, or a strict provider-tenant/entity match can be strong verified
   relationship evidence. Search snippets and token overlap remain unverified.
4. Candidate ordering prefers a relationship-verified candidate before source
   priority and result rank. An unrelated candidate cannot consume the first
   bounded attempt merely because S3 verified the company.
5. `ProviderIdentity.relationship_verified` is derived from the selected
   candidate's relationship evidence or the existing first-party board rules.
6. S7 validates the hiring/provider relationship whenever a job list is
   published, even if no exact opening exists. A relationship-unverified board
   is diagnostic evidence only and is suppressed from the product result.
7. Exact opening checks retain all provider, tenant, canonical board, opening,
   title, location, and availability requirements.

## Consequences

This change may reduce the raw job-list funnel where the old result represented
only an adapter-recognized but company-unverified board. That is intentional:
the product metric is a verified company job list, not provider detection. The
contract improves candidate attempt ordering without lowering any identity gate
and adds no company, tenant, or URL override.

The identity contract becomes `1.1` and the adapter/checkpoint compatibility
version becomes `2026-07-17.93`.
