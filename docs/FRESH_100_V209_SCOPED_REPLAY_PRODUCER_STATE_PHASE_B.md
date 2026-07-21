# Fresh100 `.209` Scoped Replay Producer State Phase B

## Defect

The frozen `.208` five-record live produced four verified Exact outcomes and
one retryable NYC website timeout. Failure replay reproduced NYC, but full
replay stopped on the second Versana posting with one unconsumed S2 request:

```text
GET https://versana.io
```

The live LinkedIn evidence cache contained `https://versana.io`. Replay rebuilt
the cache from the final canonical selected URL, `https://versana.io/`. The
resolver caught the strict request mismatch as a candidate fetch failure, while
stage finalization correctly exposed the original tape entry as unconsumed.

This was not a network failure and not a reason to relax tape identity. It was
lossy reconstruction of mutable producer input.

## Contract

S2 trace now records `linkedin_official_evidence_urls`: the exact ordered URLs
returned by live or cached LinkedIn evidence before candidate verification.
Scoped replay restores that field verbatim when the source is `cache`.

For old captures without the field, replay first reads verification allocation
entries carrying `linkedin_cached_official_website`, preserving their exact URL
spelling. The previous candidate/selected scan remains only as a final legacy
fallback. No company, domain or job identifier is special-cased, and strict
request identity and unconsumed-tape checks are unchanged.

## Evidence

- Targeted replay/resolver/checkpoint suite: 246 tests passed.
- The isolated Versana UX replay reproduced 1/1.
- Migration replay of the immutable `.208` five-record capture reproduced 5/5.
- Mismatch: 0.
- Fixture gap: 0.
- Tape divergence: 0.

The migration replay is diagnostic because code version changed. Release
acceptance requires a clean `.209` live capture with new checkpoint, completion,
evidence, snapshot and output roots, followed by same-version replay.

## Next Gate

Freeze `.209`, rerun the same five records, audit every Exact identity chain,
and replay all 5/5. Run the 2500+ full suite once after that focused gate, not
after each local edit.
