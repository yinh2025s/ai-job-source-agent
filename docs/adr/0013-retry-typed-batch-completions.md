# ADR-0013: Retry Typed Batch Completions

- Status: accepted
- Date: 2026-07-14

## Context

The live batch runner atomically stores one completion envelope per compatible
company input. Before this decision, every compatible envelope was restored as
terminal, including outcomes whose first failed stage explicitly reported
`retryable=true`. A transient timeout could therefore remain sticky forever even
though stage checkpoints already supported selective invalidation.

Automatic retry must preserve successful upstream evidence, avoid retrying
deterministic failures, remain crash-safe, and expose enough provenance to audit
which companies were restored or resubmitted.

## Decision

1. Completion compatibility remains bound to normalized input, deterministic run
   configuration, batch execution configuration, and adapter version. This ADR
   does not weaken any cache or checkpoint compatibility check.
2. A compatible pipeline success is restored. For a non-success completion, the
   runner validates the complete ordered stage chain and examines its first
   non-success stage.
3. Automatic resubmission requires an explicit boolean `retryable=true` on that
   stage. An explicit `false` is restored as non-retryable. Missing, malformed,
   out-of-order, or inconsistent retryability evidence fails closed and restores
   the completion instead of issuing new network requests.
4. Before resubmission, the stage checkpoint store invalidates only the failed
   stage and its downstream stages. Compatible successful upstream checkpoints
   remain reusable.
5. The old batch completion is not deleted before the retry. It remains the
   durable crash fallback until the new result is atomically published over it.
   Repeating the operation after interruption is idempotent.
6. `--no-resume` and explicit `--rerun-stage` retain their existing override
   semantics and bypass whole-company completion restore.
7. Trace provenance records only action, reason, stage, and canonical reason code.
   Summary counts distinguish compatible completions, successful restores,
   non-retryable restores, unclassified fail-closed restores, and retryable
   resubmissions. Raw error text, page content, credentials, and browser state are
   prohibited.
8. Batch completion reads and writes remove only stale temporary files belonging
   to the same execution fingerprint while holding that fingerprint's lock.

## Consequences

- Transient network and budget outcomes can recover on an ordinary batch resume
  without re-running successful or deterministic companies.
- A persistent retryable failure may be attempted again on each later invocation;
  callers control that cadence by deciding when to resume the batch.
- The policy relies on typed stage results rather than parsing error strings.
- Snapshot evidence remains governed by ADR-0006. Attempt-scoped stage evidence is
  a separate persistence contract and is not inferred from completion trace.
