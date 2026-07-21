# LLM Candidate Reasoning Phase D Report

Status: completed, promotion rejected, feature remains off.

## Frozen Execution

- Artifact root: `/private/tmp/ai-job-llm-phase-d-20260721/run-deepseek-v4-flash-006`
- Runtime commit: `de3613109fb61a102ad373a9d61fa05f2789e582`
- Provider/model: DeepSeek / `deepseek-v4-flash`
- Prompt: `deepseek-company-candidates-v1`
- Cohort: fixed 18-record eligible-G development cohort
- Labels loaded during capture: false
- Real calls: 25 of 36 allowed
- Provider-accounted tokens: 4,747 prompt, 1,988 completion
- Provider-accounted cost: USD 0.00122122 of USD 0.50

The capture, baseline, treatment, decisions, snapshots, checkpoints and replay
are sealed under the artifact root. The API credential was loaded from an
external environment file and is absent from the repository and artifacts.

## Replay Gate

- Same-version fixture-only replay: 18/18 reproduced
- Replay mismatch: 0
- Fixture gap: 0
- Selected decision records: 25
- Outcome and record-integrity gates: passed

Replay constructs no real provider client. The preceding `run-005` is retained
as an unsealed diagnostic run and is excluded from all metrics.

## A/B Results

| Metric | Baseline | Treatment | Required |
| --- | ---: | ---: | ---: |
| Candidate recall@3 | 0/18 | 1/18 | +25 percentage points |
| Verified website recall | 3/18 | 4/18 | diagnostic |
| Eligible-G recovery | - | 1/18 (5.56%) | at least 40% |
| Exact opening | 0 | 1 | diagnostic |
| Wrong verified URL | 1 | 1 | 0 |
| Cross-company | 1 | 1 | 0 |
| Cross-tenant | 0 | 0 | 0 |
| Invented/modified treatment URL | - | 0 | 0 |

Treatment emitted one Exact: Versana, `DevOps Engineer - Raleigh`, Raleigh, NC,
on Lever tenant `Versana`. Manual review confirmed company, title, location,
provider, tenant and canonical opening URL from the frozen public evidence and
the S7 identity assertion.

The unsafe result is record 081. `WICHITA COMPANY LIMITED` references the UK
company at `wichita.co.uk`, while the deterministic resolver bound it to
`wichita.gov` and GovernmentJobs tenant `wichita`. The collision exists in both
arms and was not invented by the model, but it still violates the treatment
safety gate and blocks promotion.

## Operational Results

- Mean calls per company: 1.39; maximum: 2
- Advisory failures: 14/18
- Aggregated model latency P50/P95: 8.20/10.84 seconds per company
- Provider budget ledger cost: USD 0.00122122
- Evaluator decision-record estimate: USD 0.00142730

The provider budget ledger is the capture-time cost authority. The larger
evaluator value is preserved rather than normalized away: failed timeout audit
records can inherit the client's previous usage snapshot, inflating the
decision-record aggregate. Per-call usage must be reset before any later
experiment.

## Decision

Promotion failed on all four substantive gates: recall uplift, eligible-G
recovery, zero wrong URLs and zero cross-company output. The feature stays off.
No blind v2/v3 or Fresh100 cohort was opened, and no prompt-specific fix should
be derived from this development cohort.

Post-experiment stabilization `.200` completed the first two independent fixes
without changing this sealed result:

1. deterministic private-company/municipality identity collisions now fail
   closed before first-party ATS trust can propagate;
2. every planner/ranker invocation resets usage before calling the provider, so
   timeout audit records cannot inherit prior-call tokens.

The remaining question is the high planner/ranker advisory timeout rate. It
must be addressed without weakening fail-closed fallback or URL verification,
and requires a separately approved experiment rather than rewriting run-006.
