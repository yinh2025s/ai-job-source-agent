# ADR-0036: Propagate Candidate Route Outcomes

Status: accepted

Date: 2026-07-29

## Context

Stage-v1 candidate discovery carries candidates as typed objects but carries
producer completion only in diagnostic dictionaries. Non-throwing producers
are labeled `success`, while search-source transport failures, deterministic
denials, budget exhaustion and true empty results can all end as
`no_valid_candidates`. S5 can subsequently report `not_run` after those routes
actually executed.

Trace cannot be the authority for retry or terminal semantics.

## Decision

Candidate producers and composite waves will return immutable typed outcomes.
The outcome distinguishes produced candidates, completed empty work,
producers with no route-applicable input, retryable failure, deterministic rejection, budget
exhaustion and source-local candidate rejection.

S5 consumes the typed wave outcome when no verified portfolio or stronger
Website/Career result exists. Diagnostic trace is serialized from the typed
outcome but cannot reconstruct or override it.

The contract does not authorize candidates. Provider adapters, hiring
relationship verification, tenant continuity, opening selection and S7 remain
unchanged.

External-blocked reporting additionally requires a serialized provider
identity whose hiring relationship is verified. A denial observed before that
boundary remains discovery-unresolved even when its concrete source reason is
retained.

## Consequences

- causal reports can distinguish candidate absence from unavailable sources;
- retry policy can use the actual producer outcome;
- an executed S5 route no longer appears as `not_run`;
- historical checkpoints and completions require an adapter-version boundary;
- some Fresh100 terminal reasons may change without increasing Exact recall.

## Non-Goals

- coordinator-v2 migration;
- new search backends or provider adapters;
- changing search ranking, filtering or query count;
- treating an unverified source denial as an externally blocked official
  provider;
- relaxing identity or URL publication gates.
