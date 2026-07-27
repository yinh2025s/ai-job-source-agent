# v242 Provider Identity Continuation - Phase A

## Causal Cluster

Caesars Entertainment, Fabric and Prophetic share one executable backend
failure:

```text
S5 produced a typed native-provider candidate portfolio
provider relationship is still unverified
S5 = partial / COMPANY_IDENTITY_AMBIGUOUS
live_batch_eval returns before S6
```

The lower-level S6 portfolio runner is already designed to inspect official
provider inventory and remain fail closed when employer, provider or tenant
evidence is missing. The batch coordinator prevents that validation from
running because it currently requires an already verified public Job List.

CHAMP is the negative control. Its Greenhouse candidates belong to other
companies and must remain unpublished and non-Exact after S6.

## Common Code Path

This phase changes only the two-phase backend coordinator in
`scripts/live_batch_eval.py`. It does not change candidate ranking, relationship
verification, provider adapters, title/location matching or S7.

An S5 result may continue to S6 when either:

1. S5 already succeeded and published a verified Job List; or
2. S5 is `partial / COMPANY_IDENTITY_AMBIGUOUS`, its identity assertion contains
   one non-generic provider with `relationship_verified=false`, and its S5 trace
   contains a matching typed provider portfolio.

Every other S5 partial remains terminal.

## Safety Contract

- Continuation is permission to validate, not permission to publish.
- The unverified board remains absent from product
  `DiscoveryResult.job_list_page_url`.
- S6 may authorize a route only from provider-owned inventory/detail evidence
  bound to the same provider, tenant and opening.
- Search result titles, snippets, ranking and tenant-name similarity cannot
  authorize a relationship.
- Employer evidence must strictly match the source company after only approved
  legal-form normalization.
- Missing or conflicting employer evidence remains
  `COMPANY_IDENTITY_AMBIGUOUS`.
- Exact still requires title, location, opening state and the complete S7
  identity chain.
- Cross-company and cross-tenant candidates remain rejected.

## Checkpoint Boundary

The split process may continue only when the S5 checkpoint prefix is durable.
If the typed portfolio cannot be serialized safely, the coordinator must not
pretend it can resume directly at S6. That case remains partial until the
portfolio checkpoint contract is made replay-safe.

## Acceptance

1. Caesars, Fabric and Prophetic use the same continuation predicate; no
   company-specific branch is added.
2. A valid identity-pending provider portfolio reaches S6.
3. `JOB_BOARD_NOT_FOUND`, generic candidates, missing/mismatched provider
   identity, malformed trace and unsafe checkpoint portfolios do not continue.
4. CHAMP-like cross-company candidates publish neither Job List nor Exact.
5. Existing verified Job Lists keep their current S6 path.
6. Focused unit/integration tests and scoped replay pass before any larger live
   run.

## Rollback

Revert the coordinator predicate and adapter version. S5 publication safety from
`.241` remains intact; affected records return to the earlier conservative
partial terminal.

## Out Of Scope

This phase does not yet add Greenhouse, Oracle or Ashby employer extractors. It
does not enable coordinator-v2, extension work, blind cohorts or the isolated
LLM branch.
