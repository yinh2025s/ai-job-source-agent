# Fresh 100 `.188` Cold-Start Generalization Report

Run date: 2026-07-20
Cohort observation date: 2026-07-18
Source commit: `ed4c9343ec382387542d7b917050acbc04096dda`
Frozen tag: `frozen100-v188`

## Isolation And Preservation

- The original frozen 100 result remains `69/100`; this run does not update that score.
- The source commit and tag did not change during live execution or replay.
- The fresh cohort contains 100 distinct LinkedIn job IDs from 95 companies and has zero
  job-ID overlap with the original frozen cohort.
- The run started with new checkpoint, snapshot, company-evidence, completion and output
  directories under `/private/tmp/fresh100-v188-cold-20260720-run1`.
- `restored=0` and `pending=100`; no frozen-cohort cache or completion was resumed.

## Results

| Metric | Result |
| --- | ---: |
| Program raw Exact | 12/100 |
| Audited correct Exact | 11/100 |
| Raw Exact precision after audit | 11/12 (91.7%) |
| Eligible Exact recall | 11/90 (12.2%) |
| Verified website | 47/100 |
| Career page | 42/100 |
| Verified Job List | 38/100 |
| Wrong opening URL | 1 |
| Cross-company false positive | 0 |
| Cross-tenant false positive | 0 |

Eligible records exclude nine `VERIFIED_NOT_FOUND` records and one
`INPUT_IDENTITY_INVALID` record. No record had sufficient evidence for
`EXTERNAL_BLOCKED` in this cold run.

## Terminal Classification

| Terminal | Count |
| --- | ---: |
| EXACT | 11 |
| VERIFIED_NOT_FOUND | 9 |
| EXTERNAL_BLOCKED | 0 |
| INPUT_IDENTITY_INVALID | 1 |
| SYSTEM_GAP | 79 |

The paired closure matrix classifies every record:
`artifacts/evaluations/fresh100-v188-cold-20260720-run1/closure-matrix.csv`.

## Exact Audit

| Company | Provider / tenant | Title and location audit | Verdict |
| --- | --- | --- | --- |
| Aperia | Greenhouse / `aperiasolutions` | DevOps Engineer; Omaha, NE | pass |
| Wolfe, LLC | Pinpoint / `wolfe` | DevOps Engineer; Pittsburgh, PA | pass |
| Sunbird Software | JazzHR / `sunbirdsoftwareinc` | Cyber Security Analyst; Sioux Falls, SD; first-party snapshot confirms both | pass |
| STEAMe | JazzHR / `steamellc` | Product Designer; Chicago, IL; first-party snapshot confirms both | pass |
| EnsoData | Workable / `ensodata` | UX Designer; Madison, WI | pass |
| Lab37 | Greenhouse / `lab37` | UI/UX Designer; Pittsburgh, PA | pass |
| Steampunk, Inc. | iCIMS / `careers-steampunk.icims.com` | UI/UX Designer; McLean, VA | pass |
| BWXT | first-party SuccessFactors surface | Project Manager; Idaho Falls, ID | pass |
| TreeHouse Foods | Workday / `treehouse/TreeHouseCareers` | Human Resources Manager; Green Bay, WI | pass |
| Alaska Commercial Company | CATS / portal `100910` | Manager, Human Resources; Anchorage, AK | pass |
| Resolute Road Hospitality | Paylocity / Braintree tenant | Human Resources Manager; Spokane, WA | pass |
| Arkema | first-party SuccessFactors surface | LinkedIn requires Beaumont, TX; selected URL is Clear Lake, TX | **fail** |

Arkema is therefore not counted as audited Exact. It remains a same-company and same-tenant
candidate, but it is the wrong opening for the requested location and is classified as
`SYSTEM_GAP`.

## Replay Gate

The required 100/100 replay gate did not pass. Results were:

- 97 records reproduced exactly.
- 1 outcome mismatch: ProMach retained `CAREER_PAGE_NOT_FOUND`, but changed from live
  `failed` to replay `partial` because replay added a `NO_PUBLIC_OPENINGS` terminal.
- 1 tape divergence: the second B&D Industries record left an unconsumed
  `GET https://www.bdindustries.com/` request. The two same-company postings expose a
  shared-request/cache versus per-record tape boundary defect.
- 1 unreplayable record: Ken Garff Automotive Group raised an uncaught
  `http.client.IncompleteRead` in the S2 worker before an S2 snapshot boundary was recorded.
- Fixture gaps reported by completed replay comparisons: 0.

The batch-level replay manifests and all per-record bundles are preserved with the run.
The honest gate result is `97 reproduced / 1 mismatch / 2 replay-integrity gaps`, not
100/100.

## System-Gap Clusters

| Cluster | Impact | General repair direction |
| --- | ---: | --- |
| Cold S2 fetch failure or timeout | 48 | Catch the complete transport exception family, use bounded retry/backoff, and ensure all three candidate routes can proceed without making S2 a practical single point of failure. |
| S6 opening discovery incomplete | 14 | Execute declared first-party search contracts and native inventory adapters to a complete/filtered terminal; do not stop at a Job List page. |
| S5 Job Board not found | 8 | Continue one bounded navigation/search hop from verified Career pages and strengthen provider handoff extraction without company mappings. |
| S7 identity mismatch | 4 | Preserve hard identity gates, but improve correct-candidate ranking and location-aware selection; Arkema demonstrates that title-only acceptance is unsafe. |
| Career/website discovery | 3 | Improve first-party website and Career identity resolution while retaining conservative rejection. |
| Unsupported provider variant | 1 | Add a generic Oracle Recruiting Cloud site adapter contract, not a Vertiv special case. |
| Worker/snapshot integrity | 1 | Convert `IncompleteRead` into a typed retryable fetch result and always finalize stage snapshot lineage before parent recovery. |
| Replay determinism | 2 | Isolate same-company cache/tape ownership by record and make terminal pipeline status deterministic between live and replay. |

The 48-record cold S2 cluster is the dominant generalization failure. Several of these
companies had exact or verified-board evidence in the earlier July 18 development run, so
the decline cannot be explained as closed jobs alone; cold-start discovery and transport
stability are materially weaker than the resumed path.

## Decision

`SYSTEM_GAP` is non-zero, so no implementation was changed after this benchmark. The next
phase must start from a new version, fix clusters generically, and rerun affected samples
plus the original frozen cohort without rewriting either frozen score.
