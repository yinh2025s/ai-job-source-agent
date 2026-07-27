# v240 Display Descriptor Identity - Phase C

## Trigger

The `.239` SearXNG A/B exposed a deterministic identity regression. A verified
homepage could be rejected when a LinkedIn display name appended a marketing
descriptor after a spaced `-` or `|`, even when the LinkedIn company slug
identified only the prefix brand.

The repair is intentionally narrow:

1. split only a spaced display separator;
2. normalize the prefix through the existing legal-form rules;
3. use the prefix only when it exactly equals the LinkedIn company slug tokens;
4. otherwise retain the complete display name.

This keeps `Bosch - Home` with slug `bosch-home` as a complete brand identity
while allowing `Acme - Workflow Automation Software` with slug `acme-inc` to
use `Acme` for parent-page detection.

This is a regression guard, not a new causal-cluster closure. Only iClassPro and
DSV in the two development cohorts satisfy the exact trigger, below the
three-company implementation threshold. It is retained because it restores an
already audited Exact that `.239` regressed and adds a negative contract test;
no aggregate Fresh100 projection is changed.

## Focused Live

Using `.240`, SearXNG and fresh isolated artifact roots:

- iClassPro: verified Website, Career, Paylocity tenant and Exact DevOps
  Engineer opening `4331044`; replay 1/1.
- DSV: retained first-party `dsv.com`, reached its first-party Job List and
  stopped honestly at `OPENING_DISCOVERY_INCOMPLETE`; replay 1/1.

No company, title, location, provider, tenant or opening identity was relaxed.

## Verification

- focused transport/search/resolver tests: 218 passed;
- replay/config/resolver regression tests: 283 passed;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture gate: 46 adapters, 0 issues.

The full multi-thousand-test suite and full Fresh100 live were deliberately not
run for this bounded regression guard.
