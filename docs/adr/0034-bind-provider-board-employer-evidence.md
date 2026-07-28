# ADR-0034: Bind Provider Board Employer Evidence

Status: accepted

Date: 2026-07-29

## Context

Opening-scoped provider evidence can prove which employer published a specific
job. It cannot protect a complete empty or title-filtered inventory because no
opening exists to carry that evidence.

GovernmentJobs exposed the gap across three independent tenants. College
Station contained the target opening, Lubbock returned a complete filtered
inventory without the target, and the Wichita tenant explicitly represented
the City of Wichita while the source input claimed `WICHITA COMPANY LIMITED`.
Treating all three as generic S6 misses either loses valid terminals or risks a
cross-employer no-match.

## Decision

`AdapterResult` may carry immutable
`ProviderPublishedBoardEmployerEvidence` containing:

- the normalized recruiting employer name;
- the provider's original display name;
- the canonical board evidence URL;
- a bounded extraction method identifier.

The evidence is provider-owned and may only come from an unambiguous current
board response. S6 compares it with the resolved hiring entity for both Exact
and no-opening paths. A conflict overrides route authorization and fails closed
as company identity ambiguous. Matching evidence does not independently create
a hiring relationship, parent/brand relationship, tenant or opening.

GovernmentJobs extracts one agency heading from the verified tenant shell.
Provider UI suffixes such as `Careers` and `Human Resources` may be removed
only at the end of that heading; the original display value remains in trace.
Its inventory comes from the frontend-declared same-host XHR and remains
subject to tenant, count, opening URL and location continuity checks.

## Consequences

- Complete provider inventories can reject a wrong employer even when they
  contain no candidate opening.
- Exact and verified no-match outcomes use the same current provider identity
  boundary.
- Existing adapters remain source-compatible because board evidence is
  optional.
- Provider trace dictionaries alone cannot manufacture the typed contract;
  adapters construct and validate it before publication.
- A board-employer mismatch never becomes evidence that the source posting is
  closed or absent.
