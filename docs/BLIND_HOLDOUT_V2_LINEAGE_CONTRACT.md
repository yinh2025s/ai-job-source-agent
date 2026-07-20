# Blind Holdout V2 Selection/Runtime Lineage Contract

## Defect

Manifest schema 1.0 bound the cohort to the code commit that selected it, and
the one-shot runner required execution on exactly that same commit. This made
the required lifecycle impossible: a cohort frozen before implementation could
not evaluate the implementation without first becoming observed.

## Schema 1.1

The freeze manifest now records:

- `selection_code_commit`
- `selection_source_tree_sha256`
- `execution_code_policy=clean_descendant_of_selection_commit`
- the unchanged cohort, candidate-pool, run-config, history, and identity
  digests

The one-shot runner requires a tracked-clean runtime tree, verifies the frozen
selection commit still resolves to the recorded tree, and requires it to be a
Git ancestor of the runtime commit. The exclusive ledger and execution manifest
bind both selection and runtime identities before live execution starts.

Legacy schema 1.0 keeps exact selection/runtime identity. It is not silently
upgraded.

## Fail-Closed Cases

- Divergent or rewritten runtime history.
- Selection commit whose tree no longer matches the frozen manifest.
- Dirty tracked runtime tree.
- Changed cohort or run-configuration bytes.
- Existing artifact directory or consumed ledger.
- Missing selection/runtime identity in review-chain verification.

## Gate

Contract tests cover descendant acceptance, non-descendant rejection, legacy
exact-code behavior, selection-tree validation, one-shot ledger exclusivity,
serial/no-resume command construction, and review-chain identity drift.

Complete offline gates pass 2556 tests (4 skipped), 25/25 provider benchmark,
6/6 resolver benchmark, and 46 native adapters with zero architecture issues.
No S1-S7 product behavior changes in this iteration.

## Sealed Selection

After the network route recovered, S1-only collection produced a frozen pool of
239 unique LinkedIn job identities. Two schema-1.1 cohorts were selected from a
clean `66054fdcad16b5c910e22b41266f6452d8cc11d9` worktree without running S2-S7:

- v2: 40 records, 40 unique identities, zero post-selection overlap, cohort
  SHA-256 `d2846346085661fbbf10aed3aa069828695718d84846443d9dce0ed15dc5528d`.
- v3: 40 records, 40 unique identities, zero post-selection overlap, cohort
  SHA-256 `6b655522f0f2817e0b73732ecd9c8035f1092e9384177e11896447e6b69bf556`.
- Cross-cohort identity overlap is zero and neither cohort contains website,
  career, board, or opening prefills.

The sealed files are read-only and no one-shot execution ledger exists. The
repository receipt stores only counts and digests, not holdout identities. Both
cohorts remain unobserved and must not be opened or executed during product
implementation.
