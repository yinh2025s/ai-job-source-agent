# v268 Haley Marketing Tape Body Redaction - Phase C

## Decision

Do not accept `.268`.

Body sanitation removed raw HMG tickets while preserving the expected live
business outcomes, but automatic replay diverged on Kavaliro's search-entry
POST. `.268` artifacts remain diagnostic evidence and are not the final
provider closure.

## Focused Live

Isolated run:

`/private/tmp/v268-haley-focused-run1`

Live produced:

- Website: 3/3;
- Career: 3/3;
- verified Job List: 3/3;
- Exact: 1/3;
- evidence-backed no match: 2/3.

Kavaliro remained the correct S7 Exact. Madison-Davis and Top Prospect Group
remained verified provider-inventory no-match outcomes.

## Replay Failure

Automatic scoped replay stopped with:

`outcome tape has 2 unconsumed entries; first remaining request: POST https://jobs.kavaliro.com/index.smpl`

The sanitized HMG search-entry page used the inert `t` placeholder, while the
live form-body fingerprint still included the rotating raw `t`. The resulting
request identities did not match.

## Next Action

`.269` owns the exact HMG search-entry request-identity fix and a new isolated
live/replay acceptance. No `.268` result is promoted to final closure.
