# `.223` SuccessFactors Detail Continuity Phase A

## Frozen Evidence

Three independent companies share one provider-family failure after S5 has
already established a first-party hiring relationship:

| Company | Correct opening | Detail evidence | Current terminal |
| --- | --- | --- | --- |
| Arkema | Beaumont `1401455133` | SuccessFactors JobPosting microdata: Human Resources Manager Job, Beaumont, TX, Arkema | title/provider identity rejected |
| Aramark | Indianapolis `1404601400` | SuccessFactors JobPosting microdata: HR Manager, Indianapolis, IN, Aramark | opening identity missing |
| Cintas | Fort Myers `1373711200` | SuccessFactors JobPosting microdata: Human Resources Manager II, Fort Myers, FL, Cintas | correct city not selected |

The current pages were captured in `/private/tmp` before implementation. The
three pages use the same SAP SuccessFactors Career Site microdata structure.
The `.220` frozen inventory already contains each correct opening. `.222`
prevents Cintas from selecting a wrong city, but does not read this microdata
or preserve the native provider identity through S7.

## Root Cause

1. `SuccessFactorsAdapter.list_jobs()` returns title and URL from the search
   inventory but does not verify candidate detail microdata, so custom-domain
   candidates have no location or hiring-organization evidence.
2. `OpeningMatchStage` can detect a native SuccessFactors adapter from page
   evidence while the relationship-verified S5 board remains `generic`.
   `_provider_identity()` rebuilds from the original generic URL and discards
   the stronger native board/tenant evidence before `_opening_identity()`.
3. SAP Career Site may publish a terminal display token `Job`; it must be
   treated only as provider-owned presentation metadata, not as a global title
   relaxation.

## Frozen Contract

### Adapter-owned detail verification

- Fetch at most three exact-title candidate details.
- The detail must remain on the candidate URL's safe host and canonical URL.
- The verified listing binds custom-domain candidates to its tenant. A detail
  must stay on that exact host and must not publish a conflicting tenant; when
  detail tenant metadata is present it must equal the listing tenant.
- Accept only schema.org JobPosting microdata bounded by the JobPosting item;
  read `addressLocality`, `addressRegion` and `hiringOrganization`. Read title
  from the same item when present; if the provider template omits it, accept
  exactly one page-level `og:title` only when the canonical detail URL is exact.
- The detail title must match the inventory title after SuccessFactors-only
  normalization of one terminal presentation token `Job`.
- Emit location and hiring organization only for a fully verified detail.
- Wrong tenant, redirect, URL, title, missing microdata or malformed fields
  remain unresolved. No free-text location parser is allowed.

### Generic-to-typed identity promotion

- Promotion is allowed only when the current generic provider identity is
  relationship-verified and the selected opening came from a native adapter
  result with complete adapter-owned board identifier evidence.
- The selected URL must exactly equal one native candidate and the adapter
  trace must bind provider, tenant and canonical board.
- Preserve the existing hiring entity and relationship evidence; promotion
  does not create a hiring relationship.
- The promoted opening must still pass strict company, provider, tenant,
  title and location validation.
- Search snippets, guessed paths, hostname similarity and URL location tokens
  cannot trigger promotion.

## Acceptance

- Arkema selects only the Beaumont opening for a Beaumont source; Clear Lake
  remains rejected.
- Aramark produces a complete SuccessFactors provider/opening chain for the
  Indianapolis opening.
- Cintas selects only the Fort Myers opening; Gahanna and other cities remain
  rejected.
- Wrong tenant, wrong city, cross-provider, missing hiring organization and
  unverified generic relationship tests fail closed.
- Focused adapter/stage tests, three-company live, identity audit and
  same-version replay pass before closure.

## Ownership

- Adapter line: `job_source_agent/providers/successfactors.py` and
  `tests/test_provider_successfactors.py`.
- Identity line: `job_source_agent/stages/discovery.py` and
  `tests/test_parallel_candidate_stage.py`.
- Main line: shared version, docs, changelog, integration review and gates.

## Rollback

Revert if microdata outside the selected JobPosting item is accepted, if a
generic relationship can authorize an unrelated tenant, or if a wrong-city or
cross-tenant opening reaches Exact.
