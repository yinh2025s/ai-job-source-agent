# ADR-0030: Coordinate Independent Candidate Discovery Routes

- Status: proposed; opt-in prototype frozen, product migration not approved
- Date: 2026-07-21

## Context

ADR-0025 introduced External Apply, provider-targeted search and Website/Career
candidate sources. The normal product scheduler still evaluates them as staged
waves inside S5, can stop after a direct candidate, and globally stops S5 after
a deterministic S3 failure. Provider search is also first-hit rather than
bounded-exhaustive. The resulting system has three producer classes but no
independent route coordinator.

Fresh100 confirms that this is not merely terminology. Its anonymous public
input contains no External Apply observation. A later authenticated input-
parity gate observed 18/24 visible off-site Apply controls and six Easy Apply
controls, but LinkedIn exposed all 18 off-site controls as buttons without a DOM
target URL. This establishes a separate browser-input capability gap and does
not authorize this coordinator proposal. For 30 non-Exact records in the candidate-not-
produced group, provider search issued 150 fixed queries, observed 1,434 raw
SERP results and emitted zero provider candidates. The five-query schedule
always reaches general, Greenhouse, Lever, Ashby and Workable while excluding
the same later provider families. Details are recorded in
`docs/FRESH_100_V212_ARCHITECTURE_REVIEW.md`.

The two permitted stabilization repairs have already completed, and the second
recovered 0/4 declared Exact openings before being reverted. This proposal is a
new orchestration contract, not a third local heuristic. An opt-in logical
prototype exists for evaluation, but the product remains on `stage_v1`.
Physical route concurrency and any product migration remain separately gated
by route-local snapshot and checkpoint evidence scopes and explicit approval.

## Decision

### Responsibilities

Introduce a thin `CandidateDiscoveryCoordinator`. It owns only:

- immutable S1 candidate-discovery input;
- route-local applicability, suppression, failure and budget state;
- bounded execution of the three route producers;
- deterministic, provenance-preserving candidate merge.

It does not identify a provider, authorize a tenant or hiring relationship,
read opening inventory, select an opening or approve an Exact URL. Those remain
S5 provider/relationship, S6 inventory/selection and S7 identity duties.

### Immutable Input

The coordinator consumes a `CandidateDiscoveryInput` frozen from S1 source
evidence. It contains source company, target title/location, LinkedIn posting
and company URLs, and External Apply observation state. It must distinguish:

- `observed`: a sanitized public External Apply URL was captured;
- `observed_absent`: the capable input adapter inspected the detail evidence
  and found no External Apply target;
- `not_observed`: the input path did not have detail-observation capability.

The coordinator may later receive a `WebsiteCareerRouteInput` containing the
verified Website/Career URLs and their evidence lineage. It must not reconstruct
S1 input from S2-S4-mutated context or treat a trace string as evidence.

### Route Contract

The only route identities remain:

```text
external_apply
provider_search
website_career
```

Every route returns an immutable result with one status:

```text
completed
not_applicable
suppressed
budget_exhausted
failed
```

`completed` may contain zero candidates. `not_applicable` means the required
input capability is conclusively absent. `suppressed` requires typed,
scope-matching rejected evidence. Budget and failure states retain retryability
and cannot disappear because another route succeeds. Route status is diagnostic
and cannot directly determine the company terminal result.

Each result records bounded candidates, elapsed time, request/query usage,
truncation and a privacy-safe trace. Unversioned trace fields cannot establish
provider, relationship, inventory or success.

### Route-Local Identity Suppression

S3 no longer acts as a company-global pre-S5 gate. A deterministic S3 rejection
may suppress only the Website/Career route and only when:

1. it rejects the exact Website/Career identity used by that route;
2. the rejected URL and evidence scope match the route input;
3. each suppressed candidate depends on that scope.

S3 `failed` or `not_run` cannot remove External Apply or prevent provider search.
Provider-published employer evidence still enters the existing S5 relationship
contract. If suppression scope cannot be proven, the candidate remains an
untrusted lead and existing S5/S7 gates reject it when continuity is absent.

### Deterministic Merge

There is one merge before S5 registry verification. Candidate URLs are
canonicalized and deduplicated while retaining every route provenance. A route
with at least one candidate receives one reservation before remaining slots are
filled by existing candidate priority, result rank, canonical URL and source
kind. The global candidate cap remains 12.

Different URLs that adapters canonicalize to the same provider board are merged
only in S5. The board retains all applicable route evidence. Search rank decides
inspection order only; it never establishes truth.

Provider search becomes bounded-exhaustive within its fixed global budget:

- retain more than one safe result per query;
- distribute query opportunities deterministically across provider families;
- remove the first-valid-lead stop;
- retain later candidates when an earlier lead is stale, wrong-region or wrong-
  tenant;
- keep every current URL, provider, tenant and relationship validation.

### Logical Independence And Physical Concurrency

The first implementation phase establishes logical route independence and
deterministic merge inside the canonical S5 evidence scope. It may execute
serially while contract and replay behavior are proven, but it must evaluate all
applicable routes with route reservations and may not claim physical parallelism.

Physical overlap of provider discovery with S2-S4 is a separate required phase
before coordinator-v2 can become the product default. It starts only after
capture/checkpoint infrastructure can assign route-local producer scopes without
losing the canonical seven-stage boundary. Runtime futures, threads, fetchers and
partial candidate pools are never serialized. Final merge remains deterministic
regardless of completion order.

This phased rule prevents a staged implementation from being marketed as
parallel while also preventing nondeterministic request scheduling from
silently breaking replay.

### Budget Contract

External Apply, provider search, Website/Career and opening inventory receive
explicit reservations within the company wall-clock budget. An earlier route
cannot borrow another route's reservation until that route is completed,
inapplicable or explicitly releases it. Request, query and candidate limits
remain independently bounded.

The accepted serial implementation reserves `provider_search_reserve_seconds`
from S4 at the retrying fetch boundary. Before each S4 dispatch, effective
remaining time is `global_remaining - provider_reserve`; a non-positive value
returns retryable `FETCH_BUDGET_EXHAUSTED`, finalizes the existing S4 evidence
scope and checkpoint, and releases the reservation when the runner enters S5.
The live runner's existing post-S5 opening reserve remains independent. This
does not create another process, stage, snapshot scope or persisted runtime
object.

Provider search first spends one Bing RSS request per scheduled provider query.
If every accepted bucket is still empty, composition reserves at most two
additional DuckDuckGo HTML requests for deterministic empty-bucket rescue.
Rescue results remain untrusted and pass the same ATS-host, provider, tenant,
relationship, inventory and S7 gates.

Deadline ownership is a typed fetch terminal, not an error-message inference.
`COMPANY_TIME_BUDGET_EXHAUSTED` and `FETCH_BUDGET_EXHAUSTED` are persisted by
snapshot capture; outcome-tape replay switches `remaining_fetch_seconds()` to
exhausted after consuming either terminal so later request scheduling matches
live execution. Ordinary network timeouts remain `NETWORK_TIMEOUT`.

Lower network latency can improve outcomes but cannot substitute for this
scheduler contract. A controlled current-exit/United-States-exit A/B remains a
separate release experiment.

## Version And Persistence

This migration changes deterministic execution semantics.

1. Run configuration upgrades from `1.5` through `1.6` to `1.7`; it requires
   `candidate_discovery_engine` with `stage_v1` or `coordinator_v2`, and `1.7`
   versions the provider-search reservation.
2. Adapter version advances from `.212`; old checkpoints therefore miss safely.
3. Old run payloads retain their original digest and may execute only with their
   compatible staged implementation. They are never silently reserialized as
   coordinator-v2.
4. A missing, unknown or extra schema-1.6 engine field fails closed.
5. Public result schema remains unchanged because no output field changes.
6. `JobBoardPortfolio` remains schema 2.0 unless implementation proves a new
   persisted field is necessary.
7. Snapshot record and outcome-tape schemas remain unchanged while all
   coordinator requests stay in one canonical stage scope. Strict extra,
   missing and unconsumed request checks remain active.
8. Contract/checkpoint schema upgrades only if a new context update or
   `StageExecution` payload is introduced. Partial, runtime-only or unfinished
   candidate state is not checkpointable.
9. A future route-local capture format requires its own schema decision and
   fail-closed bundle migration before physical concurrency is enabled.

Old tapes cannot be forced through coordinator-v2 with an execution-fingerprint
override. New and old checkpoints, snapshots, evidence, completion stores and
evaluation roots remain isolated.

## Compatibility And Rollback

The binary retains staged `stage_v1` during rollout. Engine selection is part of
the run configuration digest, never an untracked environment switch. Rollback
restores the `.212` binary/config and reads only its isolated roots; coordinator
state is not down-converted.

A rejected coordinator experiment is reverted as one behavior unit. Provider
adapters and S6/S7 contracts remain independently reusable.

## Implementation Workstreams

After authorization, work is split with non-overlapping ownership:

| Workstream | Ownership |
| --- | --- |
| Coordinator contract and merge | `job_source_agent/candidate_discovery_coordinator.py`, `tests/test_candidate_discovery_coordinator.py` |
| Provider search diversity | `job_source_agent/provider_search_discovery.py`, `job_source_agent/career_search.py`, their tests |
| Producer input adapters | `job_source_agent/direct_candidate_discovery.py`, `job_source_agent/career_surface_discovery.py`, their tests |
| Main integration | `job_source_agent/stages/discovery.py`, `job_source_agent/contracts.py`, `job_source_agent/composition.py`, shared schemas and versions |
| Product entry consistency | `job_source_agent/cli.py`, `job_source_agent/extension_bridge.py`, entry tests |
| Governance | architecture/ADR, implementation plan, changelog and phase reports |

Main owns shared contracts, composition, S5 integration, review and final gates.
Subtasks run only ownership-local tests. Full live benchmarks and shared network
resources remain serial.

## Acceptance

Contract fixtures span at least three independent companies and providers for
each accepted cluster:

- S2 failure still allows provider candidates to reach S6.
- S3 rejection does not erase independent External Apply/provider routes, while
  S7 still rejects unsafe final identities.
- A correct later search result survives an incorrect first result.
- Every configured provider family receives deterministic opportunities under
  the fixed query budget.
- Candidate cap and merge retain at least one result per productive route.
- Route-local latency cannot consume another route's reservation.
- CLI, extension and library produce the same candidate set for equivalent S1
  evidence, with observation-capability differences explicit.
- Wrong URL, wrong location, cross-company and cross-tenant Exact remain zero.

Checkpoint/replay tests prove distinct run digests, old-checkpoint rejection,
complete portfolio round-trip, no partial-state persistence, exact tape
consumption and unchanged route-local Exact attribution requirements.

Development runs only affected unit/contract tests and scoped replay. The main
line runs one full offline integration gate after freeze, then a serialized
focused live and the controlled network A/B. Sealed holdouts remain unobserved
until development and Frozen100 regression gates pass.

## Consequences

The migration is larger than another adapter or retry rule, but directly
addresses the observed orchestration failure. It increases bounded work because
productive early routes no longer suppress independent evidence. Route budgets,
candidate caps and deterministic merge contain that cost.

The prototype implements logical route independence, deterministic
route-reserved merge, bounded-exhaustive provider search and persistence
isolation behind an explicit flag. It does not authorize a product-default
migration, LLM integration, relaxed identity gates, physical route concurrency
without route-local evidence scopes, or consumption of sealed holdouts.
