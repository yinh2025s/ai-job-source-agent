# v243 Provider Employer Inventory - Phase A

## Trigger

After `.242` allowed a typed identity-pending provider portfolio to reach S6,
the common remaining condition is:

```text
official native inventory is complete
provider/tenant continuity is verified
S5 relationship is still unverified
provider payload names the employer
```

Greenhouse exposes `company_name` on inventory records. Ashby can expose
opening-bound employer sections. Oracle inventory omits employer; only an
already selected exact detail may expose `hiringOrganization`.

## Contract

- Employer evidence is provider-owned and bound to one concrete opening URL.
- The opening must belong to the same provider and tenant as the candidate
  board.
- Exact authorization may use the selected opening's unique employer evidence.
- Board/no-match authorization requires complete native inventory and one
  unique, matching employer evidence item for every inventory opening.
- Only approved legal-form normalization is allowed.
- A missing, conflicting, cross-company or cross-tenant item prevents
  authorization.
- Oracle exact-detail evidence preserves exact host, site and opening
  continuity. Inventory does not add speculative detail requests.
- Search snippets, tenant slugs and result titles remain non-authoritative.

## Acceptance

1. Greenhouse emits opening-bound evidence only for same-tenant API records.
2. Ashby existing evidence reaches the common S6 trace.
3. Oracle exact detail emits employer evidence and remains fail closed on
   redirects or malformed details.
4. Complete matching inventory may authorize a verified no-match.
5. CHAMP-like employer mismatch and cross-tenant payloads remain unpublished.
6. Focused provider tests, S5-S7 tests and replay pass.

## Out Of Scope

This phase does not change search ranking, add company exceptions, enable the
extension/coordinator-v2, inspect blind cohorts or merge the LLM branch.
