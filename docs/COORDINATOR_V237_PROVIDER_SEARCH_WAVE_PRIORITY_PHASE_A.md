# Coordinator `.237` Phase A: provider-search wave priority

## Refinement

`.236` successfully creates a cooperative S4 boundary and executes S5 for all
five focused companies. It does not fully satisfy the provider-reservation
contract:

- Provider Search executes two of five configured queries for four companies.
- Splashlight executes zero Provider Search queries.
- `CareerSurfaceCandidateDiscovery` consumes the reserved S5 search window
  before `ProviderSearchCandidateDiscovery`.

The reservation is explicitly named and configured for provider search. S4 has
already completed the bounded first-party Career exploration before S5 starts,
so S5 should give the independent ATS route first use of that reserved window.

## Contract

Within the S5 search wave:

```text
ProviderSearchCandidateDiscovery
-> CareerSurfaceCandidateDiscovery
```

Direct External Apply and Website/Career candidates retain their existing
higher-priority wave. Both search sources remain enabled; only their execution
order changes. Candidate ranking, provider verification, relationship evidence
and S7 validation remain unchanged.

## Acceptance

- Composition freezes Provider Search before Career Surface in the search wave.
- All five focused companies execute at least one provider-search query after
  S4 reaches its cooperative reserve.
- Total query, company and opening budgets do not increase.
- No candidate may be published without the existing provider/tenant and
  relationship gates.
