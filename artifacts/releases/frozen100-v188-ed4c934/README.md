# Frozen 100 `.188` Release Archive

This local archive fixes the final frozen-100 release before the independent
July 18 fresh-100 generalization benchmark.

- Git commit: `ed4c9343ec382387542d7b917050acbc04096dda`
- Git tag: `frozen100-v188`
- Adapter version: `2026-07-20.188`
- Final ledger: 69 Exact, 23 Verified Not Found, 5 External Blocked,
  3 Input Identity Invalid, 0 System Gaps
- Final replay: 100 reproduced, 0 mismatch, 0 fixture gap

`source-frozen100-v188-ed4c934.tar.gz` is a Git archive of the tagged source.
`frozen100-v188-final-artifacts.tar.zst` contains the original frozen cohort
input plus final results, trace, summary, evidence store, checkpoints,
completion store, snapshots, replay bundle, and Exact audit. `SHA256SUMS`
authenticates both archives.

The original `/private/tmp/frozen100-v228-final100-*` paths remain untouched.
No fresh-cohort output may be written into this directory or counted in the
frozen-100 69/100 result.
