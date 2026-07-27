# v241 Unverified Board Publication - Phase C

## Result

The product projection now distinguishes an internal provider candidate from a
public Job List.

When S5 explicitly ends with `COMPANY_IDENTITY_AMBIGUOUS` or the current
provider identity has `relationship_verified=false`:

- `PipelineContext`, portfolio, selected candidate, provider identity and trace
  retain the board for downstream processing and diagnosis;
- `DiscoveryResult.job_list_page_url` is null;
- S5 evidence names it `candidate_job_board_url`;
- pipeline status and typed reason remain partial;
- verified boards and generic first-party boards without an explicit identity
  rejection keep their existing behavior.

No provider, tenant, hiring relationship, inventory, title, location or S7
threshold was relaxed.

## Focused Live

The immutable `.241` five-record input has SHA-256:

```text
bbf01099b635ffc9ed1bfddccdf9eeecd706dcd15d0520069fd898f795994346
```

Result:

| Record | Public Job List | Opening | Terminal |
| --- | --- | --- | --- |
| iClassPro | verified Paylocity board | Exact `4331044` | success |
| Caesars | none | none | partial |
| CHAMP | none | none | partial |
| Fabric | none | none | partial |
| Prophetic | none | none | partial |

The current search responses did not reproduce all four historical ambiguous
provider candidates. Therefore this run proves that the verified iClassPro
result is preserved and no unsafe board was published, but it is not claimed as
a live reproduction of every `.239` candidate. The deterministic publication
boundary is covered by negative contract tests.

The new capture replayed 5/5 with zero reported mismatch or fixture gap. An
attempt to replay the old `.239` bundle under `.241` was correctly rejected:
the intervening `.240` iClassPro identity repair changed the request path and
left four old tape entries unconsumed. That cross-version attempt is not counted
as replay success.

## Gates

- scoped unit/integration tests: 656 passed;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture gate: 46 adapters, 0 issues;
- `git diff --check`: clean.

No full test suite, full Fresh100 live, Frozen100 or sealed holdout was run.

## Next Clusters

The audit separated two downstream implementation clusters:

1. Caesars, Fabric and Prophetic need a bounded provider-owned employer
   evidence bootstrap so S6 can verify the same tenant without weakening S5/S7.
2. The two Hawaiian Electric records need page-derived SuccessFactors
   `custom:hawaiianel` tenant projection instead of a shared provider root.

These remain separate because they have different triggers and code paths.
