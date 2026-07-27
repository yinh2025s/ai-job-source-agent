# Coordinator `.235` Causal Ledger And Timeout Reproduction Phase C

## Decision

Two historical stage-label clusters were rejected after current-version
reproduction:

1. `generic_inventory + single_page_unbounded` combined three unrelated
   integration families: Consider, Eightfold and Bullhorn OSCP.
2. Five historical `NETWORK_TIMEOUT` records produced zero timeout terminals
   when rerun on the frozen `.235` backend.

No product heuristic, provider special case, retry increase or budget increase
is authorized by this phase. The adapter version remains `.235`.

## Causal Ledger Contract

`job_source_agent.causal_evidence` and
`scripts/build_causal_failure_ledger.py` now build an ordered, evidence-backed
development projection keyed by LinkedIn job ID. The contract:

- freezes cohort membership from the first artifact or an explicit cohort;
- rejects duplicate and out-of-cohort IDs;
- prevents later retryable observations from downgrading audited terminals;
- accepts focused terminal upgrades only through an explicit review manifest;
- separates primary cause, contributing causes and bypass opportunities;
- requires current-version reproduction and a reviewed nonzero batch-recovery
  expectation before a cluster can authorize implementation.

Ledger schema `1.1` no longer groups undeclared generic inventory by a common
fallback stop reason. Until a provider family is identified, each integration
origin is classified as insufficient causal evidence.

## Seven-Record Reproduction

Immutable run root:

```text
/private/tmp/fresh7-v235-causal-reproduction-run1
```

The run used fresh checkpoint, completion, snapshot, evidence and replay roots,
`stage_v1`, one worker and the frozen `.235` production configuration.

Results:

- 7/7 completed and 7/7 replayed.
- 3/7 verified Job Lists.
- 0/7 Exact.
- Kelly reached a reviewed verified inventory no-match observation.
- Prophetic and WICHITA produced search results but no valid provider candidate.
- Diamondback exhausted its Career search deadline before query execution.
- Necessary Ventures, HP and Crawford Thomas reached Job Lists but exposed
  Consider, Eightfold and Bullhorn OSCP respectively.

The previous four-company generic inventory cluster is therefore invalid. The
three current records do not share an integration family or a batch repair.

## Five-Record Timeout Reproduction

Immutable run root:

```text
/private/tmp/fresh5-v235-network-timeout-reproduction-run1
```

Results:

- 5/5 completed and 5/5 replayed.
- 0/5 reproduced `NETWORK_TIMEOUT`.
- Lorum and Team Royal recovered audited S7 Exact openings.
- American Fabrication and NextPlay reached Career evidence but not a verified
  Job Board.
- iClassPro ended at Website identity resolution.

The historical timeout label is environmental observation, not a common
production code defect.

## Exact Identity Audit

| Company | Title | Location | Provider / tenant | Opening |
| --- | --- | --- | --- | --- |
| Lorum | DevOps Engineer | New York, New York | generic first-party / `url:https://www.lorum.com/careers` | `https://www.lorum.com/open-roles/devops-engineer-34274` |
| Team Royal | Project Manager | Lafayette, Louisiana | BambooHR / `royal` | `https://royal.bamboohr.com/careers/77` |

Both records have verified company, hiring relationship, provider, tenant,
title, location and opening continuity. They are accepted into the development
projection. The projection becomes:

```text
36 Exact
10 Verified No Match
1 External Blocked
53 unresolved
```

This is code-frozen recovery evidence, not a claimed behavior improvement.

## Remaining Stage-Label Reproductions

Two additional clean `.235` runs completed after the projection update:

```text
/private/tmp/fresh4-v235-career-not-found-reproduction-run1
/private/tmp/fresh4-v235-job-board-not-found-reproduction-run1
```

Both replayed 4/4.

The first run split the historical four-record Career label:

- Splashlight bypassed Career discovery and reached an Ashby board candidate,
  but the candidate had no hiring relationship and remained fail closed.
- Caesars received non-career responses from generated paths.
- Pitch Aeronautics had a retryable TLS EOF on its Careers subdomain.
- Systematic Business Consulting received 404 path responses plus a separate
  Careers-subdomain TLS EOF.

The second run split the historical Job Board label:

- Hawaiian Electric's two postings share one employer and an HTTP-forbidden
  path, so they count as one independent company.
- CHAMP and Fabric reach Career evidence but remain separate Job Board
  discovery cases.

Neither stage label is a three-company executable cause.

## Search Source Quality

Across the reproduced unresolved records, Bing RSS frequently reports 8–10
results while ignoring quoted terms and `site:` constraints. Snapshot review
shows dictionaries, Wikipedia, consumer sites and unrelated ATS tenants.
DuckDuckGo returns a challenge and Bing HTML returns zero parsed results.

The unused Pinpoint and SmartRecruiters tenant candidates for five affected
companies were checked directly. Pinpoint returned 404 and SmartRecruiters
redirected to its public root, producing zero recoverable boards. Reordering
the current tenant-probe scheduler therefore has zero expected batch recovery.
A new reliable search backend is a separate product/external-service decision,
not authorization to weaken URL or relationship filters.

## Provider-Family Audit

Three read-only, non-sealed development-artifact audits checked the unidentified
inventory integrations:

| Family | Independent companies | Evidence | Decision |
| --- | ---: | --- | --- |
| Consider | 1 | Necessary Ventures custom CNAME and `fixedBoard` / `Powered by Consider` markers | below gate |
| Eightfold PCS X | 2 | HP and Mayo Clinic `pcsx-data` plus `ef-*` assets | below gate |
| Bullhorn OSCP | 1 | Crawford Thomas official `bullhorn-oscp` iframe | below gate |

The provider registry and page-aware adapter contracts can host all three
families. Consider and Bullhorn have no adapter; Eightfold exists but does not
yet implement the shared PCS X inventory variant. None reaches the required
three independent companies, so no provider implementation is authorized from
this cohort.

## Gates

- Causal ledger tests: 15/15.
- Seven-record live replay: 7/7.
- Five-record live replay: 5/5.
- Career-label reproduction replay: 4/4.
- Job-Board-label reproduction replay: 4/4.
- Exact URL identity audit: 2/2.
- `git diff --check`: clean.
- Sealed blind v2/v3, plugin work and the isolated LLM branch were not touched.
