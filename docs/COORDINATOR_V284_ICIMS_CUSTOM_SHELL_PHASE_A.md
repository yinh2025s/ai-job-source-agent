# `.284` iCIMS Custom-Shell Inventory Probe Phase A

## Decision

One provider-family defect qualifies for Phase B:

> A public iCIMS portal may serve a branded outer shell at `/` or
> `/jobs/intro`, then declare its actual public inventory in an
> `icims_content_iframe`. The current adapter recognizes only direct
> `/jobs/search` and detail URLs, while its page-aware path recognizes only
> Jibe. S5/S6 therefore classify these portals as `generic` and never execute
> the provider-declared title search.

This is one observable trigger and one production path across four independent
development companies. It is not a hostname-only rule.

## Development Evidence

All new evidence was collected on 2026-07-29 into the isolated, disposable
directory `/private/tmp/icims-shell-phase-a`. It is not part of a sealed or
blind cohort.

| Company | Outer shell | Declared inventory | Evidence terminal |
| --- | --- | --- | --- |
| Bluehawk | `careers-bluehawk.icims.com/` | same-host `/jobs/search`; exact `Data Scientist - Mid-Level`, `US-HI-` | expected Exact |
| Hyland | `careers-hyland.icims.com/jobs/intro` | same-host `/jobs/search`; exact `Treasury Analyst`, `Remote - U.S.` | expected Exact |
| Wheels Up | `careers-wheelsup.icims.com/jobs/intro` | same-host `/jobs/search`; exact `Flight Controller (Expression of Interest)`, `US-GA-Chamblee` | expected Exact |
| Room & Board | `jobs-roomandboard.icims.com/jobs/intro` | same-host `/jobs/search`; exact `Safety and Compliance Manager`, `US-MN-Golden Valley` | expected Exact |

The four outer pages contain the iCIMS runtime plus a unique same-origin
`icims_content_iframe` URL with `in_iframe=1`. Fetching that URL returns an
iCIMS search form whose action is a same-origin `/jobs/search` route. The
declared route works without preserving the opaque `hashed` query parameter,
so the canonical board remains query-free.

### Focused-live dependency found before Phase B closure

The code-frozen first `.284` provider probe identified all four boards and
returned all four exact opening URLs from complete title-filtered inventories.
It also showed `location=None` for every HTML-link candidate. The previously
proposed `.281` card-location parser had been reverted because its original
three-record cohort produced only two terminal recoveries; the third record
stopped at an independent title-ambiguity gate.

The four new development controls now reproduce the parser trigger through one
code path and each has one title-filtered target opening. They therefore meet
the three-company threshold independently of the rejected Steampunk case.
`.284` may restore only card-local location binding:

- location must follow an exact `Location`, `Job Location` or `Job Locations`
  field label inside the same `iCIMS_JobCardItem`;
- provider-coded `US-STATE-CITY` values are normalized to
  `CITY, STATE, United States`; an empty city remains
  `STATE, United States` rather than an opaque trailing delimiter;
- one card's location must never leak to another card or a standalone link;
- page filters, descriptions, navigation and adjacent text are not location
  evidence;
- no title-ambiguity or S7 threshold is changed.

Cretex and Highgate are controls, not claimed recoveries:

- Cretex declares a valid iCIMS shell and inventory, but its aggregate portal
  publishes openings on multiple child iCIMS hosts. That is a separate
  multi-portal tenant-continuity problem and must not be accepted by weakening
  same-tenant validation.
- Highgate's `/jobs/intro` shell exposes an HRSmart handoff and does not
  establish a same-tenant iCIMS inventory. The iCIMS adapter must not consume
  it as a successful board.

Historical records marked `blind_candidate_collection` are excluded from
implementation evidence and success calculations.

## Contract

Phase B may recognize a custom iCIMS shell only when all of the following are
true:

1. the outer page has a safe HTTPS single-label `*.icims.com` origin;
2. the path is public portal scope (`/` or `/jobs/intro`), never login,
   profile, onboarding or employee scope;
3. the page contains strong iCIMS iframe runtime evidence;
4. all duplicate declarations normalize to one iframe URL;
5. the iframe is HTTPS, credential-free, standard-port, same-origin and
   explicitly contains `in_iframe=1`;
6. the iframe response remains same-origin and contains a public iCIMS search
   form with a same-origin `/jobs/search` action;
7. the canonical board is the query-free declared search action.

The existing hosted-iCIMS adapter then owns title search, bounded pagination,
job-card parsing, title/location extraction and detail URL validation.

Phase B must not:

- recognize arbitrary `*.icims.com` root pages from hostname alone;
- accept `http`, credentials, non-standard ports or suffix-confusion hosts;
- follow multiple, cross-origin or undeclared iframe targets;
- classify onboarding/login/profile pages as public job boards;
- accept a child iCIMS opening host merely because the parent shell is iCIMS;
- weaken company, location, provider or tenant validation;
- add company, domain, title, location or job-ID special cases.

## Scope

Production ownership:

- `job_source_agent/providers/icims.py`
- `job_source_agent/job_board.py` only if the replay-safe board policy needs
  the new query-free public board shape
- `job_source_agent/checkpoint.py` for the semantic version bump

Test ownership:

- `tests/test_provider_icims.py`
- `tests/test_provider_registry.py`
- focused fixtures owned by the iCIMS provider tests

No registry, coordinator-v2, plugin or LLM architecture change is in scope.

## Acceptance

1. Four development controls produce typed, replay-safe iCIMS boards and
   their exact title/location opening.
2. Bluehawk, Hyland, Wheels Up and Room & Board use the same adapter path;
   there is no company-specific branch.
3. Highgate/HRSmart, onboarding, login, cross-origin iframe, multiple iframe,
   HTTP, credentials, non-standard port and weak-text-only controls are
   rejected.
4. Cretex may discover its shell/search board, but a child-host opening must
   not pass the same-tenant gate in this phase.
5. Existing hosted iCIMS and Jibe tests remain unchanged.
6. Focused live uses fresh checkpoint, snapshot, evidence, completion and
   output roots; strict replay requires zero mismatch and zero fixture gap.
7. URL audit requires zero unsafe URL, wrong location, cross-company and
   cross-tenant publication.

Focused success does not rewrite any frozen Fresh100 or Frozen100 score. After
Phase C, the full cohort is rerun only at the next approved code-frozen
measurement gate.

## Rollback

The change is isolated to page-evidence recognition of public iCIMS shells. If
the probe accepts a non-job portal, crosses origin/tenant, changes existing
hosted behavior or cannot replay deterministically, revert `.284` and retain
this evidence for redesign.
