# Fresh100 `.212` Architecture Review

## Decision Context

The two permitted generic stabilization rounds are complete. `.210` fixed the
stored-provider identity projection defect. `.211` recovered zero of four
declared Exact openings and was reverted in `.212`. This review therefore does
not propose another local heuristic. It tests the two architecture questions
required by the goal: whether candidate discovery has actually removed the S2
dependency, and whether the remaining variance belongs to transport code or to
the network environment.

The review uses the immutable Fresh100 `.209` run, its replay artifacts, the
current `.212` source, and prior Fresh100 network-run reports. It does not open
or execute either sealed holdout and does not inspect or integrate the isolated
LLM branch.

## Findings

### The three routes are implemented, but not independently coordinated

The composition root registers External Apply, explicit Website/Career ATS,
Career-surface search and provider-targeted search producers. Product execution
is nevertheless staged and sequential:

```text
S2/S3/S4
  -> direct candidate wave
  -> optional legacy Website/Career attempt
  -> conditional search wave
  -> S5 provider and relationship verification
```

`CompositeCandidateDiscovery` iterates producers synchronously. Normal product
mode can skip the search wave after a verified direct or stored candidate. The
benchmark-only `evaluate_all_candidate_routes` flag evaluates every route, but
it does not make them concurrent and is not normal product scheduling.

S2 failure alone no longer prevents provider search: when S3 is `not_run`, S5
can still execute and S6 only requires a verified Job List. However:

- Website/Career discovery still requires the S2 website.
- A deterministic S3 failure globally prevents S5 before External Apply or
  provider candidates are evaluated.
- Candidate producers consume the S2-S4-mutated shared context rather than an
  immutable S1 input plus route-local evidence.
- The legacy `JobSourceAgent.discover()` API retains a website-first early
  return, while CLI, extension and library defaults do not have identical
  candidate semantics.

### External Apply exists downstream but was absent from the cohort input

Fresh100 contains `0/100` External Apply URLs. Public LinkedIn search-card input
did not expose them. The extension can capture a visible logged-in DOM handoff,
but ordinary CLI LinkedIn discovery does not perform the same detail-page
enrichment by default. Thus the highest-priority route had no opportunity to
participate in this benchmark; its zero coverage is an input-contract gap, not
an algorithmic failure rate.

### Provider search is neither exhaustive nor sufficiently diverse

`ProviderSearchCandidateDiscovery` asks the resolver with `exhaustive=False`
and stops after the first syntactically valid search lead. With the Fresh100
query cap of five, the fixed plan covers only:

1. one general title query;
2. Greenhouse;
3. Lever;
4. Ashby;
5. Workable.

Pinpoint, SmartRecruiters, Workday, Oracle and Eightfold queries are present in
the builder but unreachable under that cap. A first stale, wrong-region or
wrong-tenant result can also suppress later valid leads and the tenant-probe
fallback.

For the 30 non-Exact records represented by the 31-record "correct candidate
not produced" group (two postings share company identity), the provider search
executed 150 queries and observed 1,434 raw search results. It emitted zero
provider candidates. Twenty-six records then exhausted the tenant-probe attempt
limit, two found no verified tenant and two lacked a probe source. This is a
candidate-source architecture failure, not evidence that 1,434 URLs were
correct or that identity validation should be weakened.

### Network instability is material, but not sufficient as the explanation

The `.209` evidence supports three transport-related groups affecting at most
27 records:

| Group | Records | Evidence |
| --- | ---: | --- |
| Website transport/deadline | 18 | DNS, TLS, connect and read failures; most authoritative routes had no scheduled retry. |
| Correct opening route, transport failed | 5 | Four opening-query timeouts and one GovernmentJobs server error after a correct route existed. |
| Company wall-clock starvation | 4 | Company deadline expired while substantial request-count budget remained; search received no opportunity. |

Prior frozen-code network reruns moved Exact results between records and reached
24 Exact in `.207`, compared with 19 in `.209`. That proves transport variance
is important. It does not prove that a United States exit alone fixes the
system: current artifacts record no exit IP, ASN or country, and lower timeout
counts historically produced only small net Exact gains.

The defect is two-layered: an unstable network and code that amplifies latency
through zero-retry authoritative routes, one shared company deadline and
serial work that can starve later candidate sources.

## Proposed Architecture Migration

The next change should be an orchestration migration, not another provider or
company heuristic. It should preserve all provider adapters, S6 matching and S7
identity gates.

1. Freeze an immutable `CandidateDiscoveryInput` from S1.
2. Add a `CandidateDiscoveryCoordinator` with route-local budgets and traces.
3. Start External Apply and provider search from S1 evidence without waiting
   for S2; append Website/Career candidates when S4 evidence becomes available.
4. Make S3 failure route-local. It may suppress Website/Career evidence that
   depends on the rejected identity, but cannot erase an independent LinkedIn
   handoff or provider-published employer candidate. S7 remains the final hard
   gate.
5. Make provider search bounded-exhaustive: retain multiple results per query,
   distribute the fixed query budget across provider families, and remove the
   first-valid-lead stop. Ranking affects order only.
6. Give authoritative transport, provider search and opening inventory explicit
   reservations inside the company deadline so an early route cannot starve the
   others.
7. Unify CLI, extension and library execution on `PipelineApplication` and make
   External Apply enrichment capability explicit in the S1 trace.

This is a contract and orchestration migration. It exceeds the completed
two-round stabilization allowance and therefore requires an explicit decision
before behavior code changes.

## Acceptance Contract

Before implementation, freeze fixtures spanning at least three independent
companies for each accepted behavior cluster:

- S2 failure does not prevent three different provider candidates from reaching
  S6.
- An S3 route-specific identity rejection does not erase three independently
  valid External Apply/provider routes; unsafe final identity still fails S7.
- When the first search result is stale or wrong-tenant and a later result is
  correct, all three fixtures retain and evaluate the later result within the
  unchanged total candidate cap.
- The fixed query budget reaches every configured provider family over a
  deterministic schedule rather than always truncating the same tail.
- Route-local latency injection cannot consume another route's reservation.
- CLI, extension and library produce the same candidate pool for the same
  normalized S1 input; capability differences are explicit trace facts.
- Wrong URL, wrong location, cross-company and cross-tenant Exact remain zero.

Development verification uses only affected unit/contract tests and scoped
replay. One full offline gate runs after integration freeze. Live validation is
then serialized: first a focused development cohort, then a frozen-code
network A/B. Sealed holdouts remain untouched until the development and Frozen
100 regression gates pass.

## Network A/B Requirement

The network experiment must freeze code, config and the 27 transport-affected
development records. Current and provably United States exits each run at least
three times, first serially and then with the same bounded worker count. Every
run records exit IP/ASN/country, DNS resolver, connect/TLS/TTFB timings, retry
events and route-local budget consumption. Only a controlled four-cell result
(`old/new scheduler x current/US exit`) can separate environment recovery from
code recovery.

## Current Verdict

The `.212` stabilization line is offline-green but does not satisfy the product
goal. Continuing to add provider heuristics under the present scheduler is not
justified. The next defensible implementation is the coordinator migration
above, followed by controlled network A/B evidence. Until that migration is
explicitly authorized, sealed holdouts remain preserved and the goal remains
open.
