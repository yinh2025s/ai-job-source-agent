# v270 Prefixed API-Key Redaction - Phase C

## Decision

Accept `.270` for the Haley Marketing / HMG provider cluster and its shareable
focused artifacts.

The title/location pagination defect, HMG URL/body ticket leakage,
search-entry request-identity divergence and prefixed API-key leakage are
closed without changing matcher thresholds, stage scheduling, company
identity, provider/tenant continuity or S7 rules.

## Focused Live

Final isolated run:

`/private/tmp/v270-haley-focused-run1`

| Company | Final result |
| --- | --- |
| Madison-Davis, LLC | verified Haley Job List + `OPENING_NOT_FOUND` |
| Top Prospect Group | verified Haley inventory + `OPENING_NOT_FOUND` |
| Kavaliro | S7 Exact |

Aggregate:

- Website: 3/3;
- Career: 3/3;
- verified Job List: 3/3;
- Exact: 1/3;
- evidence-backed no match: 2/3;
- wrong URL: 0;
- wrong location: 0;
- cross-company: 0;
- cross-tenant: 0.

## Exact Audit

Kavaliro:

- source company and hiring entity: `Kavaliro`;
- target and selected title: `Quality Engineer`;
- target and selected location: `Jacksonville, FL`;
- provider: `haley_marketing`;
- tenant: `custom:jobs.kavaliro.com`;
- board: `https://jobs.kavaliro.com`;
- canonical opening:
  `https://jobs.kavaliro.com/jb/Quality-Engineer-Jobs-in-Jacksonville-Florida/14172225`;
- S7 verdict: `verified`;
- location classification: `exact`.

## Replay

All three scoped outcome tapes exported and replayed under `.270`:

- replayed: 3/3;
- reproduced: 3;
- expected transition: 0;
- budget recovery: 0;
- fixture gap: 0;
- mismatch: 0;
- tape divergence: 0;
- record-integrity gate: passed;
- outcome gate: passed.

## Privacy Audit

The final snapshot files, trace, checkpoints and scoped replay tapes were
audited together:

- HMG inventory URL ticket fields: 54, invalid or raw: 0;
- HMG board/search HTML ticket fields: 56, invalid or raw: 0;
- HMG inventory refresh ticket fields: 12, invalid or raw: 0;
- prefixed API-key fields: 12, invalid or raw: 0.

The counts include duplicated immutable snapshot paths and scoped replay
copies. Every HMG ticket uses the intended redacted or inert replay value, and
every prefixed API-key value is `[REDACTED]`.

Final independent read-only review reported no findings in the reviewed
provider, request-identity, snapshot, replay and Exact-identity scope.

## Offline Gates

- focused provider/request/snapshot/tape/job-board/registry/checkpoint tests:
  124/124;
- deterministic provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 48 native adapters, 0 issues;
- `git diff --check`: passed.

The full test suite was intentionally not run for this focused cluster. It
remains a release integration gate.

## Frozen Product Hashes

The live run used:

- `snapshot.py`:
  `7b8ac22bc3133eba7de978338b5a48c02d6c3c5680f8ca7668b1874b2ca78280`;
- `haley_marketing.py`:
  `43add8092457c9593ae13ccb57cc59ee8c4adfcb9c6ffc29566d7b1ba8286636`;
- `job_board.py`:
  `54d1629e12c208e70f3aaafb2d6c39886870571843aad0f275af8df01792fefa`;
- `request_identity.py`:
  `167eaa7e5b4bec4e697757b650f2dba39df7960f39a9c1e565021724f974b896`;
- `checkpoint.py`:
  `56ba4561a65e1f43fb857d8b4516b8cf226367326dfa3f2c023c316389031e45`.

## Closure

The shared HMG cluster is closed at `.270`. Future failures require a new
three-company causal trigger and shared production code path; a common failure
stage alone is not sufficient.

Plugin work, authenticated External Apply, coordinator-v2, LLM and sealed
holdouts remain frozen.
