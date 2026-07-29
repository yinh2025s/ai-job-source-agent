# Fresh100 `.278` Cold Live Run 3

Date: 2026-07-29

## Decision

The user-authorized historical `.278` measurement completed from the frozen
detached worktree and entirely new runtime roots. Live completed 100/100, but
strict replay failed. This run is independent evidence and does not overwrite
`.278` run1, `.278` run2, or the authoritative current `.283` result.

## Frozen Runtime

- Commit: `d86a021e228238531c0e7627eecf7cb996dd14f8`
- Adapter: `2026-07-28.278`
- Input: `samples/evaluation/live100_fresh_cohort_20260718.json`
- Input SHA-256:
  `fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`
- Records / unique LinkedIn job IDs: 100 / 100
- Candidate engine / search backend: `stage_v1` / `legacy`
- Workers: 4
- Company / website budgets: 120 / 25 seconds
- Fetch timeout / retries: 8 seconds / 1
- Local artifact root:
  `/private/tmp/fresh100-v278-cold-20260729-run3`

The run used `--limit 100 --require-full-cohort --no-resume` with new
checkpoint, completion, evidence, snapshot, failure replay and full replay
directories. The detached worktree remained clean. Sealed cohorts, the plugin,
coordinator-v2 and the LLM branch were not accessed.

## Live Result

| Metric | `.278` run1 | `.278` run2 | `.278` run3 |
| --- | ---: | ---: | ---: |
| Records completed | 100 | 100 | 100 |
| Website | 90 | 91 | 92 |
| Career page | 76 | 78 | 79 |
| Job List | 69 | 70 | 71 |
| S7 Exact opening | 31 | 32 | 32 |
| Pipeline partial | 46 | 47 | 49 |
| Pipeline failed | 23 | 20 | 19 |

The unchanged historical code again varied with public network and source
state. Run3 is not a product improvement and cannot be combined with focused
or current-version measurements.

All 32 Exact outputs passed the serialized evidence audit:

- public HTTPS URL without credentials or a non-standard port;
- verified hiring and provider relationships;
- continuous provider, tenant, board, opening and selection identity;
- selected canonical opening equal to the published URL;
- accepted title and location evidence;
- successful S7 validation.

Hays + Sons uses the accepted `url_qualifier` location evidence carried by its
opening URL and title. Unsafe, wrong-location, cross-company and cross-tenant
publications were zero. The cohort has no terminal ground-truth annotation, so
eligible recall and formal human-label precision remain not reportable.

## Strict Replay

Full replay record integrity passed with 100 source results, 100 exported
records, 100 replay results, 100 traces and 100 comparisons. Fixture gaps were
zero.

| Classification | Records |
| --- | ---: |
| Reproduced | 95 |
| Budget recovery | 3 |
| Mismatch | 2 |
| Fixture gap | 0 |

Budget recoveries:

- North Dakota Information Technology;
- ARUP Laboratories;
- HP.

Each live record ended at the captured Career-stage company budget boundary;
latency-free replay advanced to the corresponding semantic
`CAREER_PAGE_NOT_FOUND` result.

Outcome mismatches:

- Brown and Caldwell: live Exact became `OPENING_NOT_FOUND` after the
  object-valued UltiPro state evidence was sanitized;
- Systematic Business Consulting: the Career failure reason remained
  `CAREER_PAGE_NOT_FOUND`, but pipeline status changed from failed to partial.

The separate 68-record failure bundle produced 64 reproduced, three budget
recoveries, one mismatch and zero fixture gaps. The command's non-zero exit was
the replay outcome gate, not an interrupted Python process.

## Privacy

No JWT-shaped capability value or AWS access-key-shaped value was found after
scanning approximately 996 MB of text artifacts. The raw capsule is still not
shareable: one public Google browser-key-shaped value is serialized in exactly
three files, a Job Board checkpoint, one completion record and `trace.json`.
No release archive was created.

## Conclusion

The authorized `.278` run3 cold live and strict replay are complete. The live
URL safety gate passed, but the release replay gate failed at 95/100
reproduced. No product code, configuration, version or authoritative score was
changed.
