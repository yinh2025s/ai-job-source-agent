# `.221` LinkedIn Input Canonicalization Phase C

## Scope

This phase closes only the three-company worker-contract cluster frozen in
`docs/COORDINATOR_V220_LINKEDIN_INPUT_CANONICALIZATION_PHASE_A.md`. It does not
claim that website transport or overall Fresh100 recall is complete.

The focused live run used a new checkpoint, completion, evidence, snapshot and
replay root at:

`/private/tmp/fresh3-v221-linkedin-input-20260722-run1`

The three inputs were copied from the frozen Fresh100 input without changing
company, title, location, LinkedIn job URL or LinkedIn company URL.

## Live Results

| Company | Previous `.220` outcome | `.221` focused outcome |
| --- | --- | --- |
| Investigative Case Management powered by Tapestrii | `batch_worker_contract_failed` | typed `NETWORK_TIMEOUT` at website resolution |
| University of Oklahoma | `batch_worker_contract_failed` | typed `NETWORK_TIMEOUT` at website resolution |
| Hays + Sons | `batch_worker_contract_failed` | S7-verified Exact |

No record raised an uncaught coordinator input exception. All three emitted a
normal stage result and a replayable snapshot boundary. The two transport
failures remain unresolved and retryable; they are not counted as product
recoveries.

## Exact Identity Audit

Hays + Sons returned:

- source title: `Project Manager - Bloomington Full Time`
- source location: `Bloomington, IN`
- company website: `https://www.haysandsons.com`
- hiring entity: `Hays + Sons`
- canonical board: `https://haysandsonscareers.com`
- tenant: `url:https://haysandsonscareers.com`
- opening: `https://haysandsonscareers.com/job/bloomington-in-2-project-manager-bloomington`

S7 returned `verified` with no failure or conflicting fields. The selected
opening title equals the source title. The provider page did not publish a
separate structured location value, so location continuity was established by
the `bloomington-in` opening URL qualifier against the source location. No
wrong URL, company, tenant or location was observed.

## Replay Gate

The same-version scoped replay selected, exported and compared all three
records:

- record integrity: passed, 3/3
- reproduced: 3
- mismatch: 0
- fixture gap: 0
- replayability drop: 0
- missing snapshot boundary: 0

The two network failures replayed as the same typed `NETWORK_TIMEOUT` outcome;
Hays + Sons replayed the same Exact URL and verified identity chain.

## Decision

The LinkedIn input canonicalization cluster is closed. The adapter now keeps
strict coordinator validation while preventing malformed optional provenance
from escaping the stage and worker boundaries.

Overall Fresh100 recall is unchanged until a later code-frozen 100-record run.
The next Phase A must select a different causal cluster shared by at least three
companies. Stage labels such as S2 or `opening_discovery_incomplete` are not by
themselves sufficient cluster definitions.
