# v258 Eightfold PCS X Inventory - Phase C

## Decision

Accept the native Eightfold PCS X provider contract. Do not claim that HP's
wider candidate-portfolio terminal is closed.

## Implementation

The existing Eightfold adapter now recognizes an exact `code#pcsx-data`
document whose config explicitly enables PCS X search. It calls only the
same-origin `/api/pcsx/search` endpoint using the shell domain and bounded
offset pagination.

The response contract requires:

- HTTP-success JSON envelope with either no error or the observed empty
  `{message, body}` error object;
- a nonnegative stable count and at most ten positions per page;
- unique numeric job IDs across pages;
- same-origin `/careers/job/<id>` details;
- no unsafe redirects or tenant substitution.

One unambiguous `efcustomTextOperatingcompany` value becomes immutable
opening-bound provider employer evidence. Legacy `smartApplyData` behavior and
non-production shell rejection are unchanged.

## Focused Live

Frozen input:
`/private/tmp/v258-eightfold-input.json`

SHA-256:
`17fadbfbe45a27c4090eef4e9cc7baaa4f66897faffa4c043dfa3e855bf35fb9`

Final-code artifacts:

- `/private/tmp/v258-eightfold-run3`
- `/private/tmp/v258-eightfold-hp-run4`

| Company | Native adapter result | Aggregate terminal |
| --- | --- | --- |
| Gordian | exact title, location and operating company | S7 Exact |
| Mayo Clinic | complete 40-record title-filtered inventory | `OPENING_NOT_FOUND` |
| HP | complete 3-record title-filtered inventory | `JOB_BOARD_PORTFOLIO_INCOMPLETE` |

HP's first authorized board attempt is a verified Eightfold no-match. The
aggregate remains incomplete only because live search produced unrelated,
unauthorized Oracle candidates after that official result. This is outside the
Eightfold adapter and remains an open scheduler cluster; `.258` does not
convert it to a false verified no-match.

All emitted opening safety errors are zero. Gordian's canonical opening is:

`https://fortive.eightfold.ai/careers/job/893395484171`

Every focused run built and passed its automatic same-version replay bundle.

## Offline Gates

- Eightfold adapter tests: 22 passed;
- related provider/board/pipeline tests: 211 passed;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 47 native adapters, 0 issues;
- scoped `git diff --check`: passed.

The full test suite was not run for this isolated adapter variant.

## Next Cluster

Replay location mutation satisfies the implementation gate with seven
companies across BambooHR, Paylocity and Hireology. CRG/Symmetrio intermediary
false positives remain at two strictly confirmed companies and therefore do
not yet qualify.
