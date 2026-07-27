# `.222` Detail Location Enrichment Phase C

## Scope

This phase validates one shared matcher defect across Lorum, Sunbird Software
and IMG: the correct exact-title opening existed, but its URL-bound structured
location did not reach the strict location gate. It does not claim to close
website discovery or transport failures.

## Code-Frozen Evidence

The source patch and each input were frozen before their run. All checkpoint,
completion, evidence, snapshot and replay roots were new.

| Run | Purpose | Result | Replay |
| --- | --- | --- | --- |
| `/private/tmp/fresh3-v222-detail-location-20260722-run1` | cold-input attempt during degraded network | 0/3; all stopped before S6 at retryable S2 timeout | 3/3 reproduced |
| `/private/tmp/fresh3-v222-detail-location-20260722-run2` | cold-input retry after endpoint preflight | Sunbird and IMG Exact; Lorum stopped at retryable S2 timeout | 3/3 reproduced |
| `/private/tmp/lorum-v222-opening-focused-20260722-run1` | matcher-focused run using previously verified first-party Lorum Website/Career evidence | Lorum Exact | 1/1 reproduced |

The Lorum focused run is not a cold-start recovery and must not be included in
Fresh100 aggregate recall. It exists only to exercise the generic opening
detail path that the transport failure prevented the cold run from reaching.

## Identity Audit

| Company | Provider / tenant | Opening | Selected title | Selected location | S7 |
| --- | --- | --- | --- | --- | --- |
| Sunbird Software | JazzHR / `sunbirdsoftwareinc` | `RfBS8vS11O/Cyber-Security-Analyst` | Cyber Security Analyst | Sioux Falls, SD | verified |
| IMG | JazzHR / `img` | `yc2AIb13kq/UX-Designer` | UX Designer | Indianapolis, IN | verified |
| Lorum | generic / `url:https://www.lorum.com/careers` | `/open-roles/devops-engineer-34274` | DevOps Engineer | New York, New York | verified |

All three selected URLs are specific openings. No wrong city, cross-company or
cross-tenant candidate reached S7. The LinkedIn and selected titles are exact;
the normalized locations are exact. Replay reported zero mismatch and zero
fixture gap for both accepted runs.

## Offline Gate

- 177 scoped tests passed across the opening matcher, JazzHR adapter,
  coordinator stage, checkpoint and run-configuration boundaries.
- Wrong-city JazzHR detail remains rejected.
- Existing remote and multi-location native-provider behavior remains intact.
- `git diff --check` passed.

The full suite is intentionally deferred until the backend behavior set is
frozen; development continues to use cluster-scoped tests.

## Decision

Close the structured detail-location cluster. Keep Lorum's cold-input S2
`NETWORK_TIMEOUT` open under transport/candidate-production analysis. Do not
add prose location extraction and do not broaden provider, tenant, title or
location identity contracts.
