# Fresh 100 `.189` S2 Root-Cause Contract

## Scope

This Phase A audit covers the 49 fresh-cohort records whose `.188` closure is a
`SYSTEM_GAP` at S2 (`FETCH_FAILED`, `NETWORK_TIMEOUT`, or
`WEBSITE_NOT_RESOLVED`). It does not change code or reinterpret the frozen
`.188` score. The immutable baseline remains in
`artifacts/evaluations/fresh100-v188-cold-20260720-run1/`.

Two independent S2-only cold runs used separate checkpoint and snapshot roots:

- `/private/tmp/fresh100-v188-s2-diag-run1`
- `/private/tmp/fresh100-v188-s2-diag-run2`

Both runs resolved the same 3/49 companies (`Ivo`, `Nisga'a Tek, LLC`, and
`System One`) and failed the same remaining 46. The failure labels varied for
seven records, but the success set did not. This is therefore a repeatable
system defect, not evidence of a single transient network outage.

## Observed Failure Cluster

| Signal | Run 1 | Run 2 |
| --- | ---: | ---: |
| S2 success | 3 | 3 |
| S2 failed | 46 | 46 |
| Retry events | 413 | 414 |
| Mean elapsed seconds | 8.957 | 9.331 |
| Maximum elapsed seconds | 13.512 | 13.517 |

Run 1 ended with 33 `FETCH_FAILED`, 6 `NETWORK_TIMEOUT`, 6
`HTTP_FORBIDDEN`, and 1 `WEBSITE_NOT_RESOLVED`. Run 2 ended with 38
`FETCH_FAILED`, 6 `NETWORK_TIMEOUT`, 1 `HTTP_FORBIDDEN`, and 1
`WEBSITE_NOT_RESOLVED`. The seven changing labels demonstrate that the current
terminal reason often describes the last attempted candidate rather than the
stage's causal outcome.

## Root Causes

### 1. Speculative candidates consume the retry budget

S2 places mechanically generated domains in its fast verification wave. A
candidate can score above the likely `.com` simply because a long LinkedIn
display name contributes more matching tokens. For example, the resolver tried
multiple `.ai`, `.io`, `.co`, `.org`, `.app`, and `.tech` variants for
`Loveland Innovations`; each failed guess was eligible for the same retry as a
LinkedIn-declared or search-supported website.

The result is retry amplification: about eight retry events per record before
the resolver reaches stronger evidence sources. A retry can improve transport
reliability for a known endpoint, but it cannot add identity evidence to a
guessed domain.

### 2. Evidence collection runs after speculative verification

For ordinary LinkedIn company slugs, the resolver verifies guessed and
slug-derived domains before loading the LinkedIn company page or website search
evidence. When the shared caller deadline is consumed by those probes, the
resolver never gets a fair chance to obtain an official outbound URL or a
search-supported candidate.

### 3. The stage has no phase reservation

The outer S2 worker budget is 25 seconds, while the inner fetch deadline uses a
12.5-second reservation. Candidate verification, LinkedIn evidence, and search
share that single deadline without phase quotas. A slow TLS handshake or two
retryable guesses can starve every later route. Merely increasing the timeout
would increase latency without correcting the scheduling defect.

### 4. Some transport failures escape the typed fetch contract

`Fetcher._fetch_live()` catches `HTTPError`, `URLError`, timeout, socket, and
`OSError`, but `http.client.IncompleteRead`/`HTTPException` can escape from
`response.read()`. The fresh run observed this for Ken Garff Automotive Group.
Because `RetryingFetcher` and `SnapshottingFetcher` catch only `FetchError`, the
worker aborted and no terminal S2 snapshot boundary was written.

### 5. Transport diagnostics are too coarse

TLS EOF, DNS, connect, HTTP response, and body-read failures can collapse to
`FETCH_FAILED`. This prevents reliable failure-cluster analysis and makes the
last candidate's failure look like the reason the company was not resolved.

## `.189` Repair Contract

The implementation must satisfy all of the following without company, URL,
tenant, or benchmark-specific branches:

1. Normalize known public transport exceptions, including incomplete body
   reads, into typed `FetchError` values with sanitized request identity.
2. Persist a terminal failure snapshot for every begun request that ends in a
   known transport failure; programming errors must still fail loudly.
3. Record a stable transport phase (`dns`, `connect`, `tls`, `http`, `read`, or
   `timeout`) separately from the public reason code.
4. Retry evidence-backed candidates only. Speculative guesses receive one
   bounded attempt and candidate switching takes precedence over retrying the
   same unverified host.
5. Schedule direct input, cached/LinkedIn official evidence, and search evidence
   ahead of speculative expansion, while preserving strict homepage identity
   verification before selection.
6. Reserve enough stage budget for at least one evidence-collection route and
   one evidence-backed verification route. Exhaustion must report the stage
   cause, not whichever guess happened to run last.
7. S2 failure must remain non-authoritative for downstream candidate discovery;
   S5's External Apply and provider-search routes must still run.

## Focused Acceptance

Phase B/C for this cluster requires:

- transport, retry, snapshot, worker, and resolver unit tests pass;
- injected `IncompleteRead` becomes a typed, replayable terminal fetch outcome;
- no begun snapshot request lacks a terminal page/failure record;
- the fixed 49-record S2 diagnostic completes without worker exceptions;
- retry amplification decreases and no speculative candidate is retried;
- every selected website still has current positive company-identity evidence;
- all 49 records receive a complete S2 boundary and scoped replay has zero
  mismatch and zero fixture gap;
- no result from the diagnostic is written into the frozen `.188` artifacts.

Improved S2 resolution is expected, but focused S2 success alone does not alter
the fresh-100 score. Only a later code-frozen unified run may update the closure
matrix.

## Rollback

`.189` must be reverted if it lowers identity strictness, selects an unverified
website, starves evidence-backed candidates, creates snapshot gaps, or regresses
the frozen cohort. A latency or recall gain is not sufficient to retain a
correctness regression.
