# v261 Career Transport Reservation - Phase C

## Result

`.261` closes the scheduler and terminal-attribution defect defined in Phase A.
It does not claim a recall improvement.

The Career transport scope keeps the configured total dispatch limit and
reserves one quarter of a bounded budget, capped at six dispatches, from
speculative routes when Career search is enabled. `blind_ats` is the only
current speculative phase. Evidence-backed phases may use the reserve, so
unused capacity is not stranded.

Reservation rejection is a typed, non-retryable
`SPECULATIVE_ROUTE_BUDGET_RESERVED` scheduling event. It remains visible in
trace but cannot masquerade as global `FETCH_BUDGET_EXHAUSTED`.

## Focused Live

Frozen input:

- `/private/tmp/v261-career-reservation-input.json`
- SHA-256:
  `34ec8d2e48c24f6fe6178ac87b9fe13d70b7cade17cc34c54ba6bba88a2d36a4`

Final artifact:

- `/private/tmp/v261-career-reservation-run2`

| Company | Old blind/search dispatches | `.261` blind/search dispatches | Final |
| --- | --- | --- | --- |
| The Naked Market | 11 / 0 | 5 / 6 | `CAREER_PAGE_NOT_FOUND` |
| Motorola Solutions | 12 / 0 | 7 / 6 | `CAREER_PAGE_NOT_FOUND` |
| Daedalus | 12 / 0 | 6 / 6 | `CAREER_PAGE_NOT_FOUND` |
| DataAnnotation | 14 / 0 | 8 / 6 | `CAREER_PAGE_NOT_FOUND` |

All four retain the 24-dispatch ceiling. All four automatic outcome replays
pass. No wrong URL, company, provider or tenant is published.

## Interpretation

The Phase A cluster is closed because blind ATS no longer starves the later
search phase and no internal reservation rejection pollutes the final reason.
The focused result is still 0 Career / 0 Job List / 0 Exact.

The remaining four failures are a new causal cluster:

```text
verified Website
-> bounded first-party and blind ATS candidates rejected
-> legacy Career search runs all six queries
-> zero valid candidates produced
-> CAREER_PAGE_NOT_FOUND
```

Any next repair must investigate why valid candidates are not produced. It
must not increase the total Career request limit or relabel this as transport
starvation.

## Post-Closure Causal Audit

The four records do not remain one implementation cluster:

- The Naked Market has no public recruiting entry in the captured homepage,
  bundle or search evidence.
- Motorola Solutions exposes an observed `/en_xp/about/careers.html` link.
  A queryful canonical homepage prevents stored navigation evidence and
  `en_xp` is incorrectly interpreted as region `xp`. This is a singleton
  locale/evidence case.
- Daedalus exposes `/employment` and the exact target at
  `/employment/17`. The first-party numeric detail verifier does not accept
  this route family. This is a singleton inventory shape.
- DataAnnotation exposes a first-party `Apply now` handoff and singular
  `Career` surface, but not an opening-specific URL. This is a singleton
  action/surface case and cannot be promoted to Exact.

Across all four, Bing RSS returned 120 semantically drifted results and no
correct Career URL. Bing HTML returned Turnstile pages that the current
challenge classifier misses; DuckDuckGo challenge detection worked. Correcting
Bing challenge classification would recover 4/4 diagnostics but is expected
to recover 0/4 Career URLs, so it is not the next recall workstream.

S5 also ran for all four. External Apply was unknown, targeted provider search
produced zero ATS candidates, and all tenant probes failed verification.
Provider filters did not discard a correct captured candidate. The S5
`not_run` projection after an attempted empty portfolio is an observability
defect, not a recall repair.

No post-closure cause reaches the required three-company implementation gate.
The locale, first-party employment detail and generic Apply cases remain open
for third independent examples. The next action is another backend diagnostic
cohort, not a company-specific patch.

## Gates

- Related tests: 218/218
- Provider benchmark: 25/25
- Resolver benchmark: 6/6
- Architecture validation: 47 native adapters, 0 issues
- Focused replay: 4/4
- Scoped `git diff --check`: passed
