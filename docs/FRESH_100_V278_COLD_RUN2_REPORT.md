# Fresh100 `.278` Cold Live Run 2

Date: 2026-07-29

## Decision

The user-authorized historical `.278` rerun completed from a detached,
code-frozen worktree and entirely new runtime state. Live completed 100/100,
but strict replay failed. This run does not overwrite the immutable `.278`
run1 result and does not change the current `.281` measurement.

## Frozen Runtime

- Commit: `d86a021e228238531c0e7627eecf7cb996dd14f8`
- Adapter: `2026-07-28.278`
- Input: `samples/evaluation/live100_fresh_cohort_20260718.json`
- Input SHA-256:
  `fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`
- Records / unique LinkedIn job IDs: 100 / 100
- Resume and prior cache/completion/snapshot reuse: disabled
- Candidate engine / search backend: `stage_v1` / `legacy`
- Workers: 4
- Local artifact root:
  `/private/tmp/fresh100-v278-cold-20260729-run2`

The detached worktree remained clean at the frozen commit through live and
replay. Sealed cohorts, the plugin, coordinator-v2 and the LLM branch were not
accessed.

## Live Result

| Metric | `.278` run1 | `.278` run2 |
| --- | ---: | ---: |
| Records completed | 100 | 100 |
| Website | 90 | 91 |
| Career page | 76 | 78 |
| Job List | 69 | 70 |
| S7 Exact opening | 31 | 32 |
| Pipeline partial | 47 | 47 |
| Pipeline failed | 22 | 20 |

Run2 gained Diamondback Energy, Salas O'Brien and a second Versana posting,
while losing NYC Department of Social Services and Wolfe. This is network and
live-source variance on unchanged code, not a product improvement.

All 32 Exact outputs passed a serialized evidence audit:

- HTTPS URL with host, no credentials and no non-standard port;
- verified hiring relationship and provider relationship;
- continuous provider, tenant, board, opening and selection identity;
- output URL equal to the canonical selected opening after trailing-slash
  normalization;
- title and accepted location evidence;
- successful S7 result validation.

Unsafe, wrong-location, cross-company and cross-tenant publications were zero.
The cohort still has no terminal ground-truth annotation, so eligible recall
and human-label precision remain not reportable.

## Strict Replay

Record integrity passed with 100 source results, 100 selected/exported records,
100 replay results, 100 traces and 100 comparisons. Fixture gaps were zero.
The outcome gate failed:

| Classification | Records |
| --- | ---: |
| Reproduced | 95 |
| Budget recovery | 2 |
| Mismatch | 3 |
| Fixture gap | 0 |

Budget recoveries:

- North Dakota Information Technology;
- ARUP Laboratories.

Outcome mismatches:

- Brown and Caldwell: live Exact became `OPENING_NOT_FOUND` after object-valued
  UltiPro state evidence was redacted;
- ProMach: `CAREER_PAGE_NOT_FOUND` retained its reason but changed failed to
  partial;
- Systematic Business Consulting: the same failed-to-partial status drift.

The separate failure-only bundle also failed its outcome gate at 63
reproduced, two budget recoveries and two mismatches. The command's non-zero
exit was this gate failure, not an interrupted Python process.

## Privacy

The raw capsule is not shareable. A public Google browser-key-shaped value is
serialized in exactly three artifact files: one Job Board checkpoint, one
completion record and `trace.json`. No JWT-shaped value was found. No release
archive was created.

## Conclusion

The authorized cold live and strict replay measurement are complete. `.278`
does not pass release replay: 95/100 reproduced is below the required 100/100.
The result is evidence only and does not authorize a sealed cohort or rewrite
the current `.281` baseline.
