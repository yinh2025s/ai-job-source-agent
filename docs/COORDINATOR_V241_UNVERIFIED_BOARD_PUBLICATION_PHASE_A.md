# v241 Unverified Board Publication - Phase A

## Causal Cluster

The valid `.239` SearXNG run contains five records across iClassPro, Caesars,
CHAMP, Fabric and Prophetic with the same executable trigger:

```text
S5 provider candidate selected
provider relationship_verified = false
S5 = partial / COMPANY_IDENTITY_AMBIGUOUS
top-level job_list_page_url still populated
```

CHAMP is the negative control. Its selected Greenhouse tenant belongs to another
company, so this is a product publication defect rather than a cosmetic status
issue.

## Common Code Path

S5 must retain the candidate in `PipelineContext`, portfolio and trace because
later inventory validation may still recover it. The defect is in final product
projection: `discovery_result_from_context()` hides some S7-rejected or stored
boards but does not hide an explicitly relationship-unverified S5 candidate
when execution stops before S7.

## Contract

- Internal `context.job_list_page_url`, provider identity, portfolio, selected
  candidate and trace remain unchanged.
- A product `DiscoveryResult.job_list_page_url` is not published when the
  current provider identity explicitly has `relationship_verified=false` or
  S5 ended as `COMPANY_IDENTITY_AMBIGUOUS`.
- The S5 stage evidence calls such a URL `candidate_job_board_url`, not
  `job_list_page_url`.
- Verified relationship boards and legacy first-party generic boards without an
  explicit provider rejection keep their existing publication behavior.
- No provider, tenant, title, location or S7 threshold changes.

## Acceptance

1. The five `.239` ambiguous records project a null public Job List while
   retaining complete diagnostic trace.
2. CHAMP's unrelated Greenhouse URL is not exposed as a product Job List.
3. An unverified candidate remains available inside the context for S6.
4. Verified provider boards still publish before an opening is found.
5. Exact results, verified no-match results and replay determinism do not
   regress.
6. Focused fixture tests, scoped replay/config tests, provider benchmark,
   resolver benchmark and architecture gate pass.

## Out Of Scope

This phase does not bootstrap provider-owned employer evidence, implement the
Hawaiian Electric SuccessFactors tenant variant, enable coordinator-v2, change
the extension or merge the LLM branch.
