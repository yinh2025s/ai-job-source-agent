# Coordinator `.230` UltiPro Display-Address Phase C

## Decision

The UltiPro display-address contract is implemented and accepted. Target
Hospitality recovers from an S7 location rejection to an identity-verified
Exact opening without changing any company, provider, tenant, title or
location threshold.

## Implementation

- `DisplayAddress=true` now selects a complete public `Address.City` and
  `Address.State.Name` before a site/building label.
- The structured path requires the literal boolean and both non-empty string
  fields.
- False, string-like, malformed and partial display-address values retain the
  existing public-label fallback.
- Multiple public locations remain deterministic and deduplicated.
- UltiPro board, tenant, opening, inventory and S7 contracts are unchanged.

## Focused Live

Artifact root:

```text
/private/tmp/fresh1-v230-target-20260723-run1
```

Target Hospitality completed Website, Career, Job Board, opening match and S7:

| Field | Verified value |
| --- | --- |
| Company / hiring entity | Target Hospitality |
| Provider | UltiPro |
| Tenant | `TAR1004TARG/bae0b5b6-65ef-4503-b57a-b9283744bca8` |
| Title | Security Analyst |
| Location | The Woodlands, Texas |
| Opening | `OpportunityDetail?opportunityId=fd291233-38fa-43d7-aa40-f47f2b6cbd25` |

The title-filtered inventory was complete with seven candidates. S7 classified
the selected location as exact. No wrong URL, wrong location, cross-company or
cross-tenant result was published.

The automatically exported all-outcome replay reproduced 1/1 and passed its
outcome gate with the same opening and verified identity assertion.

## Gates

- UltiPro adapter: 15/15 tests.
- Integrated provider/matcher/S7/checkpoint slice: 214/214 tests.
- Provider benchmark: 25/25.
- Resolver benchmark: 6/6.
- Architecture validation: 46 adapters, zero issues.
- `git diff --check`: passed.

The full 2,500+ test suite is deferred until the backend behavior set is
frozen; this provider-family change used scoped tests and the standard small
offline gates.

## Closure Effect

The conservative Fresh100 development projection gains one audited Exact:
28 to 29. `RESULT_IDENTITY_MISMATCH` decreases from four to three and total
unresolved decreases from 62 to 61. This focused result does not replace a
future code-frozen 100-record cold benchmark.
