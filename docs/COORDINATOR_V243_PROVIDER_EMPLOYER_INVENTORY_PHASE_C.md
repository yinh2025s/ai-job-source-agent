# v243 Provider Employer Inventory - Phase C

## Result

Native adapter employer evidence is now carried through the common matcher
trace. Greenhouse emits `company_name` only when the API opening belongs to the
same tenant as the board. Exact Oracle detail results can emit
`hiringOrganization`; plain Oracle inventory performs no speculative detail
fan-out. Existing Ashby opening-bound evidence uses the same trace contract.

Complete no-match authorization requires one unique, strictly matching
provider-owned employer item for every inventory opening.

## Live Evidence

### Fabric

The official Greenhouse API returned two complete openings. Both openings:

- belong to tenant `fabric83`;
- publish `company_name=Fabric`;
- do not match `Product Designer`.

A real S6/S7 focused execution returned:

```text
S6: partial / OPENING_NOT_FOUND
Job List: https://job-boards.greenhouse.io/fabric83
provider relationship: verified
hiring evidence: provider_inventory
S7: success
opening: none
```

### Caesars

Oracle returned complete title-filtered inventory, but the public detail pages
did not expose valid JobPosting employer identity. The bounded detail
experiment recovered zero records and was removed to avoid three extra requests
without recall gain. Caesars remains relationship-unverified.

### Prophetic

Ashby returned the exact `UX Designer` opening in Portland but no opening-bound
employer evidence. The provider board identifies `Prophetic Technologies Inc`,
while S2 selected an unrelated Canadian `Prophetic Software Inc` website from a
slug-derived candidate. This is a resolver identity collision, not an Ashby
inventory failure. It remains fail closed.

## Cluster Decision

The original three-provider grouping is rejected as one causal recall cluster:

- Fabric: Greenhouse adapter evidence projection.
- Caesars: Oracle payload lacks employer evidence.
- Prophetic: resolver/company identity collision.

The Greenhouse recovery is accepted as a provider-family contract improvement,
but it does not rewrite the aggregate Fresh100 score because the full
end-to-end Fabric search route was not reproduced in the `.243` run.

## Gates

- Relevant tests: 506 passed.
- Provider benchmark: 25/25.
- Resolver benchmark: 6/6.
- Architecture validation: 46 adapters / 0 issues.
- `git diff --check`: clean.
- No full test suite or full Fresh100 run was performed.
