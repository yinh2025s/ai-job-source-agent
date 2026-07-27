# v259 Location-Aware Snapshot Redaction - Phase C

## Decision

Accepted as a seven-company, three-provider replay-correctness closure.

## Implementation

Query and unknown response-body `state` remain sensitive. A body value is
preserved only when:

- its normalized key is exactly `state`;
- the same mapping contains a public geographic sibling such as city, country,
  postal code, address or location;
- the mapping does not contain OAuth/authentication context;
- the state value is bounded public text without URL, markup or control
  characters.

Valid JSON and strict JSON objects embedded in HTML use the same structural
classifier. Flat unquoted assignments, hidden inputs, meta tags and unknown
objects continue to redact `state`. Replay hydration uses the same context and
never restores a secret.

## Focused Live And Replay

Frozen input:
`/private/tmp/v259-location-redaction-input.json`

SHA-256:
`65ad0a60bf023b3eeb8fcb0f0a4bdc5c0941766569b56770e0acc75fd6bb2e9e`

Artifact:
`/private/tmp/v259-location-redaction-run1`

| Company | Provider | Live/replay location |
| --- | --- | --- |
| ReachMobi | BambooHR | Bonita Springs, Florida |
| Team Royal | BambooHR | Lafayette, Louisiana |
| ADDMAN | Paylocity | Statesville, NC |
| iClassPro | Paylocity | Headquarters; Longview, TX, USA |
| San Diego Padres | Hireology | San Diego, CA |
| Mills Automotive Group | Hireology | Henderson, NC |
| Tim Moran Hyundai | Hireology | Hemet, CA |

Result:

- live Exact: 7/7;
- automatic replay Exact: 7/7;
- URL, title, location and location-classification mismatch: 0;
- fixture gap, tape divergence or missing boundary: 0;
- `offline-replay-redacted-credential` in captured location evidence: 0.

Old snapshots remain immutable and cannot recover state values already removed.
Acceptance therefore uses newly captured snapshots rather than rewriting old
artifacts.

## Offline Gates

- snapshot/replay/provider/pipeline tests: 276 passed;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 47 native adapters, 0 issues;
- scoped `git diff --check`: passed.

The full test suite was not run for this bounded sanitizer/replay change.

## Remaining Safety Work

CRG and Symmetrio are two confirmed opening-level intermediary false positives.
CTTX Health remains provisional because its captured opening body does not
prove an undisclosed client. The strict three-company gate is not met, so no
intermediary implementation follows from `.259`.
