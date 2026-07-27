# `.226` Official Candidate Failure Attribution Phase C

## Result

The code-frozen three-record cold run is preserved at
`/private/tmp/fresh3-v226-official-failure-20260723-run1`.

| Company | Withheld official evidence | S2 result |
| --- | --- | --- |
| City of Lubbock | verified `mylubbock.us` | unrelated `cityoflubbock.com` timeouts suppressed; `WEBSITE_NOT_RESOLVED` |
| North Dakota Information Technology | verified `ndit.nd.gov` | unrelated `kxnet.com` HTTP 403 suppressed; `WEBSITE_NOT_RESOLVED` |
| State of Montana | verified `mt.gov` evidence plus a current same-site failure | unrelated `state-of-montana.com` failures suppressed; current `mt.gov` timeout retained |

No Website, Career, Job Board or opening URL was published for any record.
Parent/group identity remained rejected.

## Contract Validation

- Lubbock recorded official site `mylubbock.us` and suppressed three retained
  failures from other sites.
- NDIT recorded official site `nd.gov` and suppressed four retained failures,
  including the previous `kxnet.com` 403.
- Montana recorded official site `mt.gov`, suppressed five unrelated failures,
  and retained the current timeout on `https://mt.gov/` because it belongs to
  the same registrable site.
- Existing direct preferred-site 403/timeout behavior remains unchanged when no
  withheld verified official candidate exists.

The resolver/discovery/evaluation/checkpoint scoped gate passes 233 tests.
Provider benchmark, resolver benchmark and architecture validation are run as
the final offline Phase C gate.

## Replay

The full scoped bundle exported and replayed all three records:

- 3 reproduced
- 0 mismatch
- 0 fixture gap
- 0 replayability drop
- complete result/trace/comparison coverage

## Evaluation Note

The independent candidate coordinator still runs after S2 and produces its own
`JOB_BOARD_NOT_FOUND` terminal when no provider route is authorized. Therefore
the aggregate projected Exact/Verified terminal counts do not change in this
phase. The corrected evidence is visible at the S2 boundary and in replay; the
work closes false transport attribution, not downstream discovery recall.

## Decision

Accept `.226`. The three-company failure-provenance cluster is closed. Do not
interpret this as acceptance of government or parent/group Websites, and do not
claim a Fresh100 recall increase. The next workstream returns to recurrence
mining for a general downstream relationship or provider-discovery contract.
