# Coordinator `.233` Captured Producer-State Replay Phase A

## Causal Cluster

Scoped replay rebuilds a record-owned company discovery store so batch-final
mutable evidence cannot leak backward into the replay. That isolation is
correct, but one captured execution shape is currently under-reconstructed:

1. live S5 writes a verified provider board to the shared store;
2. the S6 child cannot restore S5 and resumes from S5;
3. the rerun S5 explicitly reads `stored_verified_provider_board`;
4. authoritative S2 and S4 state is restored from checkpoints; and
5. S6 performs a provider request from that stored board.

Replay correctly resumes from the captured S5 boundary. It restores S1-S4 into
the checkpoint store, but its record-local discovery store remains empty
because S2/S4 did not themselves read stored evidence. Provider restoration
then requires `record.career`, declines the board and makes replay S5 choose a
different branch. The captured S6 provider request remains unconsumed.

This is a producer-state reconstruction defect, not an OutcomeTape matcher or
provider fixture defect.

## Contract

When captured S5 explicitly selected `stored_verified_provider_board`, replay
may reconstruct its prerequisites only if all of these hold:

- checkpoint events prove the captured resume stage was S5;
- authoritative upstream results contain successful S2 and S4 outputs;
- the frozen source store contains the exact Website, Career and provider board;
- Website and Career URLs equal the authoritative upstream outputs;
- Career points back to that Website;
- company name and LinkedIn company identity are unchanged;
- provider, tenant and canonical board URL agree with the selected board and
  current provider adapter.

The original evidence objects, including source, method and timestamp, must be
copied into the record-local store. Batch-final evidence cannot be copied when
the captured stored-read marker is absent.

If the marker exists but the prerequisites are missing or conflict, scoped
replay must fail before network/tape execution with an explicit producer-state
error rather than surfacing an unrelated unconsumed request.

## Change Boundary

Phase B changes only:

- `scripts/replay_failure_bundle.py`;
- `tests/test_replay_failure_bundle.py`;
- adapter version and Phase C governance summaries.

It does not change live discovery, provider adapters, relationship
authorization, S7, the extension, LLM code or sealed cohorts.

## Acceptance

1. A Focus-shaped captured S5 resume reconstructs exact Website, Career and
   stored provider board in a record-owned store.
2. Missing marker, non-S5 resume, cross-company, cross-tenant, board-URL
   conflict and Website/Career discontinuity all fail closed.
3. Same-company postings remain isolated.
4. Batch-final evidence without a captured read marker is never restored.
5. Scoped replay consumes the captured provider request with zero extra or
   unconsumed tape entries.
6. Existing direct S6 checkpoint replay remains unchanged.
