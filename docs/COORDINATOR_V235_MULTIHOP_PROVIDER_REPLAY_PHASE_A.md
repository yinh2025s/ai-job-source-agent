# Coordinator `.235` Phase A: Multi-hop provider replay

## Causal cluster

Jushi Holdings follows a valid first-party chain:

```text
LinkedIn company identity
-> https://jushico.com/careers/
-> https://jushico.com/job-listings
-> https://jobs.lever.co/jushico
```

The live run captured the Lever board as a stored, verified first-party
provider input. Scoped replay rejected that producer state because it required
the relationship evidence URL to equal the Career root exactly. That condition
models a one-hop handoff, not the common first-party intermediate-page shape.

## Contract

Scoped replay may restore a captured `VerifiedProviderBoardEvidence` when all
of the following remain true:

1. The captured S5 source is `stored_verified_provider_board`.
2. The replay boundary is `job_board_discovery`.
3. Company name and LinkedIn company identity match the record-local evidence.
4. Website and Career evidence retain their original identity continuity.
5. Provider source is `first_party_handoff`.
6. Relationship evidence is on the same normalized HTTPS host as the verified
   Career page. A sibling path is allowed; a different host is not.
7. The provider registry independently reconstructs the same provider, tenant,
   and canonical board URL.

The restored board is producer state only. It does not independently authorize
an opening or bypass provider inventory, title, location, hiring relationship,
or S7 validation.

## Acceptance

- A same-host `Career -> jobs page -> ATS` chain restores deterministically.
- Cross-host relationship evidence is rejected.
- Cross-provider, cross-tenant, non-first-party, and non-stored inputs remain
  rejected.
- The captured Jushi bundle builds and replays without fixture gaps or tape
  divergence.
- Existing captured-producer-state replay tests remain green.
