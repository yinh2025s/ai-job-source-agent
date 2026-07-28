# Fresh100 `.281` Current-Version Cold Measurement Gate

Date: 2026-07-29

## Decision

The user-authorized, code-frozen Fresh100 measurement completed 100/100 from
new state on commit `e38be3d`. Live improved from the `.278` baseline of 31
Exact openings to 34, and all 34 passed the serialized identity and URL safety
audit. The strict replay gate nevertheless failed:

- record integrity passed for 100/100 records;
- 99 outcomes reproduced;
- Brown and Caldwell changed from Exact to `OPENING_NOT_FOUND`;
- fixture gaps, replayability drops and budget recoveries were zero.

This is a failed current-version release gate. The run does not overwrite the
immutable `.278` result and does not authorize a sealed cohort.

## Frozen Input And Runtime

- Code commit: `e38be3d901ad589f56e41cd8ae9c00c54634ce0b`
- Product adapter: `2026-07-29.281`
- Input records and unique LinkedIn job IDs: 100 / 100
- Input SHA-256:
  `fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`
- Input source:
  `samples/evaluation/live100_fresh_cohort_20260718.json`
- Resume: disabled
- Prior checkpoint, completion, evidence and snapshot reuse: prohibited
- Candidate engine / search backend: `stage_v1` / `legacy`
- Workers: four bounded company workers in one process
- Code changes during live and replay: none
- Sealed v2/v3, plugin and LLM branch: not accessed

The manifest, exact command and local artifacts are preserved under:

`/private/tmp/fresh100-current-v281-cold-20260729-run1`

## Live Result

| Metric | `.278` | `.281` | Change |
| --- | ---: | ---: | ---: |
| Records completed | 100 | 100 | 0 |
| Website | 90 | 93 | +3 |
| Career page | 76 | 78 | +2 |
| Job List | 69 | 71 | +2 |
| S7 Exact opening | 31 | 34 | +3 |
| Pipeline partial | 47 | 46 | -1 |
| Pipeline failed | 22 | 20 | -2 |

Thirty `.278` Exact records remained Exact. Four records became Exact:

- WalkMe, DevOps Engineer;
- StatRad, DevOps Engineer;
- Salas O'Brien, Project Manager;
- Versana, UX Designer.

NYC Department of Social Services was the one lost Exact; this cold run had
official `nyc.gov` evidence but did not select a Website and ended
`WEBSITE_NOT_RESOLVED`.

The cohort has no terminal ground-truth annotation. Eligible Exact recall,
Exact precision against human truth, and the five product dispositions are
therefore `not_reportable`; pipeline status is not substituted for annotation.

## Exact Safety Audit

All 34 live Exact records passed the recorded-evidence audit:

- 34/34 HTTPS opening URLs with a host, no credentials and no non-standard
  port;
- 34/34 verified S7 identity assertions and hiring relationships;
- 34/34 consistent provider, tenant, Job Board and opening chains;
- 34/34 output URLs equal the asserted canonical openings;
- 34/34 title and location evidence present;
- zero wrong-location classifications;
- zero cross-company or cross-tenant publication.

There are 33 distinct opening URLs because two Arkema LinkedIn records
correctly resolve to the same Beaumont Human Resources Manager opening.

This audit is based on the captured official evidence and serialized S7
contract. It is not an independent post-run HTTP availability check.

## Strict Replay

| Replay classification | Records |
| --- | ---: |
| Reproduced | 99 |
| Outcome mismatch | 1 |
| Fixture gap | 0 |
| Budget recovery | 0 |
| Replayability drop | 0 |

Record integrity passed with 100 source results, 100 selected/exported records,
100 replay results, 100 traces and 100 comparisons. Three recorded
company-budget boundaries were projected and reproduced.

Brown and Caldwell was the only mismatch. Live UltiPro evidence represented
the location as `Address.City="Wailuku"` plus the object
`State.Name="Hawaii"`. Snapshot sanitization treated the object-valued
`State` field as a sensitive OAuth field and redacted it. Replay then fell
back to `LocalizedDescription="Maui, HI"`, failed the Wailuku location gate,
and changed the valid Exact opening to `OPENING_NOT_FOUND`.

Nine captured companies contain an object-valued redacted `State`, but only
Brown and Caldwell changes terminal outcome. This is a real replay sanitizer
defect, but currently a one-record mismatch rather than a three-recovery
cluster.

## Causal Failure Clusters

The 66 live non-Exact records were assigned to executable causal roots rather
than stage labels. Two clusters satisfy the repository threshold of one common
trigger, one production path, at least three independent companies and at
least three expected recoveries:

1. GovernmentJobs interactive/inventory fetch timeout:
   City of Lubbock, WICHITA COMPANY LIMITED and City of College Station.
   All have a verified tenant and unique declared official search form, then
   time out in `GovernmentJobsAdapter.list_jobs()` and its fallback inventory.
2. Career candidate probes consume the company deadline:
   Diamondback Energy, NDIT and ARUP Laboratories. Each enters Career discovery
   with substantial time and transport calls remaining, spends roughly 79
   seconds in serial candidate probes, then reaches the outer company deadline
   before reserved ATS/search routes can establish a deterministic terminal.

Large labels that do not authorize implementation:

- five official Career hosts repeatedly return HTTP 403; this is external
  refusal, not evidence of three Exact recoveries;
- nine companies reach generic JS pages with `transport_not_declared`, but
  their page protocols are unrelated;
- twelve companies expose complete official inventories without a
  title/location-compatible opening;
- the remaining portfolio, handoff, identity and provider-variant roots affect
  fewer than three independent companies or have no three-recovery evidence.

## Privacy

The raw capsule is not shareable. A credential-shape scan found one public
Google browser-key-shaped value serialized into three artifact files, with
duplicate occurrences in trace, completion and stage checkpoint output. The
value is not reproduced in this report. No release archive is created.

## Next Decision

No product code changes are made in this measurement phase. The next legal
Phase B may address the two qualified clusters in separate ownership:

1. GovernmentJobs adapter interaction and bounded retry;
2. Career candidate per-probe deadline/failure circuit with preserved
   provider-search reserve.

Each must run focused live and scoped replay for all three affected companies,
retain zero unsafe publication, and be reviewed before another full cohort.
The Brown replay mismatch remains recorded until a three-company replay
correctness cluster qualifies or the governance rule is explicitly changed.
