# Coordinator `.232` Tenant-Probe Provenance Phase A

## Causal Cluster

`verified_tenant_probe` currently means that a provider adapter confirmed a
tenant and could read its public inventory. Two later steps can incorrectly
upgrade that existence signal into a verified hiring relationship:

1. `ProviderCandidatePortfolioBuilder` maps every non-search, non-External
   candidate to `linked_url_evidence`, so a tenant probe becomes
   indistinguishable from an observed first-party link.
2. `_candidate_hiring_relationship` authorizes a tenant probe when the tenant,
   company and official-domain names match, even though no page actually linked
   the provider board.
3. Stored provider evidence can later be reauthorized from tenant-name
   similarity before checking how the relationship was originally proven.

The shared trigger affects at least Focus, OneApp and STRIKE. Focus demonstrates
the safety impact directly: the public Ashby tenant `focus` belongs to another
employer, while the target LinkedIn company is also named Focus. Slant CRM is a
positive control because its Ashby opening publishes opening-scoped employer
and descriptor evidence; that stronger route must remain valid.

## Contract

A verified tenant probe proves only:

- the provider;
- the canonical tenant and board;
- that the provider endpoint or inventory exists.

It does not prove:

- that the source company controls the tenant;
- that the source company's Website or Career page handed off to the board;
- that a matching tenant token is a legal, parent or brand relationship.

A provider candidate may become relationship-authorized only through:

- observed LinkedIn External Apply;
- an observed first-party Website/Career handoff;
- provider-published employer evidence bound to the same board/opening; or
- an already verified parent/brand relationship with durable first-party
  provenance.

## Change Boundary

Phase B changes:

- `job_source_agent/candidate_portfolio.py`;
- `job_source_agent/stages/discovery.py`;
- focused candidate/discovery tests;
- adapter version and Phase C governance summaries.

It does not change provider adapters, tenant generation, search queries,
inventory completeness, title/location matching, S7 thresholds, the extension,
LLM code or sealed cohorts.

## Acceptance

1. A tenant probe has an explicit `verified_tenant_probe` detection method and
   never masquerades as `linked_url_evidence`.
2. Exact tenant/company/domain name equality alone leaves the candidate
   unauthorized.
3. Focus, OneApp and STRIKE tenant probes cannot create a verified
   `HiringRelationshipEvidence`, durable provider record or Exact result
   without an independent handoff.
4. Slant CRM remains authorized through `provider_published_employer`.
5. True External Apply and first-party Career handoffs remain authorized.
6. Stored provider evidence is reauthorized only when its source and
   verification method describe a durable relationship; tenant-name similarity
   cannot repair missing provenance.
7. Focused live/replay publishes zero wrong company, tenant, location or
   opening URL.

## Rollback

Restore the previous generic detection mapping, tenant-probe relationship
branch and stored tenant-name shortcut. No persistent schema migration is
required; the adapter version bump invalidates old checkpoints.
