# v258 Eightfold PCS X Inventory - Phase A

## Cluster

Three independent employers expose the same public Eightfold PCS X variant:

| Company | Board | Target |
| --- | --- | --- |
| HP | `https://apply.hp.com/careers` | Product Designer; Vancouver, WA |
| Mayo Clinic | `https://careers.mayoclinic.org/careers` | Information Security Analyst - IS-Mod; Rochester, MN |
| Gordian | `https://fortive.eightfold.ai/careers` | Product Marketing Manager; Greenville, SC |

Each shell contains a hidden `code#pcsx-data` JSON document and versioned
`ef-*` / `pcsxPwa` assets, but no legacy `code#smartApplyData`. The current
Eightfold adapter follows the same production path and returns
`PROVIDER_VARIANT_UNSUPPORTED`.

HP and Mayo were frozen as a below-threshold two-company family in `.235`.
Gordian's zero-overlap v257 capture supplies the third company.

## Public Contract

The PCS X bundle declares a same-origin GET endpoint:

```text
/api/pcsx/search
  ?domain=<pcsx-data.domain>
  &query=<target title>
  &location=<target location>
  &start=<offset>
```

The JSON envelope contains `data.count`, `data.positions` and filter metadata.
Positions contain numeric IDs, names, locations, same-origin
`/careers/job/<id>` paths and optional operating-company evidence.

Current public checks demonstrate nonzero batch terminal recovery:

- Gordian returns the exact target in the first page, with Greenville and
  provider-published operating company `Gordian`;
- HP returns a complete three-record title-filtered inventory with no exact
  `Product Designer`;
- Mayo returns 40 title-filtered records over four pages with no exact
  `Information Security Analyst - IS-Mod`.

The expected outcomes are one S7 Exact and two evidence-backed verified
inventory no-matches. This is three recovered records, not three guessed URLs.

## Implementation Boundary

Only the Eightfold provider adapter, its fixtures/tests and adapter version may
change:

- parse `pcsx-data` only from the exact hidden code element;
- require a valid HTTPS board, safe DNS-style domain and explicit PCS X config;
- construct only same-origin `/api/pcsx/search` requests;
- require a valid JSON envelope, stable domain, count and bounded pagination;
- accept only numeric IDs and same-origin `/careers/job/<id>` paths;
- preserve provider-published operating-company evidence;
- mark no-match authoritative only after complete title-filtered inventory;
- stop early on an exact-title candidate without claiming full completeness;
- reject cross-origin redirects, cross-tenant details, malformed counts,
  duplicate IDs, unsafe paths, missing domain and incomplete pagination.

No company, domain, job ID or title special case is allowed. Legacy
`smartApplyData`, non-production shell rejection and all S7 gates remain
unchanged.

## Acceptance

1. Unit fixtures cover positive PCS X inventory, pagination and all fail-closed
   cases.
2. HP, Mayo and Gordian run through the same native adapter path.
3. Focused live produces one audited Exact and two verified no-matches, with
   zero wrong URL, company, tenant, title or location.
4. Same-version replay is 3/3 with zero mismatch, fixture gap, tape divergence
   or missing boundary.
5. Existing Eightfold tests, focused provider tests, provider benchmark,
   resolver benchmark, architecture gate and `git diff --check` pass.
