# Coordinator `.231` Ambiguous Website Identity Phase C

## Result

`.231` closes the ambiguous short-name Website publication defect identified
by the diagnostic `.230` Fresh100 run. It does not close the separate provider
tenant-probe provenance defect.

The resolver now rejects a short ambiguous single-token company candidate when
its identity rests only on self-referential domain, canonical URL or
organization metadata. Without a verified LinkedIn official site, bounded page
title or body identity is required. When LinkedIn supplies an official site
but the homepage cannot be verified, the candidate domain must independently
and exactly establish the ambiguous company identity.

## Focused Validation

The first Focus run rejected `https://focus.org/`, then exposed a second
collision from the public LinkedIn company page:
`https://www.cunseling-focus.com.ar/`. That candidate was also rejected after
the official-site unavailable-homepage gate was tightened.

The final clean run is preserved at:

`/private/tmp/focus-v231-live-run2`

Observed outcome:

- Website: not published;
- Career: not published;
- Job List: unverified partial `https://jobs.ashbyhq.com/focus`;
- Exact opening: not published;
- terminal S5 reason: `COMPANY_IDENTITY_AMBIGUOUS`;
- replay: 1/1 reproduced, zero mismatch and zero fixture gap.

The Ashby board remains visible only as an unauthorized candidate. It proves
that a provider tenant exists, not that the tenant recruits for the target
LinkedIn company. That provenance issue is the next `.232` cluster.

## Gates

The focused resolver, upstream, checkpoint and evaluation slice passes 204
tests. Coverage includes three canonical/organization-only collisions,
title/body positive evidence, a verified LinkedIn official-site positive,
the Focus search collision and an unavailable-homepage extended-domain
collision.

The conservative Fresh100 development projection remains unchanged at
29 Exact, 9 Verified No Match, 1 External Blocked and 61 unresolved. The
12-hour `.230` full run remains diagnostic only because machine suspension and
network gaps contaminated its terminal distribution.

## Safety

No wrong Website or opening URL was published in the final focused run. The
change adds no company, domain, job-ID or provider special case and does not
alter provider, tenant, title, location or S7 thresholds.
