# LLM Candidate Reasoning Phase D Proposal

Status: DeepSeek provider use approved by the user on 2026-07-21. The credential is
configured outside the repository. Real calls remain gated on a clean frozen commit,
the fixed 18-record runner, offline gates, call/cost enforcement and fresh artifact
roots.

Two attempted product captures are retained as unsealed diagnostics, not A/B results.
The first found an absolute-deadline runner defect before any API call. The corrected
run completed both live arms, but all unresolved records were classified
`TRANSPORT_FORBIDDEN`, so it also produced zero model decisions. The product transport
gate remains unchanged. A separate synthetic smoke reached DeepSeek and identified the
OpenAI-compatible `prompt_tokens_details.cached_tokens` usage extension now handled by
adapter v2.

## Provider And Model

- Approved provider: DeepSeek API.
- Pinned model: `deepseek-v4-flash` in explicit non-thinking mode.
- Reason: it supports structured outputs and is sufficient for two bounded tasks:
  producing at most three URL-free search queries and ordering at most ten existing
  candidate IDs. It is not used for browsing, verification, provider identity, S6,
  S7, or publication.

The provider adapter uses only the standard library and remains behind the existing
`LLMReasoningClient` interface. It sends one non-streaming request, never retries,
sets `thinking.type=disabled`, and requires JSON Output. `deepseek-chat` is not used
because DeepSeek documents that alias as scheduled for deprecation on 2026-07-24.

Official model, JSON Output and rates checked on 2026-07-21:
`https://api-docs.deepseek.com/quick_start/pricing`,
`https://api-docs.deepseek.com/guides/json_mode`, and
`https://api-docs.deepseek.com/api/create-chat-completion`.

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
- Deadline: one shared 15-second model-time deadline per company; planner and ranker
  consume it, while intervening public search uses its existing transport budget and
  does not consume the model-time balance or the S6 opening budget.
- Estimated bounded input: at most about 225,000 tokens across the experiment.
- Estimated bounded output: at most about 54,000 tokens, including reasoning/output
  allowance.
- At the published `deepseek-v4-flash` cache-miss rates of USD 0.14/M input and
  USD 0.28/M output, the bounded estimate is about USD 0.047.
- Proposed hard cost cap: USD 0.50. The runner must stop before issuing a call that
  could exceed the cap.

The report must use provider-reported prompt/completion/total tokens and actual cost;
the estimate is not a substitute for usage accounting.

## Privacy, Logging And Credentials

Only sanitized structured requests/responses are retained locally. Provider response
IDs may be used transiently for diagnostics but are not evidence and are not persisted
in the decision record. Local artifacts use private directories, atomic files and no
raw model text. The request does not use tools, files, browser state, `user_id`,
streaming, thinking mode or chain-of-thought. DeepSeek does not document an API
equivalent of `store:false`; its privacy policy says Inputs may be collected and that
Personal Data may be processed and stored in the People's Republic of China. Therefore
this experiment sends only public company identity, public job title/location and
bounded public search evidence; personal profile data is prohibited. Official policy
checked on 2026-07-21:
`https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html?locale=en_US`.

The credential is injected only as `DEEPSEEK_API_KEY` in the process environment. The
user-owned source file is outside the repository at
`/private/tmp/ai-job-llm-phase-d-20260721/secrets/deepseek.env`, mode `0600`; the runner
does not accept a key path or key value as CLI input. The secret is never written to run
config, logs, snapshots, bundles or Git. A missing credential fails before dispatch.

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

## Approval And Execution Boundary

The user's 2026-07-21 instruction to use the configured DeepSeek API authorizes:

1. DeepSeek as provider.
2. `deepseek-v4-flash` as the pinned non-thinking model.
3. At most 36 calls over the fixed 18 records.
4. A USD 0.50 hard cost cap.

Execution may start only after the DeepSeek-ready code is committed and all offline
gates pass. Full Fresh100 and blind v2/v3 remain out of scope, and this branch is not
merged into `main`.
