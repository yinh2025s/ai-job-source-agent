# Coordinator `.235` known-domain diagnostic

## Purpose

Four companies had previously been grouped as a transport cluster because the
correct `.com` Website candidate existed but the cold run ended in TLS or read
timeouts. The current code was frozen and all five affected postings were run
with fresh checkpoint, snapshot, evidence and completion roots.

## Results

| Company / posting | Current outcome |
| --- | --- |
| Steampunk / UI/UX Designer | verified complete iCIMS inventory no-match |
| B&D Industries / Project Manager | S7 Exact ApplicantStack opening |
| Vertiv / Project Manager | Oracle board found; relationship identity remains ambiguous |
| Northern Clearing / Project Manager | S7 Exact ApplicantPro opening |
| B&D Industries / Human Resources Manager | retryable ApplicantStack provider fetch failure |

All five records recovered Website, Career and Job List. The old shared
transport label therefore does not reproduce and is not an implementation
cluster.

The two Exact openings retain a complete identity chain:

- B&D Industries: ApplicantStack tenant `banddindustries`, title
  `Project Manager`, location `Santa Teresa, NM`.
- Northern Clearing: ApplicantPro tenant `northernclearing`, title
  `Project Manager`, location overlapping `Buffalo, NY`.

No wrong URL, wrong location, cross-company or cross-tenant publication was
observed.

## Replay

The first four records replay 4/4 with identical outcomes. The fifth record
exposes a retry taxonomy mismatch: live reports `PROVIDER_FETCH_FAILED`, while
offline replay reaches `COMPANY_TIME_BUDGET_EXHAUSTED`. Both are retryable and
publish no opening, but this is still deterministic replay debt and prevents a
5/5 closure claim.

A repository-wide non-sealed artifact audit found the same drift only for City
of Lubbock. Two independent companies are insufficient for a common replay
implementation cluster, so outcome-tape and budget semantics remain unchanged
until a third company reproduces the same trigger.

Artifacts:

- `/private/tmp/fresh5-v235-known-domain-run1`
- `/private/tmp/fresh5-v235-known-domain-run1/replay-bundle-first4`
- `/private/tmp/fresh5-v235-known-domain-run1/replay-bundle`

## Decision

Do not add a generic transport heuristic or larger timeout for these companies.
Replace the three newly durable terminals in the development projection and
split the two remaining records by their actual causes.
