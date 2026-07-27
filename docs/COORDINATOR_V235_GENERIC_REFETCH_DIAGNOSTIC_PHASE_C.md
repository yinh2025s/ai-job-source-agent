# Coordinator `.235` generic refetch diagnostic

## Purpose

Milwaukee Tool, Dechert and Crawford Thomas previously shared this historical
shape: S5 verified a first-party generic Job Board, S6 fetched the same page
again, and the second request failed. A process-local page lease was considered
as a possible stage-handoff fix.

Before adding that transient architecture, the three records were rerun with
frozen `.235` code and fresh state.

## Results

| Company | Current outcome |
| --- | --- |
| Milwaukee Tool | S7 Exact `R75328`, Brookfield |
| Dechert | verified complete generic inventory no-match |
| Crawford Thomas | Job List verified; inventory discovery incomplete |

All three recovered Website, Career and Job List. None reproduced the S6
same-URL transport failure, and all three replayed with identical outcomes.

Milwaukee's opening preserves the same company, generic board identity, title
`UX/UI Product Designer`, Brookfield location and canonical opening URL.
Dechert publishes no opening because its complete official inventory has no
matching title. Crawford remains unresolved because the current page does not
prove complete inventory.

Artifacts:

- `/private/tmp/fresh3-v235-generic-refetch-run1`
- `/private/tmp/fresh3-v235-generic-refetch-run1/replay-bundle`

## Decision

Do not implement a process-local `PageLease`. The current evidence no longer
meets the three-company causal-cluster threshold, while such a lease would add
checkpoint, worker and replay complexity. Retain Crawford as an inventory
completeness problem and replace Milwaukee and Dechert with their current
durable terminals.
