# Coordinator `.236` Phase A: stage-v1 provider reservation

## Causal cluster

Five independent companies in the current S5 refresh share one trigger:

- Website and hiring identity complete successfully.
- S4 consumes the complete Career/Job Board discovery child-process window.
- S4 publishes `COMPANY_TIME_BUDGET_EXHAUSTED` at about 62 seconds.
- S5 never runs, despite parallel candidate discovery being enabled.

Affected companies are Caesars Entertainment, Splashlight, Pitch Aeronautics,
Nisga'a Tek and FOTOMILL Studios.

The configured `provider_search_reserve_seconds` is currently wired only when
the proposed `coordinator_v2` engine is selected. The production `stage_v1`
candidate portfolio therefore receives no cooperative S4 reservation.

## Contract

When parallel candidate discovery is enabled, the Career stage must stop
fetching before the shared discovery deadline and preserve the configured
provider-search reserve for S5. This applies to the current `stage_v1` engine as
well as the proposed coordinator.

- S4 reserve exhaustion is typed `FETCH_BUDGET_EXHAUSTED`.
- ApplicationRunner continues to S5.
- S5 may use the remaining time for External Apply, provider search and
  Website/Career portfolio candidates.
- Total company time, fetch count and opening reserve do not increase.
- The proposed `coordinator_v2` remains disabled by default.
- No company, provider, tenant, title, location or S7 gate changes.

## Acceptance

- Composition configures the S4 reservation for `stage_v1` whenever parallel
  candidate discovery is enabled.
- Disabled parallel discovery configures no reservation.
- Existing retrying-fetcher reservation tests remain green.
- The five-company focused live executes S5 provider-search work after S4
  reaches its cooperative reserve.
- Replay preserves the resulting typed boundary.
