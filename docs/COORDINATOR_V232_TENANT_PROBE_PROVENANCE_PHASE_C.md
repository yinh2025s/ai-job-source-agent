# Coordinator `.232` Tenant-Probe Provenance Phase C

## Result

`.232` closes the provider tenant-existence versus hiring-relationship
confusion. A verified tenant probe now remains an existence-only candidate and
cannot be promoted by company, domain or tenant-name equality.

The repair covers four layers:

- candidate portfolios retain the explicit `verified_tenant_probe` detection
  method and do not fabricate a first-party relationship URL;
- candidate hiring relationships no longer authorize tenant/domain name
  equality;
- durable stored boards cannot recover missing provenance from a matching
  tenant name;
- S7 rejects legacy `tenant_name_match` and `provider_tenant_match` identities
  even if an upstream boolean was incorrectly set.

Observed External Apply, observed first-party handoffs and provider-published
employer evidence remain authorized.

## Offline Gate

The focused candidate, discovery, identity, checkpoint, provider-search,
opening, pipeline and replay slice passes 457 tests. The negative matrix covers
tenant/domain equality, missing durable provenance and S7 boolean forgery.
Positive controls cover genuine first-party handoffs, stored first-party
evidence and provider-published employer evidence.

No full 2,000-plus test suite was rerun during development.

## Final Same-Version Live

The final isolated run is preserved at:

`/private/tmp/focus4-v232-live-run3`

| Record | Result | Relationship evidence |
| --- | --- | --- |
| Focus - HR Manager | partial Ashby candidate, no opening | tenant probe, strength 0 |
| OneApp - Product Designer | verified first-party generic board, no opening | official Website/Career; Ashby probe unauthorized |
| STRIKE - Project Manager | partial Greenhouse candidate, no opening | tenant probe, strength 0 |
| Slant CRM - Product Designer | S7 Exact | provider-published employer evidence |

Slant CRM returned the known Ashby opening
`https://jobs.ashbyhq.com/slant/1d5a754e-593d-445e-a605-89e674eb077f`
with exact title, Lehi location, provider, tenant and complete inventory.

The Focus and STRIKE provider identities remain `linked_url_only` with
`relationship_verified=false`. OneApp's Ashby probe is also unverified and
cannot displace the official first-party Career board. The evidence store
contains no provider board for Focus, OneApp or STRIKE.

Full captured-outcome replay is 4/4 reproduced with zero mismatch or fixture
gap. Wrong opening URL, company, tenant and location counts are all zero.

## Projection

This is a safety closure, not a Fresh100 recall gain. The conservative
development projection remains 29 Exact, 9 Verified No Match, 1 External
Blocked and 61 unresolved. Focus, OneApp and STRIKE remain in the causal
backlog until an independent first-party or provider-employer relationship is
observed.
