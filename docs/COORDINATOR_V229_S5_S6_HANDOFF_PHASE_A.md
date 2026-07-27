# Coordinator `.229` S5-S6 Handoff And Typed Budget Phase A

## Causal Cluster

Lubbock, Montana and the earlier Dechert run all reached a verified Job Board,
then produced terminal caller-deadline fetch entries during S6. Lubbock exposes
two concrete shared-contract defects:

1. its strict GovernmentJobs board identity is runtime-only, so the S5
   checkpoint is deleted and the S6 phase spends its reserved window rerunning
   S5;
2. the provider adapter reclassifies a typed `FetchError` from its display
   string, turning `COMPANY_TIME_BUDGET_EXHAUSTED` into
   `PROVIDER_FETCH_FAILED`. Live therefore runs four generic fallbacks while
   replay preserves the typed budget reason and stops after one request.

This is not part of provisional Website discovery. That path already produced
a verified company/provider/tenant Job Board.

## Contract Changes

- A GovernmentJobs board is checkpointable only when the host is exactly
  `www.governmentjobs.com`, the path is exactly `/careers/{tenant}`, the tenant
  is a lowercase public slug and the identifier equals that path tenant.
- The locator stores only public board identity. S6 must still fetch current
  official inventory and S7 must still validate the exact opening.
- Provider fetch classification must preserve a typed `FetchError.reason_code`.
  String taxonomy is a fallback only when no typed reason exists.
- Generic transport failure may map to `PROVIDER_FETCH_FAILED`; company and
  cooperative budget reasons, network taxonomy, HTTP state and replay failures
  must not be erased.
- A typed native budget failure terminates fallback exactly as it already does
  in the matcher. Replay must not ignore unconsumed entries.

## Parallel Ownership

- Main: `job_source_agent/job_board.py`,
  `job_source_agent/providers/base.py`, schemas/version and governance docs.
- GovernmentJobs line: `job_source_agent/providers/governmentjobs.py` and
  `tests/test_provider_governmentjobs.py`.
- Cross-provider line: three provider adapters with existing string
  reclassification plus isolated provider tests; it does not touch
  GovernmentJobs or shared files.

## Acceptance

1. At least three distinct GovernmentJobs tenants produce strict replay-safe
   canonical boards; wrong host, port, path, identifier and cross-tenant values
   fail closed.
2. S5 checkpoint save/load retains the board and portfolio, so an
   `opening_match` resume does not rerun S5.
3. Typed company/fetch budget errors remain identical across at least three
   provider adapters and never degrade to `PROVIDER_FETCH_FAILED`.
4. Lubbock focused live gives S6 the reserved phase instead of rerunning S5.
5. Same-version scoped replay has zero mismatch, fixture gap, extra request and
   unconsumed tape entry.
6. Provider, tenant, company, title, location and S7 safety remain unchanged.

## Rollback

Remove the GovernmentJobs replay locator policy and shared reason helper, then
restore `.228`. Existing `.228` checkpoints are version-incompatible and will
be ignored; no persisted opening or credential migration is required.
