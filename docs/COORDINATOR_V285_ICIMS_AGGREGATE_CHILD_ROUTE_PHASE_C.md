# Coordinator `.285` iCIMS Aggregate-to-Child Route Phase C

Date: 2026-07-29

Decision: **accepted for the bounded provider contract**

## Scope

This phase implements the Phase A contract for provider-declared iCIMS
aggregate boards whose job cards publish exact openings on child tenants. It
does not trust sibling `*.icims.com` hosts, introduce company allowlists,
change coordinator-v2, access sealed cohorts or alter the LLM branch.

## Implementation

- Added immutable `ProviderOpeningRouteEvidence`.
- Added first-class `OpeningMatchOutcome`; trace cannot authorize a route.
- Extracted child URL, numeric ID, card-local title/location, customer marker
  and `hub` from the current aggregate response.
- Re-fetched child detail and verified customer, ID, title, location, employer
  and canonical target tenant.
- Followed one same-host, same-opening `in_iframe=1` payload when the canonical
  detail is an iCIMS shell.
- Preserved child tenant and child canonical board through selection and S7.
- Preferred a complete typed route over a generic-shell alias only for the
  same canonical opening.

Identity/result/checkpoint/adapter versions are
`1.2` / `2.3` / `1.9` / `2026-07-29.285`.

## Provider Controls

| Control | Source tenant | Target tenant | Result |
| --- | --- | --- | --- |
| Cretex | `cretex-companies.icims.com` | `careers-cretex.icims.com` | exact route verified |
| Emory Healthcare | `ehccareers-emory.icims.com` | `clinical-emory.icims.com` | exact route verified |
| Ho-Chunk | `hub-hochunk.icims.com` | `careers-allnativegroup.icims.com` | exact route verified |

Negative tests reject missing/conflicting markers, malformed or duplicate hub
evidence, unsafe query/origin, undeclared child routes, redirects, opening ID,
title, location and employer conflicts, closed openings, trace-only evidence
and checkpoint mutation.

## Cretex Focused Live

All runs used fresh isolated roots. No `.283` checkpoint, completion, evidence
or snapshot was restored.

| Run | Budget | Terminal | Finding |
| --- | ---: | --- | --- |
| run1 | 120s | company budget exhausted | opening reserve starved before provider fetch |
| run2 | 240s | opening not found | canonical child response was an iCIMS shell |
| run3 | 240s | identity ambiguous | generic and native routes reported the same URL |
| run4 | 240s | S7 Exact | typed route reached the child opening |

Run4 artifact root:
`/private/tmp/fresh100-v285-icims-route-focused-20260729-run4`.

The final opening is
`https://careers-cretex.icims.com/jobs/5219/it-cyber-security-risk-analyst/job`.
The serialized identity assertion verifies:

- source tenant and board:
  `cretex-companies.icims.com/jobs/search`;
- target tenant and board:
  `careers-cretex.icims.com/jobs/search`;
- customer identity: `cretex.icims.com` on both sides;
- opening ID: `5219`;
- title/location:
  `IT Cyber Security Risk Analyst` / `Elk River, MN`;
- selection and published URL equal the canonical child opening.

Strict replay exported and reproduced 1/1 with zero mismatch, fixture gap,
budget recovery or expected transition. Credential-shape scan found zero
Google browser-key, AWS access-key or JWT-shaped value.

## Gates

- full tests: 2,903 passed, 4 skipped;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 48 adapters, 0 issues;
- `git diff --check`: passed;
- wrong URL/location/company/tenant publication: 0.

## Decision Boundary

The provider contract is accepted because three independent controls share the
same trigger and production path. Only Cretex belongs to Fresh100, and its
focused recovery does not change the authoritative `.283` result of 36/100.
A new full Fresh100 measurement requires separately frozen code, new runtime
roots and explicit authorization.
