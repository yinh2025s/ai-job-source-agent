# Fresh100 `.209` Scoped Replay Producer State Phase C

## Frozen Build

- Commit: `bf71c4bb4e3244ca4f3100559053c270a94f80a2`
- Adapter: `2026-07-21.209`
- Input SHA-256: `cb42e7fc26197c82570b2562db6668487ada80d4d2697df458717f837d85658e`
- Source archive SHA-256: `2ef3dcd59f19056650ab5dd4a469f43920c59c07d671bb9149853645344fa649`
- Run root: `/private/tmp/fresh5-v209-replay-producer-state-20260721-run1`
- Release archive SHA-256: `5b35e47628a6dcd4133f6272a985c3df0317fc11897dca3277bd5684dd46a703`

All checkpoint, completion, company-evidence, snapshot, failure-bundle and
full-replay roots were new. Restored completion count was zero.

## Live Outcome

| Posting | Terminal | Identity result |
| --- | --- | --- |
| Versana - DevOps Engineer - Raleigh | Exact | Lever `Versana`, Raleigh, NC |
| NYC Department of Social Services - DEVOPS ENGINEER | Retryable failure | S2 `NETWORK_TIMEOUT` |
| Versana - UX Designer | Exact | Lever `Versana`, Raleigh, NC |
| B&D Industries - Project Manager | Exact | ApplicantStack `banddindustries`, Santa Teresa, NM |
| B&D Industries - Human Resources Manager | Exact | ApplicantStack `banddindustries`, Albuquerque, NM |

Raw Exact is 4/5. Every Exact has a verified S7 hiring/provider/opening chain,
complete official inventory, exact title, exact location and canonical opening
URL. Wrong URL, company, tenant and location counts are zero. NYC publishes no
website, board or opening and remains retryable; this gate does not relabel the
network outcome as an external or domain absence.

## Replay Gate

- Full replay selected/exported/executed: 5/5/5.
- Reproduced: 5.
- Mismatch: 0.
- Fixture gap: 0.
- Tape divergence: 0.
- Record integrity: passed.
- Failure replay: 1/1 reproduced.

The second Versana posting consumes the exact `https://versana.io` S2 request;
the `.208` unconsumed root-path variant is closed without relaxing request
identity.

## Offline Integration

- Targeted replay/resolver/checkpoint tests: 246 passed.
- Full discovery: 2568 tests, 4 skipped, with one environment-only setup error
  because the sandbox denied a `127.0.0.1` bind.
- The affected extension HTTP contract class passed 5/5 outside that socket
  restriction.
- Provider benchmark: 25/25.
- Resolver benchmark: 6/6.
- Architecture validation: 46 native adapters, 0 issues.
- `git diff --check`: passed before the frozen commit.

## Decision

The scoped producer-state cluster is closed. `.209` is eligible for one clean
Fresh100 development-cohort rerun. That run must freeze code, use new roots and
analyze the resulting causal failure clusters before any further behavior
change. Focused 4/5 is not a Fresh100 recall claim.
