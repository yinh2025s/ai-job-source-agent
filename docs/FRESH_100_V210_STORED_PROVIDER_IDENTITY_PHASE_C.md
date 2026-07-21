# Fresh100 `.210` Stored Provider Identity Phase C

## Frozen Build

- Commit: `a419d5db0e3dde0c20525384ebc572b5598102f9`
- Adapter: `2026-07-21.210`
- Focused input SHA-256:
  `2aae8aedbe3db69e5890268bff389129ce739d84f3cf5b44b8a89d68ac70ab4d`
- Source archive SHA-256:
  `87e94e54ec3256c16501167854e91b196c6561c34ead610ab4fc3476d27e149b`
- Release archive SHA-256:
  `3401457cca5d5489505d93fa2b61845dbb95a1fcdd6d7a689971fc40e55b6d6c`
- Run root: `/private/tmp/fresh3-v210-stored-identity-20260721-run1`

The three-record run used new checkpoint, completion, evidence, snapshot and
output roots and restored zero records. Code remained frozen during live and
replay execution.

## Live Outcome

| Posting | Outcome | Evidence |
| --- | --- | --- |
| WalkMe - DevOps Engineer | Partial, identity ambiguous | Lever board candidate retained; no unverified opening published |
| OneApp - Product Designer | Exact | Verified OneApp Pinpoint tenant, exact title and Portland location |
| Heritage Companies - Corporate HR Manager | Retryable failure | S2 network timeout before the stored-board path |

OneApp's Exact identity chain passed company, hiring entity, provider, tenant,
board, opening, title and location validation. Observed wrong URL, company,
tenant and location counts are zero.

## Replay

- Full replay selected/exported/executed: 3/3/3.
- Reproduced: 3.
- Mismatch: 0.
- Fixture gap: 0.
- Record integrity: passed.

A separate clean Heritage-only retry again ended at S2 `NETWORK_TIMEOUT` after
54.1 seconds. Its same-version replay reproduced 1/1 with zero mismatch or
fixture gap.

## Decision

The `.210` contract and deterministic replay are accepted. The focused live is
not evidence that the observed Heritage stored-board branch executed under
`.210`: both clean attempts were blocked earlier by endpoint transport. The
cross-version scoped replay demonstrates the intended field correction while
preserving the provider tenant and board, and the provider-independent branch
is covered by targeted tests, but the live branch-level gate remains
transport-blocked.

Do not rerun Heritage repeatedly or claim a recall improvement. The next repair
must be selected from the causal Fresh100 clusters spanning at least three
independent companies. Full offline integration remains deferred until that
repair is integrated and ready for a single freeze gate.
