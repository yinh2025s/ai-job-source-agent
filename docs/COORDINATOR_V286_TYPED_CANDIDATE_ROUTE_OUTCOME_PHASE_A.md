# Coordinator `.286` Typed Candidate Route Outcome Phase A

Date: 2026-07-29

Decision: **qualify one execution-semantics contract**

## Problem

The `.283` Fresh100 causal ledger cannot reliably distinguish candidate absence
from candidate-source failure. Stage-v1 candidate discovery currently reports
every non-throwing producer as `success`, even when all underlying search
sources were blocked, timed out or exhausted their fetch budget. When no
verified portfolio is built, S5 may then fall back to a legacy `not_run`
execution even though Career-surface and Provider-search producers performed
network work.

This is not a recall heuristic and `S5` is not the root-cause cluster. The
shared defect is loss of producer outcome state across:

1. `CareerSearchResolver`;
2. `CandidateDiscoveryResult`;
3. `CompositeCandidateDiscovery`;
4. `JobBoardDiscoveryStage._from_candidate_portfolio`.

At least five official-host 403 records and five no-Career-input records
exercise the same state-loss path. More than fifty Provider-search source
executions in `.283` are affected by the same reporting contract.

## Contract

Every candidate producer returns an immutable typed outcome with one of these
states:

- `candidates_produced`: at least one bounded untrusted lead was emitted;
- `completed_empty`: applicable sources completed but emitted no lead;
- `not_applicable`: the producer had no route-applicable input; for example, a
  generic first-party URL belongs to the legacy Website/Career explorer rather
  than the direct ATS-URL producer;
- `source_failed`: applicable source execution failed and may be retried;
- `source_rejected`: applicable source execution was deterministically denied
  or returned a rejected response;
- `budget_exhausted`: the source/query plan stopped at a typed budget boundary;
- `candidate_rejected`: leads were observed but failed source-local structural
  verification.

`reason_code` and `retryable` are typed fields, not reconstructed from trace.
Trace remains diagnostic only. A producer that emits candidates may retain
diagnostic source failures in trace, but its aggregate outcome is
`candidates_produced`; downstream provider and identity gates still decide
whether those leads are usable.

Composite discovery returns one typed wave result containing:

- the bounded candidate pool;
- the aggregate wave outcome;
- per-producer typed outcomes;
- existing diagnostic trace.

The aggregate precedence for an empty wave is:

```text
budget_exhausted
> retryable source_failed
> source_rejected
> candidate_rejected
> completed_empty
> not_applicable
```

This precedence classifies why the route is incomplete. It does not authorize a
candidate or publish a URL.

## S5 Projection

When no provider portfolio survives:

- an actually executed candidate wave must not become `not_run`;
- budget exhaustion projects its existing budget reason;
- retryable source failure projects its typed network reason;
- deterministic source rejection retains the concrete reason, but does not
  become `EXTERNAL_BLOCKED` before a hiring/provider relationship exists;
- structurally observed but rejected provider leads project a provider or
  identity rejection;
- a completed empty plan projects `JOB_BOARD_NOT_FOUND`;
- only an all-`not_applicable` route set with no Career/External Apply input may
  remain `not_run`.

If the Website/Career legacy route produced stronger evidence, that result
remains authoritative and receives the candidate-route outcomes only as
diagnostic evidence.

## Safety Boundary

This phase must not:

- add a provider, company, domain, tenant, title or job-ID special case;
- change candidate ranking or search filtering;
- weaken provider, hiring relationship, tenant, title, location or S7 checks;
- publish a Job List or opening because a source completed;
- classify an unverified 403/challenge as `EXTERNAL_BLOCKED`;
- enable coordinator-v2, authenticated External Apply, the plugin or the LLM
  branch.

## Focused Acceptance

Snapshot-backed focused replay must cover at least three independent companies
for each changed terminal path:

1. source rejection: Sunwest Bank, City of Pharr and Benefis Health System;
2. executed-empty/not-run correction: Caesars Entertainment, Nisga'a Tek and
   Systematic Business Consulting;
3. retryable source failure/budget outcome where existing snapshots provide
   the typed evidence.

Acceptance requires:

- every applicable producer outcome is first-class typed data;
- executed routes never report S5 `not_run`;
- at least three independent records change to the correct reproducible causal
  terminal;
- no newly published Job List or opening URL;
- focused replay has zero mismatch and zero fixture gap;
- relevant unit tests, provider benchmark, resolver benchmark, architecture
  gate and `git diff --check` pass.

If fewer than three independent records change terminal semantics through this
one path, the cluster is rejected and the behavior change is rolled back.

## Rollback

Revert `.286` if typed outcomes are inferred downstream from trace, if one
producer failure hides a valid verified route, if deterministic empty results
become retryable without evidence, or if URL/identity authorization changes.
