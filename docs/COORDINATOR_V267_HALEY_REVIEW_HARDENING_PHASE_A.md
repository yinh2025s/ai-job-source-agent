# v267 Haley Marketing Review Hardening - Phase A

## Trigger

Post-implementation review found one correctness defect and two verification
gaps in `.266`. The `.266` live result remains useful evidence, but it is not
the final accepted provider version.

## Correctness Defect

The adapter stopped pagination after the first normalized exact-title
candidate. If page one contained the right title in the wrong city and a later
page contained the same title in the target city, the correct opening was never
read.

The fix must require title plus an exact normalized provider location before
early stop when the source posting has a location. If no such pair is present,
pagination continues until the filtered inventory is complete or the existing
bounded cap is reached. An incomplete wrong-location candidate set cannot be
published.

## Provider URL And State Evidence

HMG inventory records publish `POST_SEO_URL`, `POST_ID`,
`SEO_PERMALINK`, `POST_ARCHIVED` and expiration metadata.

The adapter must:

- require the provider-published canonical URL;
- verify exact same HTTPS tenant, `/jb/{slug}/{id}` path, slug and ID
  continuity;
- reject a cross-tenant or mismatched canonical URL;
- exclude explicitly archived records;
- keep replay deterministic by recording expiration metadata without comparing
  it to the current wall clock.

No detail URL may be accepted from locally constructed fields alone.

## Privacy

HMG `h/t` values are short-lived anonymous validation tickets. They are not
LinkedIn credentials, but normal trace and shareable sanitized snapshot URLs
must redact them for the exact HMG inventory contract:

`/json/index.smpl?arg=list_posts&pid=gwt`

Other unrelated query parameters named `h` or `t` remain semantic.

## Clarifications

- The final verification run must use fresh isolated roots after all `.267`
  product changes.
- Top Prospect Group's no-match result may retain the earlier generic
  top-level provider identity because no opening identity is published. Its S6
  evidence must still show page-evidenced `haley_marketing` and complete native
  inventory. Any selected opening remains subject to ordinary provider
  promotion and S7, as already demonstrated by Kavaliro.
- Plugin, authenticated External Apply, coordinator-v2, LLM and sealed
  holdouts remain frozen.

## Acceptance

1. Add a two-page same-title/wrong-city then correct-city regression test.
2. Add search-entry POST and ticket-refresh tests.
3. Add canonical URL, archived-record and cross-tenant rejection tests.
4. Add request/snapshot identity redaction tests for HMG `h/t`.
5. Run focused related tests, provider benchmark, resolver benchmark and
   architecture gate.
6. Run a new isolated three-record live and same-version replay after all code
   changes.
7. Require 3/3 terminal recovery, 1/1 Exact safety, replay 3/3 reproduced and
   zero mismatch/gap.
