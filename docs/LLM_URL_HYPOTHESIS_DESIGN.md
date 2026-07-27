# LLM URL Hypothesis Design

## Purpose

The optional LLM candidate-reasoning layer may propose public HTTPS URLs when
deterministic search does not produce the right company or recruiting-system
candidate. A proposal is a zero-trust hypothesis, not verified evidence and not
a successful result.

The feature flag remains **off by default**. Promotion requires frozen,
evaluator-owned evidence and the existing safety gates.

## Zero-Trust Flow

```text
LinkedIn input
  -> deterministic discovery
  -> optional LLM query and URL hypotheses
  -> frozen candidate portfolio
  -> URL safety checks and real fetch
  -> company identity verification
  -> hiring-relationship verification
  -> provider / tenant / board verification
  -> opening verification
  -> final identity gate
```

An LLM URL enters the same candidate portfolio as other untrusted discovery
outputs. It cannot bypass URL normalization, network retrieval, provider
adapters, company matching, tenant isolation, opening-state checks, or the final
identity gate.

## Call Budget

The per-company budget remains at most two LLM calls:

1. One planning call may produce bounded search queries and URL hypotheses.
2. One ranking call may order only the frozen candidate portfolio.

Timeout, malformed output, budget exhaustion, or provider failure falls back to
the deterministic path. No third repair call is allowed.

## Prohibited Evidence

Model output must never be treated as proof of:

- company identity;
- parent, subsidiary, brand, or hiring relationship;
- ATS provider or tenant ownership;
- job-board ownership;
- opening existence, status, title, or location;
- an exact-opening success.

Those facts require first-party pages, provider responses, or other evidence
already accepted by the deterministic verification pipeline.

## Frozen Evaluation Contract

`CandidateReasoningABObservation.frozen_llm_hypothesis_urls` records the exact
canonical URL pool emitted by the model for an observation. It defaults to an
empty tuple so older frozen observations remain valid.

The causal label `url_hypothesis_recovery` is valid only when all of the
following are true:

- `llm_plan_used` is true;
- the baseline top candidates miss the reference candidate;
- the treatment top candidates contain the reference candidate;
- the final verified website equals the evaluator-owned reference website;
- the reference candidate URL is present in the explicit frozen LLM hypothesis
  URL pool.

The legacy `invented_or_modified_treatment_url_count` metric now means a
treatment candidate URL that appears in neither the frozen search-evidence pool
nor the frozen LLM-hypothesis pool. A legitimate frozen hypothesis is therefore
auditable without being mislabeled as search-evidence URL invention. A URL
outside both pools remains a zero-tolerance gate failure.

Reference URLs and labels remain evaluator-only data and must never be copied
into planner, ranker, search, provider, or resolver requests.

## Run 008 Evidence

The one authorized fixed-development run is recorded in
`docs/LLM_URL_HYPOTHESIS_RUN008_REPORT.md`. The model added four correct Top-3
URL hypotheses while the frozen search pool recovered zero reference URLs, but
none of the four completed deterministic verification. Candidate uplift was
22.22 percentage points and strict causal recovery was 0/18, so promotion
failed.

This result preserves the design boundary: candidate generation is not product
success. The feature remains off and the route is retained only as an
experimental fallback. No Fresh100, blind holdout, main merge or second paid
prompt experiment is authorized by this result.
