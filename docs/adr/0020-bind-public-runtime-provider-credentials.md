# ADR-0020: Bind Public Runtime Provider Credentials

- Status: accepted
- Date: 2026-07-14

## Context

Some first-party career pages expose a public browser credential for a hidden
inventory API. FirstSpirit e-Spirit CaaS is one such provider: the page contains
the API origin, tenant, project, collection, bearer credential, detail-link
prefix, and country filters. The credential is intended for browser use, but it
must still be treated as sensitive runtime material.

## Decision

1. The adapter is selected only from one unambiguous jobs-specific configuration
   on a public HTTPS career page. API hosts must exactly match the bounded
   `*-caas-api.e-spirit.cloud` suffix; identifiers are bounded single path
   segments. Lookalikes, IPs, userinfo, non-default ports, query strings,
   fragments, controls, and duplicate configurations are rejected. The exact
   snapshot sentinel `[REDACTED]` is accepted only as a non-secret request
   placeholder; redacted-looking variants are rejected.
2. The bearer credential is held only in a bounded, process-local handoff from
   page identification to the immediately following inventory request. It never
   enters a URL, `JobBoard.identifier`, checkpoint, trace, log, exception,
   snapshot, or committed fixture. Such boards are not replay-safe locators;
   replay reruns S5 from sanitized page evidence.
3. Credentialed requests are origin-pinned. Redirects must not forward
   `Authorization` to another origin. A redirect or response outside the exact
   configured API origin is an unsupported provider variant.
4. The adapter calls only the constructed `get_jobs` aggregation endpoint. It
   sends a bounded title-filtered query and page size, uses country IDs observed
   on the same page, and enforces page, row, duplicate, count, and response-size
   caps. Missing or malformed pagination evidence is incomplete, never empty.
5. HAL responses must contain one typed result with consistent `data` and
   `meta[0].count`. Candidate detail URLs are constructed from the first-party
   detail prefix and relative job route, then constrained to the exact career
   origin and locale `/job/` path.
6. Request identity excludes `Authorization` and snapshot sanitization redacts
   bearer and API-key spellings. Offline fixtures use a synthetic credential and
   fictional records. Scoped replay may send the exact redaction sentinel because
   it is excluded from request identity and only selects an already captured
   response; raw live payloads are never committed.

## Consequences

- Bosch-shaped inventories can be queried without company-specific branches.
- A resumed S6 cannot silently depend on a credential stored in a checkpoint.
- Credentialed APIs require a stricter redirect transport than ordinary public
  page fetches.

## Validation

- Provider tests cover config ambiguity, SSRF lookalikes, redacted credentials,
  endpoint construction, bounded pagination, HAL/schema failures, duplicate
  records, detail URL escape, runtime handoff consumption, and trace redaction.
- Transport tests prove that credentialed fetches reject cross-origin redirects
  before forwarding `Authorization`.
- A focused live run may use the public page credential in memory, but persists
  only sanitized request identity and aggregate trace fields.
