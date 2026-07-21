# Fresh100 `.208` Replay Query Projection Phase B

Date: 2026-07-21

## Root Cause

The frozen `.207` live run completed 100 records, but full replay stopped before
execution for NYC Department of Social Services. S5's canonical generic board
was `https://cityjobs.nyc.gov/jobs`; S6 used the official title-filtered
projection `https://cityjobs.nyc.gov/jobs?q=DEVOPS+ENGINEER` as the final public
Job List display URL. Replay preflight compared that mutable display URL to the
S5 primary identity as exact URLs and classified the record as ambiguous.

After allowing the safe projection in a migration diagnostic, all 100 records
executed with zero fixture gap. The diagnostic exposed a second deterministic
projection defect: two Versana postings and one B&D Industries posting retained
the same verified typed hiring identity in replay, but the compatibility field
`hiring_entity_name` became null. S3 always created
`HiringIdentityEvidence`; it only projected the top-level name when an alternate
entity was explicitly resolved. Later provider routes could therefore populate
the field inconsistently for otherwise identical same-entity records.

## Frozen Contract

1. A final Job List URL may differ from the S5 primary only when the provider is
   `generic`, the normalized scheme/host/path are identical, the primary has no
   query, and the final URL adds a nonempty query without a fragment.
2. The source result must be S7 `verified`; hiring must be verified and provider
   relationship verified.
3. Provider, opening and selection identities must all name provider `generic`
   and the exact S5 canonical board.
4. Candidate, opening and selection opening URLs must all equal the published
   Exact URL.
5. Typed providers, path changes, board conflicts, rejected/unavailable
   identities and incomplete identity chains remain ambiguous.
6. Every successful S3 publishes the effective hiring entity name together with
   `HiringIdentityEvidence`, including same-entity relationships. It does not
   change relationship classification or authorize any URL.
7. Adapter version advances to `2026-07-21.208`; `.207` artifacts remain
   immutable and are never relabeled as same-version replay.

## Offline Evidence

- 141 replay/checkpoint tests passed after the query-projection change.
- 161 replay/upstream/pipeline/checkpoint/crash tests passed after deterministic
  S3 projection.
- A `.208` migration replay over the immutable `.207` snapshots selected,
  exported and executed 100/100 records with zero fixture gap and no preflight
  ambiguity. Its outcome differences are expected cross-version field
  projections and are not an acceptance result.

## Focused Gate

Freeze `.208` and run five Fresh100 records from clean roots:

- NYC Department of Social Services - DEVOPS ENGINEER
- Versana - DevOps Engineer - Raleigh
- Versana - UX Designer
- B&D Industries, Inc. - Project Manager
- B&D Industries, Inc. - Human Resources Manager

All five must complete live. Every source Exact that remains publicly available
must retain the same canonical company/provider/tenant/board/opening/title and
location chain. Every successful S3 must publish a nonempty top-level hiring
entity equal to its typed hiring evidence. Same-version replay must select and
execute 5/5 with zero mismatch, fixture gap, tape divergence or boundary
failure. Any URL, tenant, title or location regression rejects the change.

The focused gate validates replay correctness only. It cannot change the
Fresh100 24/100 live score or close the rejected OneApp/Home Depot/Crosby
recall cluster.
