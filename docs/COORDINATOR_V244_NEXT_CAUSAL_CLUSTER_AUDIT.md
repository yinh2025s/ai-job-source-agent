# v244 Next Causal Cluster Audit

## Scope

This read-only Phase A audit starts from the reconciled Fresh100 development
ledger:

`/private/tmp/fresh100-v244-current-causal-ledger.json`

The ledger contains 100 records:

- 37 Exact
- 12 Verified No Match
- 1 External Blocked
- 50 unresolved

Three independent workstreams audited candidate production and relationship,
S6 inventory/search, and transport/budget causes. They made no code changes,
used no network and did not inspect sealed blind v2/v3.

## Decision

No remaining group satisfies all implementation requirements:

1. at least three independent companies;
2. one shared trigger;
3. one shared production code path;
4. observed evidence for a correct candidate or official bypass;
5. one deterministic change expected to recover at least three companies.

Phase B is therefore not authorized. Provider filtering, hiring relationship,
tenant, location, pagination and form safety gates remain unchanged.

## Candidate Production And Relationship

American Fabrication and NextPlay both execute provider search with no
acceptable candidate, but neither has an observed correct ATS candidate.
NextPlay is an intermediary whose client employer is not disclosed.

Prophetic has a real Ashby opening, but the run selected an unrelated Canadian
company website. This is a one-company identity collision, not evidence that
the search URL filter should be relaxed.

Crosby and OneApp both end with incomplete portfolios, but their inputs differ:
Crosby lacks a typed source portfolio while OneApp combines an official
register-interest surface with an unauthorized tenant probe.

STRIKE and Vertiv both lack hiring-relationship continuity, but STRIKE selected
the wrong company identity while Vertiv has an Oracle candidate found only by
untrusted search. They cannot share one safe relationship promotion.

Aramark has a deterministic S7 checkpoint continuity recovery, Jushi has a
multi-hop producer/checkpoint issue, and Mayo Clinic has an Eightfold
relationship issue. Each is a separate one-company code path.

## S6 Inventory And Search

Four records have strong evidence for a deterministic single-record recovery:

| Record | Root cause | Expected recovery |
| --- | --- | ---: |
| DSV `4434690342` | declared GET result-card projection | +1 |
| StatRad `4440260052` | first-party card location binding | +1 |
| Equifax `4439438787` | safe pagination canonicalization | +1 |
| Aramark `4432355373` | S7 checkpoint identity continuity | +1 |

They are four different production paths. Home Depot is a negative control for
DSV because its response generated zero target candidates. System One and
Conrad Consulting have no captured correct target opening; incomplete
inventory must remain Partial.

WENDEL contributes two records but one independent company. Sentar, Tyler and
Cretex expose different form contracts. A broad form relaxation is rejected.

## Transport And Budget

Pitch Aeronautics, Systematic Business Consulting and Wichita share
`FETCH_BUDGET_EXHAUSTED` in `career_discovery.budget_controller`. The `.243`
code-frozen reproduction confirms the common exit, but the independent S5
search route produced zero valid candidates for all three. A budget-only change
has an expected product recovery of 0/3.

Other groups remain below the company threshold:

- opening company budget: Adapture and Heritage;
- provider search deadline before execution: RLB and ProMach;
- S4 transport dispatch budget: FOTOMILL and Nisga'a Tek;
- verified Job List transport: B&D and State of Montana, with different
  observed failures;
- Diamondback, City of Lubbock and City of Sioux Falls: separate singletons.

Increasing global budgets or retry counts is not authorized.

## Implication

The current deterministic architecture has reached an evidence boundary on
this development cohort. Continuing under the three-company/nonzero-recovery
rule requires one of:

1. collect a new non-sealed diagnostic cohort to find a third example for a
   shared production contract;
2. explicitly authorize singleton implementation, accepting the overfitting
   risk;
3. authorize an architecture experiment such as a browser-backed dynamic
   inventory route, while preserving all S7 identity gates.

Coordinator-v2 remains proposed. Extension/Chrome work, authenticated External
Apply parity, LLM work and sealed blind cohorts remain frozen.
