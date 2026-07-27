# v259 Location-Aware Snapshot Redaction - Phase A

## Cluster

Seven independent companies across three provider paths share one replay
defect:

| Provider | Companies | Mutation |
| --- | --- | --- |
| BambooHR | ReachMobi; Team Royal | state becomes `offline-replay-redacted-credential` |
| Paylocity | ADDMAN; iClassPro | state becomes `[REDACTED]` |
| Hireology | San Diego Padres; Mills Automotive Group; Tim Moran Hyundai | state becomes `offline-replay-redacted-credential` |

Live title, URL, provider and tenant remain correct, but replay location differs
because a geographic `state` field is treated as OAuth credential state.

## Root Cause

`snapshot.py` derives response-body sensitive fields directly from the query
credential set. Structured JSON redaction and the HTML fallback therefore
redact every key named `state`, regardless of whether it is an OAuth parameter
or a location field. Replay hydration then turns JSON placeholders into
`offline-replay-redacted-credential`; embedded Paylocity JSON retains the
literal placeholder.

## Implementation Boundary

The change may touch only snapshot sanitization, replay hydration, their
focused tests and adapter version:

- keep query-string `state` sensitive;
- keep unknown response-body `state` sensitive by default;
- preserve `state` only inside structurally verified public location/address
  objects with sibling geographic evidence;
- parse supported embedded JSON structurally instead of applying a flat
  case-insensitive key regex;
- never restore or synthesize credential values during replay;
- do not change provider parsing, matching, identity, tenant or S7.

No company or provider special case is allowed.

## Acceptance

1. Focused sanitizer tests cover public location state and OAuth state.
2. BambooHR, Paylocity and Hireology fixture paths preserve location exactly.
3. OAuth/CSRF/token/authorization values remain redacted.
4. Newly captured focused snapshots replay live location exactly for all
   available cluster controls.
5. Opening URL, provider, tenant, title and terminal classification do not
   regress.
