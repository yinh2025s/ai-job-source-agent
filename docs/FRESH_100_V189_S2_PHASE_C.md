# Fresh 100 `.189` S2 Phase C Report

## Scope And Score Policy

This report closes the first `.189` failure cluster: the fixed 49-record subset
that was a fresh-cohort `SYSTEM_GAP` at S2 in `.188`. It is an S2-only focused
gate. It does not replace or modify the immutable `.188` fresh-100 result of 11
audited Exact out of 100, and it does not claim that the remaining records are
valid external terminal outcomes.

The final code-frozen run is `/private/tmp/fresh100-v189-s2-focused-run7`. Its
complete replay is `/private/tmp/fresh100-v189-s2-focused-run7-replay`. Both are
preserved in
`artifacts/releases/fresh100-v189-s2-focused-20260720-run7.tar.zst` (SHA-256
`ad5d7344d187cdcd74b217140fd2448fd135afbea7c2acf8d7e6db2cd54bbb94`).

## Result

| Metric | `.188` S2 diagnostic | `.189` Phase C |
| --- | ---: | ---: |
| Records | 49 | 49 |
| Website resolved | 3 | 5 |
| S2 failed | 46 | 44 |
| Retry events | 413 | 290 |
| Mean elapsed | 8.957 s | 6.476 s |
| Worker exceptions | 0 in repeat diagnostic; 1 in original cold run | 0 |
| Missing S2 boundaries | 0 in repeat diagnostic; 1 in original cold run | 0 |

The final 44 failures are 38 `FETCH_FAILED`, 5 `HTTP_FORBIDDEN`, and 1
`WEBSITE_NOT_RESOLVED`. They remain unresolved evidence inputs, not
`EXTERNAL_BLOCKED` or `VERIFIED_NOT_FOUND` conclusions. The full three-route
pipeline must continue through provider search and External Apply even when S2
does not resolve a website.

Speculative candidates scheduled zero retries. The three remaining scheduled
retries belonged to evidence-backed, non-speculative requests. Known transport
failures now expose a separate `dns`, `connect`, `tls`, `http`, `read`, or
`timeout` phase; run7 observed TLS, HTTP, and timeout, while unit tests cover all
six phases including `IncompleteRead`.

## Website Identity Audit

All five selected websites passed current company-identity evidence checks:

| Company | Selected website | Audit |
| --- | --- | --- |
| Vectra AI | `https://www.vectra.ai` | Pass |
| Ivo | `https://www.ivo.ai` | Pass |
| Nisga'a Tek, LLC | `https://www.nisgaatek.com/` | Pass |
| ARUP Laboratories | `https://www.aruplab.com` | Pass |
| System One | `https://www.systemone.com/` | Pass |

An intermediate run exposed two unsafe low-evidence selections,
`stuller.org` and `teamroyal.org`. The final selection gate now rejects a
speculative-only domain unless current page content consistently confirms the
company identity. Both are rejected in run7. No selected S2 URL in the final
run is a known cross-company result.

This is a website audit only. S2 does not establish a provider tenant, opening
title, location, or Exact URL; those remain S5-S7 responsibilities.

## Replay And Offline Gates

- Focused S2 replay: 49/49 reproduced, 0 mismatch, 0 fixture gap, 0 integrity
  failure.
- Snapshot scope: 49/49 records have a complete S2 boundary; request counts are
  between 2 and 12.
- Python 3.12 unit suite: 2436 passed, 3 skipped.
- Provider benchmark: 25/25.
- Resolver benchmark: 6/6.
- Architecture gate: 44 native adapters, 0 issues.
- `git diff --check`: passed.

The record-by-record delta and machine-readable summary are in
`artifacts/evaluations/fresh100-v189-s2-focused-20260720-run7/`.

## Phase C Decision

Retain `.189`: it removes the worker/snapshot integrity defect, makes transport
diagnostics causal, prevents retry amplification on guessed domains, preserves
request identity for existing replay fixtures, and adds no accepted false
website in the final audit.

Do not interpret 5/49 as adequate product recall. The next cluster remains the
S5 candidate-discovery gap: use External Apply and provider-targeted search to
recover records whose website route is unavailable, then verify provider,
tenant, hiring relationship, title, location, status, and opening URL. The 44
unresolved S2 records stay in the closure matrix until a later unified,
code-frozen fresh-100 run assigns valid end-to-end terminals.
