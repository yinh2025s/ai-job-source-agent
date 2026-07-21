# Fresh100 `.210` Stored Provider Identity Phase B

## Contract Repair

Stored first-party handoff evidence authorizes an exact provider, tenant and
canonical board. It does not establish that an opaque provider tenant is the
legal or public hiring-entity name. `.210` therefore preserves verified S3
hiring evidence and keeps the tenant only in `ProviderIdentity`.

When no verified upstream identity exists, this path may construct a
same-entity identity only for the LinkedIn source company. It does not infer a
parent or alternate employer from a tenant string. Existing verified
parent/brand evidence is preserved rather than downgraded.

## Targeted Verification

- 202 discovery/checkpoint/replay tests passed.
- Provider-locator coverage includes Paylocity UUID/slug, Ashby tenant and
  Workday parent/site forms.
- A bounded multi-board regression preserves an upstream parent/brand identity
  through `JOB_BOARD_PORTFOLIO_INCOMPLETE` while retaining the exact provider
  tenant.

## Migration Replay

The three `.209` `JOB_BOARD_PORTFOLIO_INCOMPLETE` records were replayed from
their immutable scoped tapes under `.210`:

- WalkMe: reproduced.
- OneApp: reproduced.
- Heritage Companies: expected cross-version identity correction.

Heritage remains partial at the same stage and reason, with the same Website,
Career page, Paylocity provider, tenant and canonical Job Board. Only the bad
top-level and typed hiring entity changes from the Paylocity locator to
`Heritage Companies`. The strict outcome gate correctly labels that changed
field as a migration mismatch; it is diagnostic evidence, not same-version
acceptance.

## Next Gate

Freeze `.210` and run a small clean cohort containing the Heritage stored-board
path plus unaffected portfolio records. Use new checkpoint, completion,
evidence, snapshot and output roots. Then replay the full focused cohort under
the same commit and require zero mismatch, fixture gap and tape divergence.
Do not rerun the 2500+ full suite before this focused gate.
