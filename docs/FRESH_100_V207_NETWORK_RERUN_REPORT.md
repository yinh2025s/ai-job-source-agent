# Fresh 100 `.207` Network Rerun Report

Run date: 2026-07-21

## Isolation

- Runtime commit: `0f9ac95d60d93e29344738ba0bc0c0d9f2c43118`
- Adapter version: `2026-07-21.207`
- Input: `samples/evaluation/live100_fresh_cohort_20260718.json`
- Input SHA-256: `fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`
- Run root: `/private/tmp/fresh100-v207-network-rerun-20260721-run1`
- Cold start: 100 pending, 0 restored, `--no-resume`
- Explicit cohort guard: `--limit 100 --require-full-cohort`
- Four company workers used new completion, checkpoint, evidence and snapshot
  roots. The runtime source was exported from the frozen commit.
- Blind holdouts v2/v3 and the isolated LLM branch were not opened or run.

This is another run of the already observed Fresh100 development cohort. It
does not replace `.188`, either `.206` network run, or a blind holdout result.

## Live Results

| Metric | `.206` run 1 | `.206` run 2 | `.207` run | Best `.206` delta |
| --- | ---: | ---: | ---: | ---: |
| Strict S7 Exact | 23 | 22 | 24 | +1 |
| Verified website | 75 | 75 | 79 | +4 |
| Career page | 60 | 58 | 62 | +2 |
| Verified Job List | 55 | 56 | 60 | +4 |
| Records observing `NETWORK_TIMEOUT` | 25 | 26 | 20 | -5 |
| Terminal `NETWORK_TIMEOUT` | 24 | 22 | 16 | -6 |

The live phase completed all 100 records in 1,640.4 seconds. Pipeline statuses
were 24 success, 38 partial and 38 failed. Terminal reason codes were:

| Terminal reason | Count |
| --- | ---: |
| Network timeout | 16 |
| Opening discovery incomplete | 11 |
| Opening not found | 10 |
| Result identity mismatch | 9 |
| Job Board not found | 6 |
| Career page not found | 5 |
| HTTP forbidden | 5 |
| No public openings | 4 |
| Company time budget exhausted | 2 |
| Job Board portfolio incomplete | 2 |
| Website not resolved | 1 |
| DNS failed | 1 |
| Server error | 1 |

Compared with `.206` run 2, four records became Exact: Frost, NYC Department
of Social Services, Resolute Road Hospitality and iClassPro. Knock and
Prophetic lost Exact. Compared with `.206` run 1, BWXT, Frost, NYC Department
of Social Services and ProMach became Exact, while Indica Labs, Knock and
Loveland Innovations lost Exact. The lower timeout count therefore helped, but
the small net gain and continuing record churn do not establish a stable
network baseline.

## Exact Audit

All 24 published openings have `verdict=verified`, empty failure codes, a
verified hiring relationship, a relationship-verified provider identity, and
matching provider, tenant and canonical board across provider, opening and
selection evidence. Title and location validation passed for every Exact.
EnsoData, BWXT, Hays + Sons and Alaska Commercial Company differ from their
identity-canonical opening only by a trailing slash. Observed wrong URL,
cross-company, cross-tenant and wrong-location counts are zero.

## `.207` Cluster Verdict

The route-outcome machinery ran, but the frozen three-company cluster did not
close:

- OneApp retained an authorized first-party generic route and a typed Ashby
  route. The Ashby tenant belongs to a different same-name company and has no
  target opening; the expected Pinpoint candidate was not produced. The final
  result correctly stayed `JOB_BOARD_PORTFOLIO_INCOMPLETE`.
- Crosby retained an authorized first-party generic route and an unauthorized
  same-name Ashby route. S6 rejected the Ashby identity and kept the result
  incomplete instead of publishing a false no-match.
- The Home Depot reached its verified first-party Job List but the public
  inventory remained incomplete.

This proves that route-local evidence prevents unsafe outcome domination, but
it restores zero Exact openings in the declared cluster. It must not be called
cluster closure. The prior causal grouping mixed route-coexistence with the
separate prerequisite of producing the correct typed provider candidate.

## Replay Gate

The non-Exact failure bundle replay passed 76/76 as 74 reproduced plus two
explicit budget recoveries, with zero mismatch and zero fixture gap.

The full bundle selected and exported 100/100 records but stopped before
execution on one `scoped_stage_seed_ambiguous` preflight error for NYC
Department of Social Services. S5 recorded canonical generic board
`https://cityjobs.nyc.gov/jobs`, while the successful S6 search projection made
the top-level display URL `https://cityjobs.nyc.gov/jobs?q=DEVOPS+ENGINEER`.
The replay planner treated those as contradictory primary detection metadata
even though the verified S7 provider/opening/selection chain remains bound to
the canonical `/jobs` board. Thus full replay is 0/100 executed and this run is
not a release acceptance result.

The next change must first add a generic replay contract proving that an S6
query projection cannot overwrite or contradict the S5 canonical board. It
must preserve rejection for real provider, tenant or board conflicts. Because
that changes replay behavior, it requires a new version and a new frozen live
acceptance run; this `.207` capture remains immutable.

## Artifact Digests

- `results.json`: `78b899ffd95627a6fdf6b7351421bbd5a7f8b9c203ed11903d5736a73fa2c119`
- `trace.json`: `21cd47ffda0c5687a2efe5a697680f54c5341a8da6b4f12d227a7634c8c829fa`
- `summary.json`: `f23e690ae2be65e9f0417479f6caa6262817f480bc93e1c8e870bbd1702fbeb9`
- Failure replay manifest:
  `22dfb2833e86b14113c2414b2e7a52e089af65e557b687387ad9bf49d0dbfe1b`
- Full replay failed manifest:
  `b66ab76b1b2ac4ee88d4b625c780037a1a6b4fbfe6432db55f4c0fc37c0939c0`
- Read-only run archive:
  `artifacts/releases/fresh100-v207-network-rerun-20260721-run1.tar.zst`
- Archive SHA-256:
  `1ef6f9860799cf991acf525b08ddafe5b866b8ff5b4354d30fc8febdc569706b`
