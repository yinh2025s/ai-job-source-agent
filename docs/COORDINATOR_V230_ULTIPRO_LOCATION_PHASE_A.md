# Coordinator `.230` UltiPro Display-Address Phase A

## Decision

The remaining downstream records do not form one three-company scheduler,
pagination or generic-inventory cluster. The next executable unit is instead a
bounded UltiPro provider-family contract: interpret the provider's public
location display flags before S7 location validation.

This change is not a location relaxation. It replaces an incorrect provider
field choice with the provider-declared structured address.

## Causal Evidence

Target Hospitality's verified UltiPro inventory returns the exact opening:

```text
Security Analyst
opportunity fd291233-38fa-43d7-aa40-f47f2b6cbd25
```

The same location object contains:

```text
LocalizedDescription = "Corporate"
DisplayAddress = true
Address.City = "The Woodlands"
Address.State.Name = "Texas"
```

The current adapter always prefers `LocalizedDescription`, so S6 emits
`Corporate` and S7 correctly rejects the opening against the LinkedIn location
`The Woodlands, TX`.

ARUP is a second independent UltiPro tenant with the same provider shape:
labels such as `ARUP Main` and `Building 560` coexist with
`DisplayAddress=true` and structured Salt Lake City, Utah addresses. These
labels describe a site or organizational location, not the geographic
location required by S7.

## Contract

For each public UltiPro location object:

1. If `DisplayAddress` is exactly `true`, prefer a bounded structured
   `Address.City` plus `Address.State.Name`.
2. If the displayed address is incomplete, do not invent missing geography.
   Fall back to the existing public display label only when no usable
   structured city/state value exists.
3. If `DisplayAddress` is not exactly `true`, preserve the current
   `LocalizedName` / `LocalizedDescription` behavior.
4. Multiple locations remain deduplicated and joined in provider order.
5. Boolean-like strings, malformed addresses, non-string fields and arbitrary
   free text do not activate the structured-address path.
6. Provider, tenant, board, opening ID, title, hiring relationship and S7
   validation remain unchanged.

## Acceptance

- A Target-shaped record emits `The Woodlands, Texas`, not `Corporate`.
- An ARUP-shaped record emits `Salt Lake City, Utah`, not a building label.
- A location with `DisplayAddress=false` retains its public display label.
- Missing or malformed structured address data safely falls back without
  constructing partial or synthetic geography.
- Multi-location ordering and deduplication remain deterministic.
- Existing UltiPro pagination, URL, tenant and malformed-response tests pass.
- Focused Target live reaches the same verified company, UltiPro tenant,
  canonical board and exact opening; only the provider-owned location evidence
  may change.
- Same-version replay has zero mismatch, fixture gap and tape divergence.

## Ownership

- UltiPro line: `job_source_agent/providers/ultipro.py` and
  `tests/test_provider_ultipro.py`.
- Main line: version, Phase C, governance docs, integration tests, focused live,
  replay and URL identity audit.

## Rollback

Revert `.230` if a display flag other than boolean `true` activates the address
path, if partial address fields are synthesized into unsupported geography, or
if any company/provider/tenant/title/location gate is weakened.
