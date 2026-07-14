# ADR-0006: Record Request-Aware Snapshot Outcomes

- Status: accepted
- Date: 2026-07-14

## Context

Snapshot replay currently identifies a response only by URL and records only
successful pages. This makes two live outcomes impossible to reproduce safely:

1. POST pagination to one endpoint collapses into one fixture because the request
   body is absent from fixture identity.
2. A URL that succeeds earlier and returns a terminal HTTP failure later replays
   only the earlier success because the failure is not captured.

The existing URL and HTML sanitizers also use separate sensitive-key lists. A key
such as `apikey` can therefore be redacted in HTML while remaining in snapshot
metadata and fixture fingerprints. Request identity, privacy, compatibility, and
failure selection must be frozen before parallel implementation.

## Decision

### Canonical Request Identity

1. A versioned request identity contains the HTTP method, sanitized normalized
   URL, an optional body fingerprint, and a small allowlist of normalized semantic
   headers. The same implementation is used by live snapshot capture, fixture path
   selection, replay materialization, and offline fetch.
2. GET without a body remains compatible with legacy URL-only fixture paths. A
   request that can change the response, including POST JSON/form pagination, gets
   a deterministic request suffix derived from the complete sanitized identity.
3. JSON, URL-encoded form, and bounded text-only multipart bodies are parsed
   structurally, sensitive fields are replaced by `[REDACTED]`, and only a SHA-256
   digest of canonical sanitized content is stored. Multipart boundaries remain
   case-sensitive and form fields are canonicalized independently of their wire
   order. File parts, filenames, unknown part headers, malformed boundaries, and
   oversized bodies are non-replayable. Raw request bodies and digests of
   unredacted credentials are prohibited.
4. Opaque bodies that cannot be safely parsed are marked non-replayable. Capture
   may record that classification, but must not persist the body or a reversible or
   credential-derived representation.
5. Semantic headers are allowlisted. Content negotiation plus sanitized
   `Origin`, `Referer`, and `X-Referer-Host` may affect identity when a public
   provider requires first-party portal provenance. `Authorization`, `Cookie`,
   proxy credentials, CSRF/session/token headers, browser storage, and unknown
   credential-bearing headers are excluded and never influence a persisted raw
   value.

### Shared Sensitive-Key Policy

1. Sensitive-key matching is case-insensitive and punctuation-insensitive, so
   `api_key`, `api-key`, and `apikey` share one policy. The shared policy is used
   for URL queries, JSON/form bodies, HTML attributes, metadata, manifests, and
   fixture fingerprints.
2. Empty sensitive values may remain empty; non-empty values become
   `[REDACTED]`. Sanitized and already-redacted requests must map to the same
   persisted identity.
3. Snapshot validation rejects any record, manifest, or materialized body that is
   not fully sanitized. It also rejects unsafe paths, symlinks, inconsistent
   digests, duplicate sequence numbers, and unsupported major schema versions.
4. Credential-bearing URL paths require an explicit provider endpoint contract.
   CEIPAL public inventory paths redact the tenant key segment before metadata,
   fixture-path, trace, or body persistence while retaining endpoint shape and a
   sanitized multipart fingerprint. Runtime-only tenant values may select the live
   endpoint but never become checkpoint locators or product output.

### Success And Failure Outcomes

1. Successful page records remain in `snapshots.jsonl`. New records carry their
   request identity and schema metadata while the reader continues to accept
   legacy v1 success records.
2. Terminal fetch failures are stored separately in `fetch-failures.jsonl`; they
   are not represented as HTML. A failure contains a monotonically ordered
   sequence, sanitized request identity, nullable HTTP status, canonical reason,
   retryability, bounded sanitized message, taxonomy version, and capture time.
3. `FetchError(message)` remains source-compatible and gains optional structured
   status, reason, retryability, and request identity. Retry logic consumes those
   fields when present and falls back to legacy message classification otherwise.
4. Schema-v2 page and failure records share one global sequence. Materialization
   compares both outcome kinds by the complete request identity and publishes only
   the highest-sequence terminal outcome: a later page supersedes an earlier
   failure, and a later failure supersedes an earlier page, redirect alias, and
   artifact. Legacy success records without sequence retain their compatibility
   behavior and are never assigned a guessed order.
5. Missing request signatures in legacy captures that contain conflicting bodies
   for one URL are reported as `unreplayable_request_signature`, not guessed from
   an arbitrary response. Privacy exclusions are likewise distinct from ordinary
   fixture misses.

### Stage Replay Semantics

1. Failure replay resumes from the first non-success stage and treats successful
   upstream typed outputs as authoritative handoffs. It must not feed those outputs
   back into an earlier resolver as lower-trust candidate hints.
2. A resolver rerun is a separate mode and must distinguish an authoritative prior
   output from an ordinary preferred candidate. Company-specific expected
   transitions cannot hide a changed entity or request identity.

### Compatibility And Ownership

1. Snapshot replay schema and failure-bundle schema move to v2. Legacy v1 success
   snapshots remain readable; unknown future major versions fail closed.
2. Request identity and structured error contracts are main-line owned shared
   interfaces. Snapshot capture, replay materialization, provider behavior, and
   focused tests may be implemented in disjoint worktrees only after this ADR is
   accepted.
3. Full live capture and the frozen cohort remain serialized on the main line.
   Authenticated LinkedIn extension acceptance is independent and may remain
   deferred; automated replay does not replace it.

## Consequences

Positive:

- POST pagination and other response-affecting requests replay deterministically.
- Terminal HTTP/network failures become reproducible without inventing HTML.
- Credentials cannot leak through a spelling mismatch between sanitizers.
- Failure replay preserves stage and entity semantics instead of changing inputs.

Costs and limits:

- Existing v1 captures cannot recover request bodies or failures that were never
  recorded; affected cases require a new focused capture.
- Opaque credential-bearing requests can be classified but may remain
  intentionally non-replayable.
- Sequence-aware failure replay adds explicit mode and schema complexity.

## Validation

- Contract tests cover sensitive-key spelling variants, credential-bearing path
  redaction, sanitized identity stability, GET/query variants, POST
  JSON/URL-encoded/multipart pagination, semantic-header allowlisting, file-part
  rejection, and opaque-body privacy exclusion.
- Snapshot tests cover success and 403/429/5xx/timeout failure round trips,
  success-then-failure selection, legacy v1 reads, unknown-version rejection,
  corruption, path safety, and absence of raw credentials.
- Provider tests cover CEIPAL's safe omission of known empty presentation
  parameters while rejecting host, path, tenant, extra-parameter, fragment, or
  HTTPS changes.
- Failure replay tests cover authoritative upstream handoff reuse and explicit
  `unreplayable_request_signature` classification.
- Main-line integration runs focused replay first, then all offline gates and one
  serialized frozen live cohort. The Chrome extension gate remains separately
  deferred until the user resumes it.
