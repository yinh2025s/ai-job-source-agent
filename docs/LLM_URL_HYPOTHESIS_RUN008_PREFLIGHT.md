# LLM URL Hypothesis Run 008 Preflight

Status: offline preflight complete; no paid call has been made by this run.

## Purpose

Run one fixed 18-record DeepSeek A/B to measure whether zero-trust public URL
hypotheses recover candidates that the existing deterministic and frozen search
routes miss. This run does not use Fresh100 or blind cohorts and cannot trigger
a second paid rerun without new user authorization.

## Frozen Inputs

- Branch: `codex/llm-candidate-reasoning-foundation`
- Cohort:
  `samples/evaluation/llm_candidate_reasoning_g_dev_v1.json`
- Cohort file SHA-256:
  `b47545c9c09759a52600bf5cab18cedc4058de0df0667d157ab83357029f1733`
- Cohort records digest:
  `d3c65152af084f1ad2bd994c4a6d67de1e09a66781437be00adb88ceb88368ae`
- Evaluator labels:
  `samples/evaluation/llm_candidate_reasoning_g_dev_labels_v1.json`
- Labels SHA-256:
  `990dcc2cb83ec7c701c3e656bb299f8c78287e31cb1f99d3c01f05adaed64776`
- Provider/model: DeepSeek / `deepseek-v4-flash`
- Prompt: `deepseek-company-candidates-v2`
- Maximum calls: 30
- Hard cost cap: USD 0.05
- Maximum calls per company: 2

The capture manifest records the exact clean Git commit before the first arm
and verifies the same branch, commit and clean worktree after replay.

## Isolated Artifact Root

The fresh persistent root is:

`/Users/yinhuang/.codex/visualizations/2026/07/20/019f8029-8c5e-77b2-9fe6-d357b476e283/ai-job-llm-url-hypothesis-run008-20260727`

It is outside the main repository and outside `/private/tmp`. The runner refuses
an existing root. Baseline and treatment use separate snapshots, checkpoints,
evidence and outputs under this root.

## Commands

Capture, exactly once:

```bash
python3 scripts/run_candidate_reasoning_experiment.py \
  --root /Users/yinhuang/.codex/visualizations/2026/07/20/019f8029-8c5e-77b2-9fe6-d357b476e283/ai-job-llm-url-hypothesis-run008-20260727 \
  --cohort samples/evaluation/llm_candidate_reasoning_g_dev_v1.json \
  --model deepseek-v4-flash
```

Evaluator-only labels are loaded only after the capture is sealed:

```bash
python3 scripts/evaluate_candidate_reasoning_experiment.py \
  --root /Users/yinhuang/.codex/visualizations/2026/07/20/019f8029-8c5e-77b2-9fe6-d357b476e283/ai-job-llm-url-hypothesis-run008-20260727 \
  --labels samples/evaluation/llm_candidate_reasoning_g_dev_labels_v1.json
```

## Causal Contract

Each record is linked by its answer-free input evidence digest to:

1. planner queries and the complete planner URL-hypothesis pool;
2. content-addressed frozen query responses and search candidate pool;
3. the ranker request and output;
4. the recorded candidate portfolio;
5. S2/S5 adoption provenance and S7 identity evidence;
6. per-record calls, tokens, cost and latency;
7. same-version replay classification.

A URL hypothesis is causal only when the baseline misses, the treatment adopts
that explicit frozen hypothesis, the same URL was not independently supplied by
the frozen search pool, and deterministic verification succeeds. ATS recovery
additionally requires `llm_url_hypothesis` S5 provenance and a verified final
identity assertion.

## Promotion Gate

- Candidate recall@3 uplift: at least 25 percentage points.
- Strict causal recovery: at least 40% of 18 records.
- Wrong verified URL: zero.
- Invented or modified adopted candidate URL: zero.
- Cross-company and cross-tenant adoption: zero.
- Replay mismatch and fixture gap: zero.
- Calls: no more than two per company, 30 total.
- Estimated cost: no more than USD 0.05.

The report separately publishes frozen search recall@3 and @10, hypothesis
recall@3, verified website recall, Exact counts, Website/Career/ATS causal
contributions, failure calls, token usage, cost and P50/P95/max latency.
