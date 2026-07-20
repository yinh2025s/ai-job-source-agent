# Fresh 100 `.188` Cold Run

This directory records the adjudication of the isolated 2026-07-20 cold-start run over
the cohort observed on 2026-07-18.

- Source commit: `ed4c9343ec382387542d7b917050acbc04096dda`
- Frozen source tag: `frozen100-v188`
- Raw Exact: `12/100`
- Audited Exact: `11/100`
- Eligible Exact recall: `11/90`
- Replay gate: failed (`97 reproduced`, `1 mismatch`, `1 tape divergence`,
  `1 captured-boundary gap`, `0 fixture gaps in completed comparisons`)

`closure-matrix.csv` assigns all 100 records to the required terminal taxonomy.
`report.md` contains the exact audit and failure-cluster analysis. The compressed release
archive also contains the immutable raw live and replay directories from `/private/tmp`.
