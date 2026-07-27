# `.220` Provider Candidate Audit

## Question

After closing the Paylocity detail bootstrap contract, determine whether the
remaining development traces contain another provider-family cluster where a
source-backed specific opening is rejected only because the adapter cannot
bootstrap its board.

## Paylocity Search Preflight

Three current development companies were queried serially with exact
provider-specific Bing RSS searches:

- Loveland Innovations / DevOps Engineer;
- iClassPro / DevOps Engineer;
- Heritage Companies / Corporate Human Resources Manager.

All three responses returned HTTP 200, but none returned a Paylocity result.
The engine ignored the site constraint and emitted unrelated general results.
Therefore adding Paylocity to the fixed provider query rotation has an observed
recovery expectation of 0/3 and is rejected. The current general role query may
still produce a Paylocity detail lead, but its variance is not repaired by
spending another site-filtered slot.

## Rejected-Candidate Audit

The `.209` Fresh100 trace contains no second source-backed provider-detail
cluster. Its remaining `provider_not_listable` URLs are speculative tenant
probes such as trailing-hyphen Lever slugs and `b&d-industries` guesses across
Lever, Ashby and Workable. They do not carry provider-published employer,
tenant or opening evidence and must remain rejected.

## Decision

- Keep the Paylocity detail bootstrap.
- Do not add a Paylocity search-family slot based on this preflight.
- Do not normalize malformed guessed tenant slugs into accepted candidates.
- Do not label `provider_not_listable` itself as a causal cluster.
- Select the next backend repair only from source-backed evidence shared by at
  least three independent companies.
