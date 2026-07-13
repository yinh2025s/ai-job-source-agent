# ADR-0007: Bind Replay And Checkpoints To Run Configuration

## Status

Accepted, 2026-07-14.

## Context

The same company input can produce different candidate schedules and outcomes when career
candidate limits, fetch budgets, search limits, sitemap policy, or search timeout change.
Previously, stage checkpoints and batch completions were keyed only by company input. Live
results also omitted these settings, while offline replay silently used composition defaults.
An equal final failure signature therefore did not prove that replay reproduced the original
execution, and a changed configuration could restore incompatible cached work.

Successful replay had a second blind spot: the outcome gate compared status and failure stage,
but not the verified website, career page, job list, opening, or provider identity.

## Decision

- `DeterministicRunConfig` schema `1.0` contains only the behavior-affecting `AgentConfig`
  fields. `max_career_candidate_fetches=None` is expanded to its effective value.
- `BatchExecutionConfig` schema `1.0` separately contains whole-company and website budgets,
  fetch timeout/retry policy, render mode/budget, verification limit, and offline mode. It
  contains no paths, URLs, headers, cookies, or credentials.
- Validation rejects unknown fields, incompatible versions, booleans used as integers,
  negative or excessive budgets, and non-finite or excessive timeouts.
- `input_fingerprint` remains the domain identity of a company/posting input.
- Pipeline `execution_fingerprint` hashes the input fingerprint with the canonical agent run
  digest and keys stage checkpoints. Batch completion identity additionally includes the batch
  execution digest, so a short timeout or different render/fetch policy cannot poison a later run.
- Result schema `2.1` and trace carry the canonical agent configuration. Evaluation summary
  carries both agent and batch execution configurations; baseline comparison requires both
  digests to match.
- Failure bundle schema `3` reconstructs the source `AgentConfig`, records provenance, and
  rejects mixed or mismatched configurations. Legacy records require the explicit
  `--legacy-run-config composition-defaults` option and are marked `legacy_defaulted`.
- Replay input remains a domain record and does not duplicate run configuration. Fetcher
  paths, cookies, tokens, headers, snapshot roots, and CLI environment are excluded.
- Successful outcome replay compares canonical verified URL identity and provider as well as
  pipeline status. Full-outcome bundles can include success, partial, failed, and unsupported
  records; failure-only bundles remain available for focused diagnosis.

## Consequences

Changing deterministic behavior settings safely misses old checkpoints and batch completions.
Old schema artifacts remain historical evidence but are not silently reused. Evaluation
baselines with different budgets are no longer directly comparable. Replay manifests are
self-describing without containing machine-local paths or authentication material. URL-aware
success gates can now detect a green run that navigated to a different or incorrect target.
