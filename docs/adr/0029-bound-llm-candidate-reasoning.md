# ADR-0029: Bound LLM Candidate Reasoning To Untrusted Discovery Leads

- Status: accepted
- Date: 2026-07-20

## Context

The deterministic website resolver is deliberately conservative. On unfamiliar
companies it can fail before verification because a legal suffix, descriptive
brand tail, acronym, alias, or parent/brand relationship prevents the correct
official domain from entering the candidate pool. Adding more mechanical domain
guesses does not provide source evidence and can increase same-name false
positives.

An LLM can help formulate diverse search queries and compare ambiguous public
search results, but it is not an authority for company ownership, hiring
relationships, provider tenants, job inventory, title/location identity, or
opening status. Treating model text as evidence would violate the existing
resolver, provider, and S7 contracts.

The initial scope is therefore limited to the causal `G` class: the correct
website candidate was not produced, or source-backed candidates were produced
but deterministic ranking could not allocate a reasonable Top K. Transport
failures, unknown posting employers, provider inventory, opening matching, and
final publication are explicitly outside this decision.

## Decision

### Role And Pipeline Position

The seven public stages remain unchanged. A new optional candidate-preparation
application service sits behind the S2 website-resolution boundary and produces
only untrusted website leads. `WebsiteResolutionStage` constructs a frozen,
allowlisted eligibility context from S1 identity, visible External Apply, stored
verified provider evidence, replay mode, and typed budget/transport state. It does
not pass the mutable `PipelineContext`, arbitrary `source_trace`, or raw stage
payloads to the reasoning layer.

The current product runs S2 before S5, so the resolver cannot know whether a
future provider search will succeed. Phase B must therefore extract one bounded
deterministic direct/provider preflight whose immutable output is reused by S5;
it must not execute a second full provider search. The physical LLM hook remains
inside website resolution after all current deterministic website candidates
have failed selection and immediately before the resolver publishes its retained
failure. It prepares the portfolio in this order:

1. collect existing deterministic direct website evidence, External Apply, and
   provider candidates;
2. verify any direct provider candidate through the existing registry and
   hiring-relationship contract;
3. if a verified website, verified provider board, or official External Apply
   route is already sufficient, continue the existing route without an LLM call;
4. otherwise evaluate the typed LLM eligibility predicate;
5. ask a query planner for at most three search queries;
6. execute those queries through the existing bounded search transport;
7. apply deterministic URL safety and blocked-domain filters before model
   ranking;
8. ask a ranker to order at most ten immutable search candidates by ID;
9. send at most the Top 3 existing candidate URLs to
   `CompanyWebsiteResolver` for its current fetch, redirect, parking, region,
   and company-identity verification.

The prepared provider portfolio is reused by S5; it is not rediscovered merely
because S2 ran. A verified provider or External Apply route can complete without
waiting for optional LLM work. The implementation must not introduce a second
full provider search or a new public stage.

The LLM path is not a success path. Its strongest output is an ordered list of
candidate IDs. Only the existing resolver can publish Website evidence, and
S5-S7 retain their current provider, tenant, inventory, title, location, status,
and result-identity responsibilities.

### Provider-Neutral Contracts

Production orchestration depends on four interfaces, not on a model vendor:

```python
class CompanyQueryPlanner(Protocol):
    def plan(self, request: QueryPlannerRequest) -> QueryPlannerDecision: ...

class CompanyCandidateRanker(Protocol):
    def rank(self, request: CandidateRankerRequest) -> CandidateRankerDecision: ...

class LLMReasoningClient(Protocol):
    def complete(self, request: StructuredLLMRequest) -> StructuredLLMResponse: ...

class LLMDecisionStore(Protocol):
    def load(self, key: LLMDecisionKey) -> LLMDecisionRecord | None: ...
    def save(self, record: LLMDecisionRecord) -> None: ...
```

`CompanyQueryPlanner` and `CompanyCandidateRanker` own schema validation and
fail-closed conversion from a generic structured client. The composition root
injects the client and store. Tests use a fake client; no unit, provider,
resolver, architecture, or replay test may require a real model or paid API.

The planner request contains only bounded, sanitized public company evidence:
normalized company name, LinkedIn company slug, a truncated public company
summary, source job title/location, known company industry/location, and the
source plus typed rejection reason of existing candidates. It never contains
cookies, tokens, headers, browser state, complete HTML, raw snapshots, personal
profiles, user names/emails, API keys, or unredacted trace payloads.

The planner response uses schema version `1` and contains only:

- normalized company name;
- core brand tokens;
- legal/descriptive suffixes;
- possible aliases;
- at most three bounded query strings with purpose
  `official_website`, `career_site`, or `provider_site`;
- `ambiguous`;
- fixed reason codes.

Planner output cannot contain a URL or a tool action. URL-like output, unknown
keys, unknown reason codes, excessive arrays/text, malformed JSON, or a timeout
rejects the whole decision and returns to the deterministic baseline.

Every executed search result becomes immutable `CandidateEvidence` with a
generated candidate ID, normalized safe URL, bounded title/snippet, source,
query ID, and source rank. Candidate IDs are unique within one input digest.
Search documents and snippets are untrusted data, not instructions.

The ranker response uses schema version `1` and may reference only candidate IDs
present in its request. It can assign `high`, `medium`, or `low`, cite only
provided evidence IDs, use fixed reason codes, and set `ambiguous`. It cannot
return, rewrite, complete, or synthesize a URL. One unknown or duplicate
candidate ID, unknown evidence ID, malformed value, or omitted input candidate
invalidates the whole response. Confidence changes verification order only.

No request asks for or persists chain-of-thought. Structured reason codes and
bounded decision metadata are the complete explanation surface.

LLM-ordered leads retain ordinary `search_evidence` provenance. Ranking cannot
promote them to direct, official, stored, provider, or relationship evidence.

### Eligibility And Fallback

The LLM is eligible only when the feature is enabled, no sufficient direct route
exists, no forbidden condition exists, and at least one of these typed states is
true:

- no source-backed website candidate exists;
- every website candidate is `speculative_guess`;
- multiple same-name search results remain deterministically ambiguous;
- the company name has legal, descriptive, acronym, alias, or parent/brand
  signals and existing candidates did not verify;
- multiple source-backed candidates exist but none reaches the current identity
  threshold.

It is forbidden when a unique website already verified, a provider board plus
hiring relationship already verified, official External Apply is usable, the
current cause is DNS/TLS/timeout/403/budget transport, S6 or S7 has begun, the
posting employer is undisclosed/ambiguous, or a verified no-match/closed
decision already exists.

Eligibility is a pure deterministic function over typed portfolio state. It
does not inspect free-form trace text. Disabled, ineligible, malformed, timeout,
client, schema, unsafe-output, and fixture-incompatible outcomes are typed,
observable, non-terminal advisory outcomes. They cannot replace the pipeline's
pre-existing failure reason. There is no automatic LLM retry or recursive query
planning.

Runtime advisory failure codes are isolated from the pipeline's terminal reason
registry: `TIMEOUT`, `PROVIDER_ERROR`, `MALFORMED_JSON`, `SCHEMA_INVALID`,
`UNKNOWN_CANDIDATE_ID`, `OUTPUT_URL_FORBIDDEN`, `INPUT_POLICY_REJECTED`,
`DECISION_STORE_ERROR`, and `CALL_BUDGET_EXHAUSTED`. Replay uses the separate
`LLM_DECISION_FIXTURE_MISSING`, `LLM_DECISION_FIXTURE_INCOMPATIBLE`,
`LLM_DECISION_FIXTURE_CORRUPT`, and `LLM_DECISION_REPLAY_DIVERGENCE` taxonomy.

Turning the flag off performs no LLM-store lookup, model call, additional search
request, client construction, candidate reorder, or trace mutation beyond
recording the ordinary deterministic configuration.

### Security And Privacy

Before the ranker sees a candidate, its URL must pass the existing public URL
contract: HTTPS, no credential, standard port, no fragment, no sensitive query,
no control characters, no private/local address, and no blocked search or social
host. Redirect and final-domain safety remain resolver responsibilities.

Prompt construction serializes untrusted evidence as bounded JSON data and
labels it as non-instructional. Phrases such as `ignore previous instructions`,
HTML-like text, tool syntax, and URLs inside snippets have no executable meaning.
The client has no browser, shell, fetch, provider, or persistence capability.

Decision persistence uses only sanitized structured fields. Company summaries,
titles, and snippets are normalized, length-limited, and stripped of emails,
phone-like values, credential-bearing URLs, and control characters before both
transmission and storage. Logs and traces contain decision ID, status, reason
code, counts, duration, model metadata, prompt/schema versions, token usage, and
digests, not raw prompts, full responses, HTML, cookies, authorization data, or
browser state.

Model output is never company, relationship, provider, tenant, or opening
evidence. Search snippets cannot establish a hiring relationship even when the
model cites them with high confidence.

### Budget And Deterministic Configuration

Per company, the hard limits are one planner call, one ranker call, three search
queries, ten ranker candidates, and three resolver verification candidates. The
total model-call limit cannot exceed two. One monotonic total deadline contains
explicit planner, search, and ranker sub-budgets. Their sum cannot exceed the
total, and a live configuration reserves at least one executable second for the
ranker. Each provider call receives the smaller of its phase budget and the
current total-deadline remainder; an adapter cannot hide a shorter transport
timeout. Search receives only its own phase remainder. The LLM cannot consume
the reserved S6 opening budget. Failure does not retry.

The following fields are behavior identity and must enter the canonical run
configuration digest:

- `enable_llm_candidate_reasoning`;
- `llm_provider`;
- `llm_model`;
- `llm_prompt_version`;
- `llm_timeout`;
- `llm_planner_timeout`;
- `llm_search_timeout`;
- `llm_ranker_timeout`;
- `llm_max_candidates`;
- `llm_max_calls_per_company`.

The stabilized deterministic schema is `1.6` only when the feature is
enabled. Provider, model, and prompt values are bounded public ASCII identifiers,
never credentials or endpoints. With the flag disabled, canonical serialization
continues to emit the existing schema `1.4` payload and digest so the rollback
baseline has identical checkpoint identity, requests, and candidate order.
Enabled schema `1.5` remains readable only for historical fixture replay and
derives bounded replay phases from its old total timeout. An enabled
configuration without an
injected client and compatible decision store fails composition validation; it
does not silently select a default vendor. Enabling reasoning also requires the
parallel candidate-discovery contract so deterministic direct/provider preflight
cannot be bypassed. Paths and secrets stay outside the run configuration.

### Decision Record, Cache, And Replay

`LLMDecisionRecord` schema `1` contains:

- record key, execution fingerprint, and decision kind (`query_plan` or
  `candidate_rank`);
- input evidence digest;
- normalized company identity digest;
- provider and model ID;
- prompt, decision-schema, and adapter versions;
- sanitized structured request and response;
- candidate IDs, query IDs, and candidate-evidence digest;
- duration, prompt/completion/total token usage, creation time, status, and fixed
  success/failure code.

Its key includes normalized company identity, input evidence digest, provider,
prompt version, model ID, decision schema, adapter version, and decision kind.
Failure records may be retained for the current run's audit but are never negative
cache hits. Cross-run decision cache reuse is limited to a successful candidate
ranking suggestion; query-plan records remain scoped live/replay evidence in the
first implementation. The store never caches a verified Website, Job Board, Exact
opening, closed/no-match result, or other negative terminal.

The filesystem implementation follows the repository's store rules: independent
schema, SHA-256 key partitioning, per-key process lock, strict JSON without unknown
fields/non-finite values, rejected symlinks, same-directory temporary file, file
and directory `fsync`, atomic replace, and isolated worktree/run roots.
Missing/corrupt/incompatible data is a safe miss during live execution. A decision
must be persisted successfully before its ranking is used; store failure restores
deterministic ordering. A corrupt store never authorizes a candidate.

Live capture writes a scoped decision record. Decision records are not HTTP
snapshot outcomes; a replay bundle carries separate `llm-decisions.jsonl` and
`llm-decision-manifest.json` artifacts and advances the scoped bundle schema.
Replay uses only a frozen decision fixture and a replay store; it must never
construct or contact a real model client. Input
digest, company identity, decision kind, prompt version, provider/model,
decision schema, or adapter mismatch is typed `LLM_DECISION_INCOMPATIBLE`, not a
cache miss followed by a live call. Missing, extra, corrupt, incompatible,
unexpected, and unconsumed decision fixtures fail the replay gate with distinct
typed codes. A source run with no LLM call requires no fixture,
and historical flag-off bundles stay compatible. A replay that attempts a new
unrecorded call fails rather than silently falling back and claiming reproduction.
Request-aware web snapshots and LLM decision fixtures remain separate evidence
streams with one shared execution identity.

### Rollout And Measurement

Implementation status on 2026-07-22: post-experiment causal stabilization is
implemented on the isolated branch. Product
live capture writes digest-bound JSONL/manifest artifacts; failure and
full-outcome bundles freeze selected invocation decisions; fixture-only product
replay constructs no model client and enforces complete single-pass consumption.
Historical flag-off bundles stay on their existing schema and require no decision
fixture. The evaluator refuses to count `llm_calls=0`, independent live-network
variance, or an unadopted decision as recovery. Planner source recall@10 and
ranker conditional recall@3 use separate frozen-pool contracts. The 18-record
fixed eligible-G development input is unchanged. The
input contains no reference website URLs; evaluator-only labels are stored
separately and must not be loaded while constructing planner/ranker requests.
The user approved DeepSeek API use on 2026-07-21. Phase D pins
`deepseek-v4-flash` in non-thinking JSON mode behind the provider-neutral client,
with no retries. Any future development runner is capped at 30 calls and USD
0.05, although run 007 remains the one completed formal rerun and a second paid
formal A/B is not authorized. Capture never loads evaluator labels and
same-version replay never constructs the DeepSeek client.

Phase A is this read-only design and ADR. It changes no runtime behavior.

Phase B implements provider-neutral interfaces, strict schemas, fake client,
feature flag/configuration, decision records/store, product live artifact capture,
failure/full-outcome bundle binding and fixture-only replay contracts.
Synthetic company names and reserved example domains are used for contract tests.
The flag remains disabled and no real model is called.

Phase C builds a fixed development-subset A/B harness from eligible `G` records.
Baseline and reasoning variants receive identical original inputs and identical
captured search results. Correct URLs, closure matrices, and manual annotations
are withheld from planner/ranker input and prompt examples. The harness reports
candidate recall@3, resolver-verified website recall, unsafe/unknown IDs,
wrong-company selections, calls, tokens, failures, and P50/P95 latency. Offline
fixtures validate reproducibility but do not by themselves claim model uplift.

Before Phase D, the proposed provider, model, fixed record count, maximum calls,
and estimated cost must be reported to and approved by the user. Phase D freezes
code/prompt and makes one limited real run on the fixed `G` development subset
with new decision/checkpoint/snapshot/output roots, followed by manual identity
review and same-version replay. It does not run the full fresh-100 cohort.

The first rollout passes only if candidate recall@3 improves by at least 25
percentage points, at least 40% of eligible development records recover the
correct website candidate, verified website errors and model-invented URLs are
zero, cross-company/provider/tenant output is zero, replay is 100%, flag-off is
unchanged, mean calls are at most two per company, and cost/failure/P50/P95 are
reported. Recovering one or two companies does not pass.

An anonymous LinkedIn company-page enrichment rejection is not by itself a global
transport root cause: the already supplied public company slug remains usable for
alternate candidate discovery. The stage may therefore project that explicitly scoped
failure to a typed `G` condition. Transport failures from search, DNS/TLS, candidate
verification, rate limits, login walls or other phases continue to take priority and
remain ineligible.

If this gate fails, the feature remains disabled. Prompt tuning may not add
company-specific examples, expected URLs, closure data, or hand-authored
answers. The next decision is to improve search sources, provider-first evidence,
or an authoritative company-data service, not to overfit the prompt.

Only a passing development experiment may prepare a previously unseen blind
holdout measuring candidate recall@3, verified website recall, end-to-end Exact,
Exact precision, wrong URLs, model cost, and P95 latency.

## Consequences

The design may improve source-backed candidate recall while preserving the
deterministic truth boundary. It adds configuration, persistence, cost, latency,
and a new external failure mode, all isolated behind an off-by-default flag and
typed fallback. It cannot repair transport failures, provider inventory gaps,
ambiguous employers, or opening matching, and it cannot turn model confidence
into a published URL.

## Validation

Phase B must cover strict planner/ranker schemas, unknown candidate/evidence IDs,
URL-like planner output, malformed JSON, timeout/client failure, prompt injection,
privacy redaction, same-name industry/location conflicts, legal/acronym/alias
positives, parent/product/news negatives, high-confidence resolver rejection,
provider/tenant isolation, flag-off equivalence, frozen-decision replay, and
prompt/model/schema/adapter invalidation. Architecture validation must also prove
that vendor clients are injected only by composition and are not imported by the
resolver, stages, providers, or S7.
