# Fresh100 `.211` Trusted Opening Search Scheduling Phase C

## Frozen Build

- Commit: `6756da422d6bfc7cb24042f8b58f786da54fb3d1`
- Adapter: `2026-07-21.211`
- Input SHA-256:
  `7173a021800e4db3564505dc260a9b4efe1a0db6947cfcb84eb2cdee5069359d`
- Source archive SHA-256:
  `93d06b164ed26ea4be251935f2a5331ce2322b361d041426c7479f04fc5b9cf3`
- Release archive SHA-256:
  `b645efaca5286f13032d0b8a316bdba16208fcac20f21225bf3c11c2210e0992`
- Run root: `/private/tmp/fresh4-v211-opening-scheduling-20260721-run1`

The run used new checkpoint, completion, evidence, snapshot and output roots;
zero records were restored. Code remained frozen throughout live and replay.

## Live Outcome

| Posting | `.209` terminal | `.211` terminal | Exact |
| --- | --- | --- | ---: |
| Sentar - Exploitation Analyst | Network timeout | Network timeout | No |
| WENDEL - Project Manager, Albany | Network timeout | Opening discovery incomplete | No |
| Crawford Thomas - Project Manager | Network timeout | Opening discovery incomplete | No |
| Crosby - HR Manager | Network timeout | Job Board portfolio incomplete | No |

All four retained verified first-party Websites, Career pages and Job Lists.
No opening URL was published, so wrong URL/company/location/tenant Exact counts
remain zero. Request order changed as designed, but none of the official routes
produced a verified target opening. Terminal-label changes are diagnostic only.

## Replay

- Full replay selected/exported/executed: 4/4/4.
- Reproduced: 4.
- Mismatch: 0.
- Fixture gap: 0.
- Record integrity: passed.

The earlier `.209` migration diagnostic selected all 24 timeout-bearing records
because reason filtering is record-wide; all 24 reproduced with zero mismatch
or fixture gap. It is not used as recall evidence.

## Decision

The Phase A acceptance threshold required at least three independent live
recoveries or equivalent verified opening evidence. The observed recovery is
0/4. The cluster is rejected: the shared scheduling path explained timeout
presentation but did not explain missing official opening evidence.

`.212` reverts the scheduling behavior. No company/domain/job exception is
added, no safety gate is relaxed, and no third generic architecture repair is
started under the current goal. The next action is one rollback-focused local
test pass followed by a single offline integration gate.
