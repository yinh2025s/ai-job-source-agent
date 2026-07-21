# LLM Candidate Reasoning Phase D Proposal

Status: awaiting explicit user approval. No real model call is authorized by this
document.

## Provider And Model

- Proposed provider: OpenAI API.
- Proposed pinned model: `gpt-5-mini-2025-08-07`.
- Reason: it supports structured outputs and is sufficient for two bounded tasks:
  producing at most three URL-free search queries and ordering at most ten existing
  candidate IDs. It is not used for browsing, verification, provider identity, S6,
  S7, or publication.

No OpenAI client, endpoint, credential, or SDK is present in the branch yet. The
provider adapter may be implemented only after approval and must remain behind the
existing `LLMReasoningClient` interface.

Official model reference and rates checked on 2026-07-21:
`https://developers.openai.com/api/docs/models/gpt-5-mini`.

## Exact Model Fields

Planner request fields:

- `schema_version`
- `normalized_company_name`
- `linkedin_company_slug`
- truncated `public_company_summary`
- `job_title`
- `job_location`
- `industry`
- `company_location`
- at most ten rejected-candidate summaries containing only candidate ID, source,
  fixed rejection reason and display domain

Ranker request fields:

- `schema_version`
- `normalized_company_name`
- `industry`
- `company_location`
- at most ten immutable candidates containing candidate ID, existing HTTPS URL,
  bounded title/snippet, source, query ID and source rank
- bounded public context evidence IDs

Cookies, tokens, Authorization headers, API keys, browser storage, HTML, raw
snapshots, personal profiles, user names/emails, chain-of-thought, correct website
labels and closure matrices are forbidden before request construction and again
before artifact persistence.

## Fixed Experiment And Cost

- Cohort: the frozen 18-record eligible-G development input only.
- Maximum calls: one planner plus one ranker per company, 36 total.
- Retry: none.
- Concurrency: one company at a time for the first experiment.
- Deadline: one shared 8-second LLM deadline per company; it cannot consume the S6
  opening budget.
- Estimated bounded input: at most about 225,000 tokens across the experiment.
- Estimated bounded output: at most about 54,000 tokens, including reasoning/output
  allowance.
- At published rates of USD 0.25/M input and USD 2.00/M output, the bounded estimate
  is about USD 0.17.
- Proposed hard cost cap: USD 0.50. The runner must stop before issuing a call that
  could exceed the cap.

The report must use provider-reported prompt/completion/total tokens and actual cost;
the estimate is not a substitute for usage accounting.

## Privacy, Logging And Credentials

Only sanitized structured requests/responses are retained locally. Provider response
IDs may be used transiently for diagnostics but are not evidence and are not persisted
in the decision record. Local artifacts use private directories, atomic files and no
raw model text. The Responses request must set `store: false` and must not use
background mode, conversations, files or provider-side tools. OpenAI states that API
content is not used for training unless the customer opts in; default abuse-monitoring
logs may retain customer content for up to 30 days. This experiment must not enable
data sharing. Official policy checked on 2026-07-21:
`https://platform.openai.com/docs/models/default-usage-policies-by-endpoint`.

The credential is injected only as `OPENAI_API_KEY` in the process environment. It is
never accepted as a CLI value, written to run config, logged, snapshotted, bundled or
committed. A missing credential fails before creating the experiment ledger.

## Isolation And Fallback

Proposed isolated root:

```text
/private/tmp/ai-job-llm-phase-d-20260721/
  baseline/
  treatment/
  decisions/
  snapshots/
  checkpoints/
  bundles/
  reports/
```

Each subrun receives a fresh path. Baseline and treatment use the same original input
and frozen search evidence. Evaluator-only labels are loaded only after both outputs
are sealed. Any malformed JSON, timeout, provider error, unknown candidate ID, unsafe
output, artifact failure or fixture mismatch returns the deterministic baseline and
cannot replace its terminal reason.

After live treatment, same-version replay reads only the frozen decision and HTTP
fixtures, constructs no real model client, and requires zero mismatch, zero fixture
gap and zero unconsumed decision.

## Approval Boundary

Approval must explicitly name:

1. OpenAI as provider.
2. `gpt-5-mini-2025-08-07` as the pinned model.
3. At most 36 calls over the fixed 18 records.
4. A USD 0.50 hard cost cap.

Without that approval, Phase D does not start. Full Fresh100 and blind v2/v3 remain
out of scope, and this branch is not merged into `main`.
