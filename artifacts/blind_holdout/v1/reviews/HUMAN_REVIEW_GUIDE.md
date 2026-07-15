# Human Review Guide

Complete `human-review.json` independently. Do not open `codex-review.json` until the human
manifest has been signed. The signed human labels are the only authority for final metrics.

## Required Top-Level Fields

- `reviewer_id`: use `yinhuang` so it matches the SSH allowed-signer identity.
- `reviewed_at`: an ISO-8601 timestamp with timezone, for example
  `2026-07-15T10:00:00+08:00`.
- Do not change any provenance, source identity, expected URL, run ID, or digest field.

## Record Values

Every `null` field must be completed and `evidence` must contain at least one official public
HTTPS source.

- `hiring_relationship`: `same_entity`, `brand_parent`, `acquired_brand`,
  `alternate_employer`, `recruiter_client_undisclosed`, or `unknown`.
- `hiring_relationship_verdict` and `provider_tenant_verdict`: `verified`, `rejected`,
  `unknown`, or `not_applicable`.
- `title_verdict`: `exact`, `equivalent`, `mismatch`, `unknown`, or `not_applicable`.
- `location_verdict`: `match`, `compatible_remote`, `mismatch`, `unknown`, or
  `not_applicable`.
- `accessibility_verdict`: `publicly_accessible`, `closed_or_removed`, `access_blocked`,
  `unknown`, or `not_applicable`.
- `record_disposition`: `exact_public`, `verified_closed`, `no_public_opening`,
  `recruiter_client_undisclosed`, `external_blocked`, or `system_gap`.
- `eligible_exact_opening`: `true`, `false`, or the string `"unknown"`.
- `identity_verdict`: `verified`, `rejected`, `unreviewed`, or `not_applicable`.

For an `exact_public` record, verify and record all of the following:

1. `hiring_relationship_verdict`, `provider_tenant_verdict`, and `identity_verdict` are
   `verified`.
2. `eligible_exact_opening` is `true`.
3. Title is `exact` or `equivalent`; location is `match`, `compatible_remote`, or
   `not_applicable`.
4. Accessibility is `publicly_accessible` and `accessibility_checked_at` is an ISO timestamp.
5. Evidence contains exactly named kinds `official_public_opening`, `official_job_board`, and
   `hiring_entity_identity`. Opening and board evidence URLs must be the URLs being scored.

For a non-exact result, do not assume it is a system defect. Check whether the source posting
is closed, the company has no public opening, a recruiter client is undisclosed, access is
externally blocked, or the system missed an eligible public opening.

## Sign The Unchanged Bytes

After the JSON is complete and valid, run from the repository root:

```bash
awk '{print "yinhuang " $0}' ~/.ssh/id_ed25519.pub > artifacts/blind_holdout/v1/reviews/allowed_signers
ssh-keygen -Y sign -f ~/.ssh/id_ed25519 -n ai-job-source-human-review artifacts/blind_holdout/v1/reviews/human-review.json
```

The second command creates `human-review.json.sig`. Do not edit `human-review.json` after
signing. Codex will verify the signature and digest chain before generating final metrics.
