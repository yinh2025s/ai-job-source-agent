# Fresh 100 `.206` Network Rerun Report

Run date: 2026-07-21

## Isolation

- Runtime commit: `11b12f0e4fe4ddaf4ba628463c04fc09b56d64b9`
- Adapter version: `2026-07-21.206`
- Input: `samples/evaluation/live100_fresh_cohort_20260718.json`
- Input SHA-256: `fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`
- Run root: `/private/tmp/fresh100-v206-network-rerun-20260721-run1`
- Cold start: 100 pending, 0 restored, `--no-resume`
- Explicit cohort guard: `--limit 100 --require-full-cohort`
- Four company workers used isolated completion, checkpoint, evidence and
  snapshot roots.
- Code remained frozen throughout the live run.
- Blind holdouts v2 and v3 were neither opened nor executed.

LinkedIn public search and the CISA dataset endpoint both returned HTTP 200 in
the preflight. This rerun is a separate diagnostic on the already observed
Fresh100 development cohort. It does not replace `.188`, `.202`, or any blind
holdout result.

## Live Results

| Metric | `.202` rerun | `.206` rerun | Delta |
| --- | ---: | ---: | ---: |
| Strict S7 Exact | 20/100 | 23/100 | +3 |
| Verified website | 69/100 | 75/100 | +6 |
| Career page | 56/100 | 60/100 | +4 |
| Verified Job List | 52/100 | 55/100 | +3 |
| Retryable terminal | 33/100 | 27/100 | -6 |
| S2 network-timeout record | 30/100 | 25/100 | -5 |

The live and boundary-finalization phase completed in 1,572.5 seconds. Twenty-
five records observed an S2 network timeout. Slant CRM still reached a verified
Ashby Exact through the provider route, so 24 of those timeout records remained
terminal `NETWORK_TIMEOUT` outcomes.

| Terminal reason | Count |
| --- | ---: |
| Network timeout | 24 |
| Opening discovery incomplete | 14 |
| Opening not found | 9 |
| Result identity mismatch | 9 |
| Job board not found | 7 |
| HTTP forbidden | 4 |
| No public openings | 3 |
| Career page not found | 3 |
| Company time budget exhausted | 3 |
| Provider variant unsupported | 1 |

Transport improved relative to `.202`, but it did not become stable. The rerun
recovered six prior non-Exact records and lost three prior Exact records to
current S2 transport:

- Recovered: Loveland Innovations, iClassPro, Indica Labs, Stuller, B&D
  Industries Project Manager, and TreeHouse Foods.
- Lost: Frost, ProMach, and BWXT.

The net Exact gain is therefore three. This evidence supports treating network
failure as a material source of score variance, not as the explanation for all
remaining product gaps.

## Exact Audit

All 23 published openings have `identity_assertion.verdict=verified`, an empty
failure-code set, a verified hiring relationship, consistent provider/tenant/
board/opening identity, and an accepted title/location selection. Observed
wrong URL, cross-company, cross-tenant, and wrong-location counts are all zero.

| Company | LinkedIn title | Provider | Location evidence | Opening |
| --- | --- | --- | --- | --- |
| Loveland Innovations | DevOps Engineer | Paylocity | overlap | `4232544` |
| iClassPro | DevOps Engineer | Paylocity | overlap | `4331044` |
| Indica Labs | DevOps Engineer | BambooHR | exact | `185` |
| Aperia | DevOps Engineer | Greenhouse | region | `5187679007` |
| Vectra AI | DevOps Engineer | Greenhouse | exact | `7811650` |
| Wolfe | DevOps Engineer | Pinpoint | exact | `cacd0cbd-eee6-4326-99f1-b73ab432e303` |
| Versana | DevOps Engineer - Raleigh | Lever | exact | `c886fa3f-af09-4793-95c9-769ed3bafb51` |
| Ivo | DevOps Engineer | Ashby | overlap | `b54a2f89-9910-4cab-b0e8-89a51891c096` |
| Knock | DevOps Engineer | Ashby | overlap | `924e2fb4-4073-473a-90a4-dda00a565df9` |
| Stuller | Information Security Analyst | SaaSHR | overlap | `990125377` |
| EnsoData | User Experience (UX) Designer | Workable | region | `9A338B5C7A` |
| Holland America Line | UX Designer | Oracle HCM | overlap | `13555` |
| Versana | UX Designer | Lever | exact | `fb567b68-8fc6-4f7b-a8ca-bd99233dbd12` |
| Lab37 | UI/UX Designer | Greenhouse | exact | `8579139002` |
| Slant CRM | Product Designer | Ashby | exact | `1d5a754e-593d-445e-a605-89e674eb077f` |
| Salas O'Brien | Project Manager | UltiPro | exact | `70990d1a-1b25-410b-b250-fad9bc60a425` |
| Hays + Sons | Project Manager - Bloomington Full Time | first party | URL qualifier | `project-manager-bloomington` |
| B&D Industries | Project Manager | ApplicantStack | exact | `a2kyz9jvk52p` |
| Northern Clearing | Project Manager | ApplicantPro | overlap | `4141722` |
| TreeHouse Foods | Human Resources Manager | Workday | overlap | `R30530` |
| B&D Industries | Human Resources Manager | ApplicantStack | exact | `a2kyz9jgk1wd` |
| Alaska Commercial Company | Manager, Human Resources | CATS | exact | `16818337` |
| Resolute Road Hospitality | Human Resources Manager | Paylocity | overlap | `4327097` |

The S7 gate continued to reject Sunbird, Target Hospitality, Mayo Clinic, IMG,
Steampunk, both Arkema records, Aramark, and Cintas rather than publishing an
identity- or location-unsafe URL.

## Replay Gate

The failure bundle selected, exported, and replayed all 77 non-Exact records.
It classified 74 as reproduced and three company-budget outcomes as explicit
budget recovery, with zero mismatch and zero fixture gap.

The full bundle selected, exported, and replayed 100/100 records. It classified
97 as reproduced and Diamondback Energy, NDIT, and ARUP Laboratories as the
same explicit budget recovery. Record integrity passed with zero omission,
zero dropped record, zero mismatch, and zero fixture gap. This closes the
`.202` full-replay infrastructure failure on the current `.206` version.

## Artifact Digests

- `results.json`: `f1d3f6795c4149a1c215c43a75a7a6ee1de605e19086a6c9f3e9c619b9dea682`
- `trace.json`: `1f43e666bd335afc102e881e6e23facc52ad19058e87d000880f6a8ab5f4c8ea`
- `summary.json`: `ef2761ff9ec0d7f71252e009a8b632a9e2c7c26c58c07ba5f839675e660efacd`
- Replay manifest: `6e8c6c10dad2a394434b68cf3dd9a56e2babd68670349d7f88850c1a27c8ba80`
- Read-only run archive:
  `artifacts/releases/fresh100-v206-network-rerun-20260721-run1.tar.zst`
- Archive SHA-256:
  `14b73e8fce7e34d77a30829a8654402682b48241eb0261300c7e5cd94ce8248f`

The next implementation remains the evidence-backed multi-route cluster, not
another transport-specific heuristic: first-party generic and embedded typed
ATS routes must retain route-local hiring evidence through S6, and only a
route-local S7-verified Exact may win.
