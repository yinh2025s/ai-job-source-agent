# Coordinator `.278` JWT Value Redaction - Phase A

Date: 2026-07-28
Input artifact: `/private/tmp/fresh100-current-v277-cold-20260728-run1`
Decision: **qualified for one bounded offline Phase B**

## Scope

The `.277` Fresh100 measurement captured JWT-shaped capability values from
four independent public company pages through the same
`sanitize_snapshot_body -> _sanitize_snapshot_text` path:

- TreeHouse Foods: Q4 public-site session tokens;
- Tyler Technologies: public search API token;
- Pitch Aeronautics: Supabase public-role token;
- QXO: embedded content API tokens.

The audit decoded only JWT header and claim structure and recorded token
digests. It did not publish token values or signatures. All observed values
have a signed three-segment JWT shape and time-bounded capability claims.
Whether a source labels the token public does not make it suitable for a
shareable replay artifact.

This is separate from Crawford Thomas Recruiting. Crawford's Google browser
key is already removed from snapshot bodies by `.277`; it survives because a
raw extracted candidate URL is copied into trace, checkpoint and completion
sinks. That one-company serialization residual is outside this phase.

## Shared Trigger And Code Path

The accepted cluster is:

```text
trigger:
  bounded three-segment JWT value remains in sanitized page body

code path:
  sanitize_snapshot_body
    -> _sanitize_snapshot_text
    -> SnapshotStore content-addressed blob
    -> scoped replay tape

expected recoveries:
  four independent hosts become credential-shape clean before hashing
```

The cluster meets both the three-company and three-recovery thresholds.

## Frozen Contract

Phase B may add one bounded value-shape redactor with these constraints:

1. Match exactly three base64url segments whose decoded header and payload are
   JSON objects.
2. Require an algorithm-bearing header and at least one capability/time claim
   in the payload; ordinary dotted strings and malformed base64 remain intact.
3. Replace the complete JWT value with `[REDACTED]`.
4. Apply after existing structured/text field sanitation and before snapshot
   hashing.
5. Remain independent of company, domain, issuer, claim value and field name.
6. Be idempotent and preserve all surrounding HTML, JavaScript and JSON text.
7. Do not change request identity, provider, tenant, discovery, title,
   location or S7 contracts.
8. Treat an explicit URL-encoded assignment separator (`%3d`) as a value
   boundary while preserving that separator; other encoded text is not a
   boundary.

JWT values are opaque credentials. The sanitizer does not preserve decoded
claims or create a usable synthetic token.

## Replay Safety

Focused replay must cover all four captured hosts. Acceptance requires:

- zero remaining JWT shapes in newly sanitized bodies and converted tapes;
- snapshot metadata, blob hash and byte-count validation pass;
- no privacy exclusion;
- every source outcome either reproduces or fails closed with an explicitly
  attributable missing-token request identity;
- no new network request during focused replay.

If a provider requires the original JWT in an unsanitized request-identity
position, Phase B is rejected. The implementation must not weaken request
identity or restore a usable token.

## Tests

Required unit coverage:

- valid JWT redaction in HTML, JavaScript and structured JSON;
- idempotence;
- multiple JWT values;
- malformed segment/base64/JSON near misses;
- missing `alg` header;
- payload without capability/time claims;
- surrounding semantic text preservation;
- existing Google/AWS and public geographic State behavior unchanged.

Required focused artifact gate:

- recapture the historical four-host corpus through production
  `SnapshotStore`;
- run `replay_snapshots.py`;
- scan capture and replay output for JWT, Google and AWS value shapes;
- run snapshot, request-identity and replay-bundle local tests.

## Rollback

The change is isolated to snapshot value sanitation and its tests. If any
focused host cannot replay without restoring a usable token, revert the JWT
redactor and keep the `.277` artifact privacy failure explicit.

No live cohort, Frozen100 run, sealed holdout, authenticated plugin or LLM
branch is authorized by this phase.
