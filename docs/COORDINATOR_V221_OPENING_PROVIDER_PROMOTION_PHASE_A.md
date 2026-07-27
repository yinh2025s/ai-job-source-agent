# `.221` Opening Provider Promotion Phase A

Status: deferred after Phase A. The shared continuity defect is real, but the
frozen evidence predicts only one immediate Exact recovery; location/title
defects dominate the other records. It is retained for a later batch and is
not implemented in `.222`.

## Frozen Evidence

The code-frozen `.220` Fresh100 trace contains one shared identity-continuity
failure across three independent companies:

| Company | S5 board identity | S6 evidence | S7 result |
| --- | --- | --- | --- |
| Arkema | verified first-party `generic` board | native SuccessFactors page detection and opening inventory | generic/provider chain mismatch |
| Aramark | verified first-party declared inventory | selected URL is a tenant-bearing SuccessFactors opening | opening identity missing |
| Cintas | verified first-party `generic` board | native SuccessFactors page detection and opening inventory | generic/provider chain mismatch |

The common trigger is not `RESULT_IDENTITY_MISMATCH`. In all three records,
`OpeningMatchStage` receives a relationship-verified generic board, S6 obtains
stronger provider-owned evidence for the selected opening, and
`_provider_identity()` rebuilds identity only from the original generic Job
List URL. The typed provider evidence is therefore lost before S7.

## Contract

S6 may promote a relationship-verified generic provider identity to a typed
provider identity only when one of these evidence paths is complete:

1. The selected opening URL is directly recognized by a registered provider,
   is the exact selected URL in a verified declared first-party inventory, and
   yields a concrete provider tenant and canonical board; or
2. A native adapter was selected from fetched page evidence, the trace carries
   its canonical board and tenant identifier, and the selected opening is from
   that same native adapter result.

Promotion must preserve the already verified hiring entity and first-party
relationship evidence. It must not infer a tenant from a search snippet,
hostname similarity, title similarity or an unverified candidate route.

The opening must still pass the existing title and location gates. Provider
promotion does not authorize a wrong city or a weaker title.

## Acceptance

- Arkema's Beaumont record may become Exact only for the Beaumont opening.
- Arkema's Clear Lake selection for a Beaumont source remains rejected.
- Aramark's exact Indianapolis declared-inventory opening receives a complete
  SuccessFactors provider/opening identity chain.
- Cintas selections outside Fort Myers remain rejected.
- Page-evidence traces preserve provider, canonical board and tenant identity
  through checkpoint/replay.
- Missing tenant, mismatched selected URL, unverified generic relationship,
  search-only evidence and cross-provider evidence all fail closed.
- No company, domain, tenant or job ID special case is added.

## Rollback

Revert the change if a typed provider can be promoted without immutable tenant
evidence, if selected-opening provenance is not checked, or if any location,
company or tenant rejection becomes an Exact result.
