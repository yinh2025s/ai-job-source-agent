# Fresh100 `.278` Cold Live Run 4

Date: 2026-07-29

## Decision

The user-authorized historical `.278` measurement completed from the frozen
detached worktree and entirely new runtime roots. Live completed 100/100, but
strict replay failed. This run is independent measurement evidence and does
not overwrite `.278` runs 1-3 or the authoritative current `.283` result.

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
  `/private/tmp/fresh100-v278-cold-20260729-run4`

The run used `--limit 100 --require-full-cohort --no-resume` with new
checkpoint, completion, evidence, snapshot, failure-replay and full-replay
directories. Startup reported 100 pending, zero restored and zero retryable
resubmissions. The detached worktree remained clean. Sealed cohorts, the
plugin, coordinator-v2 and the LLM branch were not accessed.

## Live Result

| Metric | `.278` run1 | `.278` run2 | `.278` run3 | `.278` run4 |
| --- | ---: | ---: | ---: | ---: |
| Records completed | 100 | 100 | 100 | 100 |
| Website | 90 | 91 | 92 | 91 |
| Career page | 76 | 78 | 79 | 77 |
| Job List | 69 | 70 | 71 | 70 |
| S7 Exact opening | 31 | 32 | 32 | 33 |
| Pipeline partial | 46 | 47 | 49 | 47 |
| Pipeline failed | 23 | 20 | 19 | 20 |

All 33 live Exact outputs passed the serialized evidence audit:

- public HTTPS URL without credentials or a non-standard port;
- verified hiring and provider relationships;
- continuous provider, tenant, board, opening and selection identity;
- selected canonical opening equal to the published URL after strict identity
  canonicalization;
- target title and accepted exact, overlap, region or URL-qualified location
  evidence;
- successful S7 validation.

Unsafe URLs, wrong locations, cross-company and cross-tenant publications were
zero. The cohort has no terminal ground-truth annotation, so eligible recall
and formal human-label precision remain not reportable.

## Strict Replay

Full replay integrity passed with 100 source results, 100 selected and exported
records, 100 replay results, 100 traces and 100 comparisons. No record was
dropped and fixture gaps were zero.

| Classification | Records |
| --- | ---: |
| Reproduced | 94 |
| Budget recovery | 3 |
| Mismatch | 3 |
| Fixture gap | 0 |

Budget recoveries:

- North Dakota Information Technology;
- ARUP Laboratories;
- HP.

Each live record stopped at the company budget boundary. Latency-free replay
advanced to the corresponding `CAREER_PAGE_NOT_FOUND` semantic result.

Outcome mismatches:

- SDS International remained failed but changed from
  `COMPANY_TIME_BUDGET_EXHAUSTED` to `FETCH_BUDGET_EXHAUSTED`;
- Brown and Caldwell changed from Exact to `OPENING_NOT_FOUND`;
- Systematic Business Consulting kept `CAREER_PAGE_NOT_FOUND` but changed from
  failed to partial.

The non-zero batch exit was the automatic failure-bundle outcome gate. The
live process did not crash or stop early. Two Python 3.14
`HTTPResponse` finalizer warnings were ignored by the runtime and did not
affect the 100-record integrity checks.

## Privacy

The final scan covered 937,988,704 serialized bytes:

- valid JWT capability/time-claim values: 0;
- AWS `AKIA`/`ASIA` access-key shapes: 0;
- Google browser-key shapes: 10 matches in 3 files.

The Google-shaped value is the same class of public page value previously
observed in checkpoint, completion and trace serialization. The raw capsule
therefore remains local and no release archive is created.

## Conclusion

The authorized `.278` run4 cold live and strict replay are complete. Live URL
safety passed at 33/33 Exact, while the replay release gate failed at 94/100
reproduced. No product behavior, configuration, adapter version or
authoritative Fresh100 score changed.
