# `.220` Paylocity Detail Bootstrap Phase C

## Scope

This phase validates one provider-family contract only: a targeted Paylocity
opening URL may bootstrap its canonical board from provider-owned detail-page
evidence. It does not claim a Fresh100 score change and does not use extension,
LLM or sealed blind inputs.

## Deterministic Gate

A three-company end-to-end fixture matrix now covers Loveland Innovations,
iClassPro and Resolute Road Hospitality. Every record follows the same path:

1. untrusted search lead containing a specific Paylocity detail URL;
2. exact detail ID and requested title verification;
3. one tenant and one consistent canonical board slug;
4. provider-published employer verification;
5. canonical board inventory read;
6. title and location match;
7. S7 company/provider/tenant/opening validation.

All three fixtures reach S7 Exact. Existing negative gates reject wrong titles,
cross-tenant board links, conflicting slugs, changed detail IDs and malformed
page data. Paylocity location normalization also avoids duplicating a structured
country-extended location when `LocationName` already contains the same city
and state.

The focused backend regression passes 183 tests. The current provider benchmark
passes 25/25 and architecture validation reports 46 native adapters with zero
issues. The full suite and resolver benchmark were not rerun; the previous
`.219` frozen gate remains 2,625 tests with four skips and resolver 6/6.

## Public Provider Evidence

Fresh public captures under
`/private/tmp/coordinator-v220-paylocity-live` returned HTTP 200 for each tested
detail and board page. The adapter verified current complete inventory for:

| Company | Detail | Canonical tenant board | Result |
| --- | --- | --- | --- |
| Loveland Innovations | `4232544` | `1842d214-71f6-424b-92ed-555e85a52c30/Loveland-Innovations-LLC` | exact opening present |
| iClassPro | `4331044` | `7c2a1868-9c37-49da-820c-1c193bcd1fa6/iClassPro-Inc` | exact opening present |
| Resolute Road Hospitality | `4327097` | `e4cace93-beb5-455c-b64f-f694bee78b1d/Resolute-Road-Hospitality` | exact opening present |

Resolute's current provider detail changed the historical board slug from
`Braintree-Hospitality` to `Resolute-Road-Hospitality` while preserving the
same tenant. The bootstrap correctly follows current provider evidence rather
than trusting the historical slug.

Actabl detail `4284086` returned HTTP 200 but no longer exposed the required
detail bootstrap evidence. It was rejected instead of being treated as a live
opening. This is the expected closed/stale behavior.

## Focused Live And Replay

The clean Loveland run at
`/private/tmp/coordinator-v220-loveland-run1` completed and replayed 1/1 with no
fixture gap. It did not reproduce the Paylocity search candidate seen in `.219`,
so the new bootstrap path was not exercised by that complete live run. Its
0/1 result remains inconclusive for recall and cannot overwrite any cohort
score.

The provider-family contract is accepted because three independent current
provider pages and the deterministic end-to-end matrix use the same generic
code path. Overall discovery recall remains open because search must first
produce the detail lead.

## Remaining Identity Cluster

The actual Fresh100 source name `iClassPro - Class Management Software` does not
strictly match provider employer `iClassPro Inc`. This is not a Paylocity
bootstrap defect. It remains a separate display-name/alias evidence cluster;
the implementation deliberately does not strip the suffix from one benchmark
sample or weaken S7 company identity.

## Decision

Accept `.220` Paylocity detail bootstrap as a provider-family capability. Do
not claim Fresh100 recovery. Continue backend work on the next causal cluster,
with candidate production and source-company alias evidence analyzed
separately.
