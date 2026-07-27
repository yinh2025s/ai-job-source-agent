# v255 Opening Failure Taxonomy - Phase C

## Decision

Accepted as a four-company replay and failure-taxonomy closure.

## Implementation

Opening discovery now preserves typed `FetchError` fields when producing trace
records:

- canonical reason code;
- retryable flag;
- HTTP status when present;
- transport phase when present.

Availability aggregation also preserves those fields from singular
page/provider detection failures. Human-readable text remains a legacy
fallback only. Candidate generation, ranking, provider selection, identity and
S7 publication are unchanged.

## Focused Live And Replay

Artifact: `/private/tmp/v255-opening-failure-taxonomy-run1`

| Company | Live terminal | Replay | Opening |
| --- | --- | --- | --- |
| Barstool Sports | `OPENING_DISCOVERY_INCOMPLETE` | reproduced | none |
| Ichor Systems, Inc. | `COMPANY_TIME_BUDGET_EXHAUSTED` | reproduced | none |
| i-Pharm Consulting | `OPENING_DISCOVERY_INCOMPLETE` | reproduced | none |
| Plaid | `OPENING_DISCOVERY_INCOMPLETE` | reproduced | none |

Network timing means only Ichor encountered the typed company-budget boundary
in this run. The contract acceptance is that each live terminal is reproduced
from its same-version tape without text-based reason drift:

- reproduced: 4/4;
- mismatch: 0;
- fixture gap: 0;
- tape divergence: 0;
- missing boundary: 0;
- published opening URL: 0/4.

The original v252/v254 captures remain preserved as baseline evidence and are
not overwritten.

## Offline Gates

- scoped matcher, availability, outcome-tape, replay, checkpoint, pipeline and
  incomplete-discovery tests: 270 passed;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 47 native adapters, 0 issues;
- scoped `git diff --check`: passed.

The full test suite was not run for this bounded trace-taxonomy change.

## Scope

This change improves deterministic failure reporting and replay correctness; it
does not claim an Exact recall increase or alter Fresh100 projection. Plugin
work, coordinator-v2, the LLM branch and sealed holdouts remain frozen.
