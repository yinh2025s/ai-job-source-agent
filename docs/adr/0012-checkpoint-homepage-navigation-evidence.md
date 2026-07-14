# ADR-0012: Checkpoint Verified Homepage Navigation Evidence

Status: Accepted

Date: 2026-07-14

## Context

Live S1-S3 and S4-S7 can execute in separate company processes. S2 already fetches and verifies
the selected company homepage, but its stage output previously retained only the selected URL.
S4 therefore fetched the exact same URL again before extracting career navigation. In the `.75`
frozen-30 trace, 25 verified S2 selections were exact S4 homepage dispatches: 25 of 107 S4
transport calls, or 23.4 percent.

The process-local page cache cannot cross this boundary. Persisting a page, raw HTML, visible
text, authenticated browser data, or diagnostic trace as executable input would violate the
typed-handoff and privacy boundaries in ADR-0003, ADR-0005, and ADR-0006.

## Decision

S2 may publish a `HomepageNavigationEvidence` value through its existing stage checkpoint.
The handoff is bound to the pipeline execution fingerprint and is not a separate cross-run
cache.

The payload contains only:

- an exact, canonical, public HTTPS `homepage_url`;
- one to eight unique, canonical, public HTTPS candidate URLs whose URL itself has career or
  registered ATS semantics; and
- the handoff schema version.

Every URL must use a global non-local host and the standard HTTPS port. Credentials, fragments,
queries, controls, HTML-like content, secret-shaped content, overlong URLs, duplicate candidates,
unknown fields, and oversized payloads are rejected. Query-bearing links are omitted rather than
redacted. The handoff never stores HTML, link text, titles, attributes, timestamps, request
identity, headers, bodies, cookies, tokens, browser state, LinkedIn payloads, or page artifacts.

Evidence is created only from the `Page` that produced the ultimately selected, S2-verified
homepage. Overrides, LinkedIn-only acceptance, unselected candidates, failed candidates, trace,
and snapshots cannot produce the handoff. If no URL-semantic candidate remains after filtering,
S2 publishes no handoff.

S4 consumes the handoff only when `homepage_url` exactly matches the resolved website URL. Its
candidates receive the same first-party scheduling weight as live homepage navigation, but retain
distinct provenance. Every candidate must still be fetched and pass the existing career/provider
validation. A successful typed candidate lets S4 skip the duplicate homepage transport. Missing,
mismatched, malformed, incompatible, exhausted, or unsuccessful evidence restores the original
homepage fetch and discovery path; S4 does not infer evidence from trace or snapshots.

The internal context contract advances to `1.2` and the stage checkpoint schema to `1.4`. Old or
corrupt checkpoints safely miss; there is no implicit migration. Changes to this payload require
an explicit handoff schema change and checkpoint invalidation.

## Consequences

- The common verified-homepage path can remove one S4 dispatch without weakening candidate or
  result verification.
- Opaque links whose career meaning exists only in visible text are deliberately not persisted.
  S4 fetches the homepage through the legacy path when typed URL evidence cannot succeed.
- The handoff expires with its execution checkpoint. A longer-lived company evidence cache would
  require a separate retention, erasure, and data-boundary decision.
- Resolver, stage orchestration, candidate scheduling, transport budgeting, and provider adapters
  keep separate ownership. No provider or company-specific rule is introduced.
