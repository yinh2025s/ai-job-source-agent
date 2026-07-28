# Coordinator `.277` Value-Shaped Credential Redaction - Phase A

Date: 2026-07-28
Input evidence: Fresh100 current `.275` cold artifact
Decision: **qualify one snapshot-body privacy cluster**

## Scope

This is a read-only causal audit of the existing `.275` capture. It does not
open a new cohort, run live traffic, inspect sealed holdouts, use authenticated
LinkedIn state or inspect the isolated LLM branch.

The raw artifact contains six distinct Google browser-key values across six
independent company records and one AWS access-key-ID-shaped value from a
seventh record. None of these values entered Git.

## Causal Split

The matching files must not be treated as one cluster merely because they
contain credential-shaped text.

| Path | Independent companies | Shared code path | Decision |
| --- | ---: | --- | --- |
| Embedded credential-shaped literal in stored HTML/JavaScript | Aperia, City of Lubbock, The Home Depot, QXO, Hays + Sons, Wolfe | `snapshot.sanitize_snapshot_body` -> `_sanitize_snapshot_text` | **Qualified: 6 companies / 6 expected privacy recoveries** |
| Extracted Google script URL copied into trace/checkpoint/completion | Crawford Thomas Recruiting | raw `Page` -> `extract_links` -> trace serializers | Rejected: one company |

The selected cluster has one observable trigger: a high-confidence public
runtime credential value is embedded in text under a field shape not covered by
the existing name-based sanitizer. It has one production code path and affects
six independent companies.

The Crawford trace leak is a different path. It remains an explicit residual
and must not be bundled into `.277`.

## Root Cause

`_sanitize_snapshot_text` removes values when their surrounding field name is
recognized. It does not recognize every framework-specific field spelling and
does not independently reject high-confidence credential value shapes.

The observed fields include provider-specific Google/developer/maps/site-key
names, escaped configuration, hidden inputs and one AWS access key ID. Adding
those field names would repeat the failed heuristic pattern. The stable
contract is the credential value format itself.

## Frozen Contract

`.277` changes only snapshot-body sanitation:

1. Redact exact Google browser API-key values with the `AIza` prefix and the
   fixed credential length.
2. Redact exact AWS access-key-ID values with the `AKIA` or `ASIA` prefix and
   the fixed credential length.
3. Apply the value rule after existing structured/text sanitation so plain
   HTML, JavaScript strings, escaped JSON and structured string values share
   one behavior.
4. Preserve surrounding field names, URLs, HTML and noncredential map
   configuration.
5. Remain deterministic and idempotent.
6. Do not sanitize arbitrary identifier-like text or broaden query, identity,
   provider, tenant, title, location or S7 behavior.

Ownership is limited to:

- `job_source_agent/snapshot.py`
- `tests/test_snapshot.py`
- product adapter version in `job_source_agent/checkpoint.py`
- Phase C and governance documents owned by main

No trace, checkpoint, completion, provider, matcher or scheduler code is in
scope.

## Acceptance

1. Synthetic tests cover the Google and AWS fixed shapes in direct text,
   escaped JSON, hidden inputs and embedded URLs.
2. Near-miss prefixes and lengths remain unchanged.
3. Sanitization is idempotent and preserves neighboring semantic fields.
4. Running the production sanitizer over every key-bearing `.275`
   snapshot-body source yields zero Google/AWS credential-shape matches for all
   six selected companies.
5. Existing snapshot, request-identity and replay-integrity tests pass.
6. Provider 25/25, resolver 6/6, architecture 48/0 and `git diff --check` pass.
7. No new live batch is started. A future capture must prove that a newly
   generated replay capsule is privacy-clean without post-hoc modification.

## Rollback

If any selected body retains a credential shape, any near-miss semantic value
is changed, or snapshot/replay tests regress, revert `.277` as one isolated
snapshot-sanitizer change. Do not compensate with company/domain exceptions or
post-hoc archive rewriting.
