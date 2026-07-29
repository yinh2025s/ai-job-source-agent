# Coordinator `.286` Post-Measurement Causal Split

Date: 2026-07-29

## Scope

This read-only Phase A audit used the observed Fresh100 development artifacts,
the `.286` typed route-outcome evidence and existing non-sealed diagnostic
captures. It made no network request, product-code change or version change.
Sealed blind cohorts, authenticated extension work, coordinator-v2 and the LLM
branch remained untouched.

The implementation gate remains:

1. at least three independent companies;
2. one shared trigger;
3. one shared production code path; and
4. at least three expected evidence-terminal recoveries.

## Declared GET Is Not One Cluster

The four records previously grouped by a declared GET or interactive job-list
surface split into four different causes:

| Record | Executable cause | Shared recovery count |
| --- | --- | ---: |
| DSV | repeated result-card parser does not bind `result__info` location to an opaque numeric detail URL | 1 |
| Equifax | safe same-page pagination URL is rejected solely because it carries `#results` | 1 |
| System One | a multi-field form selects broad `term` instead of the title-specific `title` field and reaches the page cap | 1 |
| Home Depot | CWS configuration parsing rejects an optional reset before later valid static configuration | 1 |

These records share an entry shape, not a trigger or code path. They therefore
do not authorize a broad form, pagination or card-parser change.

Supporting controls do not increase any group to three recoveries:

- Canva shares Equifax's safe fragment pagination shape but is already Exact.
- Opstergo does not contain captured target-location evidence.
- StatRad already extracts its repeated cards and fails on a different identity
  path.
- WICHITA has no next-page link and later fails a separate employer identity
  contract.

## CWS / m-cloud Split

The Home Depot page contains one HTTPS m-cloud API, one organization, one
SmartPost organization, one safe detail path and a later valid static sort.
Before that final configuration it executes `CWS.jobs.sortby("")` and declares
an empty optional boost. `providers/cws.py::_page_config()` currently treats
the reset as invalid, returns no board and lets the page fall back to generic
inventory. This is a real parser defect, but it is a singleton.

Cleveland Clinic is not a second recovery case. Its page exposes a
`cws_opts` shell, but the captured pipeline follows a verified Workday board,
finds a matching title and rejects the opening for location continuity.
Northwell Health is a historical successful CWS/SmartPost control, not an
unresolved record. Existing non-sealed artifacts contain no third independent
CWS reset failure.

A future qualified CWS repair should model effective static configuration:
optional reset calls may clear optional state, while the final page state must
still contain exactly one safe API, organization and detail path. Conflicting
API, organization, filter or sort declarations must continue to fail closed.

## Other Apparent Large Clusters

| Apparent group | Companies / records | Causal result | Expected recovery |
| --- | ---: | --- | ---: |
| Search results filtered to zero | 18 / 19 | 596 raw results contained no observed correct ATS or official-domain candidate; relaxing filters would weaken company/tenant safety | 0 |
| Career transport dispatch budget | 4 / 4 | TLS failure, iCIMS relationship parsing, inventory no-match and no candidate are four different causes sharing one budget exit | at most 1 |
| Opening company budget | 3 / 3 | captures contain no target opening; Conrad is an explicit wrong-location negative control | 0 |

Necessary Ventures and Crawford Thomas use different unrecognized inventory
contracts. WENDEL contributes two records but only one independent company.
State of Montana, Aramark and Heritage remain separate singletons.

## Decision

No candidate satisfies the implementation gate, so Phase B is rejected. The
adapter remains `2026-07-29.286`; no product code, budget, filter, provider,
identity, title, location or S7 contract changes are authorized by this audit.

The next safe evidence step is to collect a new non-sealed diagnostic cohort
that can add two independent examples to one already-known singleton path.
That would be a new measurement authorization, not an automatic continuation
of the historical `.278` gate.
