# v251 Hireology Provider Family - Phase C

## Scope

This phase implements one provider-family cluster across three independent
employers. It does not change plugin behavior, coordinator-v2, the LLM branch,
Fresh100 scoring or sealed holdouts.

Frozen provider-isolation input:
`/private/tmp/hireology-provider-isolation-input.json`

1. San Diego Padres - Executive Assistant to the President of Baseball
   Operations - San Diego, CA.
2. Mills Automotive Group / Classic Toyota of Henderson - General Sales
   Manager - Henderson, NC.
3. Tim Moran Hyundai - Parts Runner - Hemet, CA.

## Baseline

Frozen `.246` provider-isolation live reached the supplied Career roots for all
three records but published zero verified Hireology Job Lists and zero Exact
openings. The common production cause was the absence of a native adapter for
the public Hireology inventory contract.

RWC Group was retained only as a closed/expired diagnostic control. Its earlier
Pasco target is absent from the provider's complete current inventory and is
not counted as an expected recovery.

## Implementation

The native adapter:

- recognizes only safe HTTPS `careers.hireology.com/<tenant>` roots and numeric
  `/<tenant>/<id>/description` routes;
- identifies custom Career pages only from unique Hireology-owned API,
  canonical or asset evidence;
- reads the official v1 full inventory under a 1,000-record hard cap;
- uses exact-title v2 detail calls only to recover current organization,
  location, status and provider-published employer evidence;
- rebuilds candidate URLs on the verified inventory root, while retaining any
  child-tenant URL returned by Hireology as provenance;
- rejects credentialed URLs, non-standard ports, malformed paths, final-URL
  drift, duplicate IDs, invalid JSON and inconsistent provider records.

The central integration adds a strict replay-safe Hireology board policy. The
non-exhaustive S5 scheduler now stops unrelated provider search when an
official Career page has already produced a typed provider board; S6 remains
responsible for inventory and employer evidence, and S7 remains the final
publication gate.

Snapshot JSON is now parsed and redacted structurally before serialization.
This fixes a replay defect where the text sanitizer interpreted the ordinary
escaped HTML attribute `data-placeholder-token` as a credential and corrupted
the JSON tape.

## Iteration Evidence

The first live run exposed the missing replay-safe provider policy and failed
before business evaluation. After that policy was added, Padres and Tim Moran
recovered while Mills exposed two independent generic defects:

1. S5 repeated unrelated ATS search after already identifying the official
   Hireology board, starving S6.
2. v2 list pages were slow and the snapshot text sanitizer corrupted one large
   JSON response during replay.

These were fixed at the scheduler, provider API strategy and snapshot contract
boundaries. No company, domain, tenant or job ID was added to production code.

## Final Live And Replay

Accepted artifact root:
`/private/tmp/v251-hireology-v1-accepted`

| Company | Job List | Exact opening | Title/location | S7 |
| --- | --- | --- | --- | --- |
| San Diego Padres | `careers.hireology.com/sandiegopadres` | `/2813570/description` | exact / San Diego, CA | verified |
| Mills Automotive Group | `careers.hireology.com/millsautogroup` | `/2699065/description` | exact / Henderson, NC | verified |
| Tim Moran Hyundai | `careers.hireology.com/goschhyundai` | `/2724843/description` | exact / Hemet, CA | verified |

Final result:

- Website: 3/3
- Career: 3/3
- verified Job List: 3/3
- S7 Exact: 3/3
- automatic replay: 3/3
- wrong URL: 0
- wrong location: 0
- cross-company: 0
- cross-tenant: 0
- closed-opening publication: 0

The Mills inventory contained 356 current records and three exact-title
candidates. The selected record was the only exact Henderson location, and its
v2 detail named Classic Toyota of Henderson. Its canonical opening remains on
the verified parent inventory tenant `millsautogroup`; the child tenant is
retained only as provider provenance.

## Offline Gates

- Relevant integrated tests: 547 passed.
- Production provider benchmark: 25/25.
- Resolver benchmark: 6/6.
- Architecture validation: 47 native adapters, 0 issues.
- `git diff --check`: clean.

The full test suite was intentionally not rerun for this scoped provider,
scheduler and snapshot-sanitization change.

## Decision

The Hireology provider-family cluster is closed for these development records.
This focused success does not alter Fresh100 aggregate metrics and does not
constitute holdout validation. The next backend round must return to the
remaining causal ledger and apply the same three-company implementation gate.
