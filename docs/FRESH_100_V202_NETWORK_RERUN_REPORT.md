# Fresh 100 `.202` Network Rerun Report

Run date: 2026-07-21

## Isolation

- Runtime commit: `ace84bd5c36f46ac80240685fb7e43eb5c1a05d5`
- Adapter version: `2026-07-21.202`
- Input: `samples/evaluation/live100_fresh_cohort_20260718.json`
- Input SHA-256: `fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`
- Run root: `/private/tmp/fresh100-v202-network-rerun-20260721-run3`
- Cold start: 100 pending, 0 restored, `--no-resume`
- Explicit cohort guard: `--limit 100 --require-full-cohort`
- Four company workers used isolated completion, checkpoint, evidence and
  snapshot roots.
- Code remained frozen throughout the live run.
- Blind holdouts v2 and v3 were neither opened nor executed.

LinkedIn public search and the CISA dataset endpoint both returned HTTP 200 in
the preflight. A prior accidental invocation omitted `--limit 100`, announced
only 30 pending records, and was interrupted before it could publish a score.
Its separate `run2` root was never resumed or included here.

## Live Results

| Metric | `.200` rerun | `.202` rerun | Delta |
| --- | ---: | ---: | ---: |
| Program raw Exact | 28/100 | 20/100 | -8 |
| Strictly accepted Exact | 24/100 | 20/100 | -4 |
| Verified website | 72/100 | 69/100 | -3 |
| Career page | 59/100 | 56/100 | -3 |
| Verified Job List | 57/100 | 52/100 | -5 |
| Retryable terminal | 27/100 | 33/100 | +6 |

The live phase completed all 100 records in 1,304.9 seconds. The terminal reason
counts were:

| Reason | Count |
| --- | ---: |
| Network timeout | 30 |
| Result identity mismatch | 12 |
| Opening discovery incomplete | 12 |
| Opening not found | 10 |
| Career page not found | 7 |
| Job board not found | 7 |
| Company time budget exhausted | 4 |
| No public openings | 2 |
| HTTP forbidden | 2 |
| Rate limited | 1 |
| Provider variant unsupported | 1 |
| Server error | 1 |

The preflight HTTP 200 responses did not predict stable end-to-end transport.
Twenty-seven of the 30 `NETWORK_TIMEOUT` terminals failed in S2 website
resolution. This run therefore provides no evidence that the earlier network
problem disappeared.

## Exact Audit

All 20 published opening URLs passed output validation and the S7 company,
provider, tenant, title and location assertion. No observed Exact crossed
company or tenant boundaries. The location gate also held every former `.200`
raw Exact that lacked location evidence: Sunbird Software, STEAMe, IMG and
Steampunk are now `RESULT_IDENTITY_MISMATCH`, not Exact.

Relative to `.200`, six previously strict Exact records were lost to current
transport failures and two new strict Exact records were recovered:

- Lost: B&D Industries Project Manager, Loveland Innovations, Milwaukee Tool,
  Stuller, TreeHouse Foods and iClassPro.
- Gained: Frost and Holland America Line.

The strict score therefore moved from 24 to 20. The larger raw-score change
from 28 to 20 also includes the intentional removal of the four former
location-unverified outputs; it must not be described as an eight-record recall
regression.

The identity gate additionally rejected wrong-location or insufficiently
verified candidates for WalkMe, Target Hospitality, Mayo Clinic, Vertiv, two
Arkema postings, Aramark and Cintas. In particular, the former Arkema
wrong-city result did not reappear.

## Replay Gate

The full bundle selected and exported all 100 records, but replay did not
start. Integrity validation rejected one Holland America Line record with
`scoped_stage_seed_ambiguous`: its scoped Job Board portfolio primary-detection
metadata is inconsistent. Counts therefore remain 100 selected, 100 exported,
0 replayed, with 1 boundary-invalid record. This is a replay infrastructure
defect, not a live cohort omission, and it is not waived.

## Artifact Digests

- `results.json`: `2c7beb0a5881b8c5c8172e441087cc8c95ad795d365e57f410aa97accfd66cd7`
- `trace.json`: `cca5ea4527e22904fb943306abd7dd6afddc23c651b2f4d32efa24cc3938c02f`
- `summary.json`: `69b9d1335539920d2b4316cabaed690f7dd053e06711b3a5707390b8a26effc9`
- Replay manifest: `6f4db54a2c00735623fe4b10aa9cd7155a333a8a1d47bb2b2f1c5a4fb151b48a`
- Read-only run archive:
  `artifacts/releases/fresh100-v202-network-rerun-20260721-run3.tar.zst`
- Archive SHA-256:
  `13035ca4403e2cc31d4acab987fef8a4849a0811bbc086b4efed30923a88e946`

The `.188`, `.199` and `.200` scores and artifacts remain unchanged. This is a
separate complete cold live diagnostic with a failed replay gate, not a release
acceptance result.
