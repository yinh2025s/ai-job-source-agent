# Fresh100 `.211` Trusted Opening Search Scheduling Phase B

## Implementation

`JobOpeningMatcher` now separates cheap landing-page extraction from expensive
landing-page JS transport discovery. For an unfiltered reused landing page it:

1. extracts links, structured postings and bounded generic inventory;
2. remembers the page without fetching its JS assets;
3. tries declared, interactive and provider-fallback title routes;
4. returns immediately if any route produces a verified match;
5. otherwise runs the remembered landing-page JS inventory exactly once before
   verified site search.

Title-filtered route pages retain immediate JS discovery. The refactor shares
one JS inventory handler, preserving inventory status, endpoint provenance,
candidate trace and blocked/retryable diagnostics.

## Safety

The change does not alter candidate scoring or authorization. Existing
canonical URL, same-site, provider, tenant, hiring organization, strict title,
location and S7 checks remain mandatory. Cross-site and wrong-location results
from deferred JS inventory are explicitly covered as negative tests.

## Offline Verification

- Opening matcher module: 99 tests passed in the implementation branch.
- Integrated opening matcher, generic inventory, availability and incomplete
  discovery modules: 168 tests passed.
- `git diff --check`: passed before version documentation.

No full 2500+ suite was run. Phase C first requires affected scoped replay and
a frozen four-company focused live/replay gate. Only after this final generic
repair is integrated will the project run one full offline integration gate.
