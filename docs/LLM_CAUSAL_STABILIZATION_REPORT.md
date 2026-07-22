# LLM Candidate Reasoning Causal Stabilization Report

Status: implementation complete on the isolated branch; promotion rejected;
feature remains off.

## Scope

This work stayed on `codex/llm-candidate-reasoning-foundation`. Main was not
modified, merged, rebased, or used as a destination. Fresh100 and blind v2/v3
were not opened. No company answer, reference website, Wichita special case, or
closure matrix entered a prompt or production resolver rule.

## Stage A: Run 006 Audit

The 18 historical records were traced and assigned one exclusive causal class:

| Class | Count |
| --- | ---: |
| `OPERATIONAL_FAILURE` | 13 |
| `DETERMINISTIC_OR_NETWORK_VARIANCE` | 4 |
| `BASELINE_IDENTITY_DEFECT` | 1 |
| `SOURCE_RECALL_MISS` | 0 |
| `RANKER_MISS` | 0 |
| `VERIFICATION_FAILURE` | 0 |

Eleven planners completed but all eleven rankers timed out. Two planners timed
out and one planner response was rejected for URL output. Versana made zero LLM
calls and is not uplift. Wichita was wrong in both arms and is a deterministic
identity defect. The complete record table and evidence-retention limitation are
in `docs/LLM_RUN006_CAUSAL_AUDIT.md`.

## Stage B: Infrastructure

- LLM run configuration advances from `1.5` to `1.6`; flag-off remains schema
  `1.4`, and historical `1.5` stays readable for fixture replay.
- Total, planner, search, and ranker budgets are versioned behavior identity.
  Phase budgets cannot exceed the total and live configuration reserves at
  least one second for ranker execution.
- Planner/ranker calls carry the smaller of their phase budget and current
  total-deadline remainder through the provider-neutral interface to DeepSeek
  transport. The hidden low-level timeout conflict is removed.
- Provider usage is reset before each invocation. Timeout, malformed, and
  provider failures retain zero usage rather than a previous call's tokens.
- Runtime output distinguishes `llm_plan_used` and `llm_rank_used`.
  `llm_causal_contribution` remains evaluator-owned and `not_evaluated` in live
  traces.
- Any future development capture is bounded to 30 calls and USD 0.05. The
  feature remains disabled by default.

## Stage C: Frozen Causal Evaluation

Planner and ranker are no longer judged from two independently changing live
arms:

1. Planner source evaluation freezes the response for each exact query and
   reports deterministic versus LLM source recall@10 and end-to-end recall@3.
2. Ranker evaluation gives deterministic and LLM rankers one identical frozen
   candidate pool. Conditional recall@3 excludes records whose reference
   candidate never entered that pool.
3. A strict filesystem query store uses canonical query digests, deterministic
   JSON, atomic writes, corruption/symlink checks, query-ID rebinding and
   missing/unconsumed replay failures. Replay owns no live search backend.
4. `llm_calls=0`, network-only arm differences, unadopted decisions, source
   misses and failed rank invocations cannot count as causal recovery.
5. Future experiment manifests seal every frozen query-response digest alongside
   decisions, snapshots, checkpoints and result artifacts.

## Stage D: Offline Gates

Final branch gates:

- full suite: 2,676 passed, 4 skipped;
- LLM reasoning suite: 122 passed;
- LLM bundle/replay suite: 32 passed;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 46 native adapters, 0 issues;
- `git diff --check`: clean.

The full suite required ordinary localhost socket permission for five extension
bridge tests; the final complete run passed in one invocation. No real model or
external live benchmark was used by these gates.

## Stage E: Real Experiment Constraint

Run 007 remains the one completed formal DeepSeek rerun. It used the same 18
development records, made 28 calls, cost USD 0.00762006 and replayed 18/18 with
zero mismatch or fixture gap. It removed the run-006 timeout cluster, but it
predates the frozen-query and explicit causal-attribution contracts.

Consequently its 4/18 treatment candidate recall and 4/18 old eligible recovery
cannot be upgraded into verified causal recoveries. The only Exact, Hays + Sons,
made zero LLM calls. This goal made zero additional DeepSeek calls and did not
perform a second formal rerun.

## Stage F: Promotion

Promotion is rejected:

- observed run-007 candidate uplift was 22.22 percentage points, below 25;
- old eligible recovery was 22.22%, below 40%, and is not proven under the new
  causal contract;
- the only Exact had `llm_calls=0`;
- no compliant frozen-query real run exists from which to claim planner source
  or conditional ranker recovery.

Run 007 did satisfy zero wrong website, cross-company, cross-tenant and invented
URL counts after the generic legal-entity/geography fix; replay was 100%, and
mean calls were 1.56 per company. Those safety and operational results do not
override the failed recall and causal gates.

The branch is therefore not merge-ready as a product feature. It is a complete,
off-by-default experimental foundation. No blind cohort, prompt tuning, or paid
rerun should follow without a new user decision and a separately authorized
experiment design.
