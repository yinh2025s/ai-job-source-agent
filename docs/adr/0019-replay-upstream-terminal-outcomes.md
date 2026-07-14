# ADR-0019: Replay Upstream Terminal Outcomes

- Status: accepted
- Date: 2026-07-14

## Context

Bundle-v6 full replay previously assumed every result could be exported from its
final website or LinkedIn-company fields and had evidence scopes through S7. A
live runner may legitimately terminate after the S1-S3 process boundary when S2
cannot resolve a website. The frozen 15-company run therefore matched 15 records
but exported only 13; the integrity gate correctly failed.

## Decision

1. A record without a final website may enter scoped replay only when its trace
   declares an allowlisted production source and valid nonempty stage lineage.
   Legacy and unscoped exports retain their existing source requirement.
2. Replay reconstructs the original preferred website and source. A rejected
   preferred website is execution input, not a successful output or checkpoint.
3. The last stage in contiguous scoped lineage is the original execution
   boundary. Replay passes it as `stop_after` and requires exactly the scopes
   from the resume stage through that boundary.
4. Missing, reordered, duplicated, unknown-source, or non-contiguous lineage
   remains invalid. Replay never synthesizes downstream scopes or reduces the
   cohort limit to hide an unexportable record.
5. Summaries report filter-matched, selected, exported, and replayed counts
   separately. Zero executions cannot be described as zero selected records.

## Consequences

- S2 terminal failures can be reproduced without network access or invented
  downstream evidence.
- Full coverage still requires every source result to be selected, exported,
  executed, traced, and compared.
- Existing successful and explicitly filtered bundles retain prior behavior.

## Validation

- Unit tests cover an allowlisted company-only S2 failure, its three-stage
  terminal boundary, integrity counts, and summary count semantics.
- The frozen 15-company capture replays 15/15 records with 15 reproduced, zero
  fixture gaps, and zero mismatches. Its funnel remains 13/12/11/7.
- Final gates pass 1271 tests, 25/25 provider cases, 6/6 resolver cases, and 24
  adapters / 0 architecture issues.
