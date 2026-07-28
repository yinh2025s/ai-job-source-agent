# `.281` iCIMS Job-Card Location Binding Phase A

## Decision

One provider-family defect qualifies for Phase B:

> Traditional hosted iCIMS search pages expose a location, title and opening
> link inside the same `iCIMS_JobCardItem`, but `ICIMSAdapter` extracts only
> the link and title. Every HTML-link candidate therefore has
> `location=None`, and S6 rejects otherwise exact openings.

This is one observable trigger, one production parser and three independent
companies with three expected Exact recoveries.

## Evidence

| Company | Target | Current official row |
| --- | --- | --- |
| Elderwood | `RN - Registered Nurse`; Ticonderoga, NY | opening `36227`, `US-NY-Ticonderoga` |
| Steampunk | `UI/UX Designer`; McLean, VA | opening `6317`, `US-VA-McLean` |
| Great Day Improvements | `Sales Manager`; Savannah, GA | opening `24730`, `US-GA-Savannah` |

The evidence comes from three separately captured live cohorts:

- Elderwood:
  `/private/tmp/frozen100-current-v280-cold-20260728-run1`
- Steampunk:
  `/private/tmp/fresh100-current-v278-cold-20260728-run1`
- Great Day Improvements:
  `/private/tmp/v273-diagnostic-run1`

All three search responses use the same hosted iCIMS structure:

```html
<li class="iCIMS_JobCardItem">
  <div class="header left">
    <span class="field-label">Job Location</span>
    <span>US-VA-McLean</span>
  </div>
  <a class="iCIMS_Anchor"
     href="/jobs/6317/ui-ux-designer/job?in_iframe=1">
    <h3>UI/UX Designer</h3>
  </a>
</li>
```

The current `_ScriptParser` stores `job_links` as `(href, title)`.
`_candidate_from_html_link` consequently constructs every candidate without a
location. The current traces then share:

- provider `icims`;
- exact or strong title candidates;
- `candidate_location=None`;
- `location_unverified_candidate_rejected`;
- zero verified detail enrichment.

The issue is not a detail-fetch budget problem. The location already exists in
the provider's official, title-filtered search inventory and is bound to the
opening link by one job-card container.

## Scope

Phase B may change only:

- `job_source_agent/providers/icims.py`;
- `tests/test_provider_icims.py`;
- a provider-owned generic fixture only if the existing inline test boundary
  is insufficient.

The implementation must:

1. recognize only bounded hosted-iCIMS job-card containers;
2. bind location text from the same card as the opening link;
3. normalize labels such as `Job Location`, `Job Locations` and `Location`;
4. preserve the current canonical query-free opening URL;
5. leave standalone links without card-bound location as `None`;
6. reject location text from an adjacent card, page filter, navigation or
   global location selector;
7. preserve same-tenant URL validation and pagination behavior.

It must not:

- raise detail-enrichment or company request budgets;
- use company, domain, title, location or job-ID special cases;
- treat page-global city text as opening evidence;
- weaken S7 title, location, provider or tenant validation;
- change Jibe parsing or non-iCIMS providers.

## Acceptance

### Provider tests

- a canonical `iCIMS_JobCardItem` produces title, URL and normalized location;
- multiple cards retain their own locations without cross-card leakage;
- location before and after the opening link remains card-bound;
- missing, malformed or page-global location stays `None`;
- cross-tenant and non-detail links remain rejected;
- structured JSON/JSON-LD behavior remains unchanged.

### Focused replay/live

After implementation:

1. run provider and opening-matcher local tests;
2. replay the three captured records through isolated fixtures/snapshots;
3. run a code-frozen focused live only for Elderwood, Steampunk and Great Day
   Improvements;
4. require 3/3 S7 Exact and zero wrong URL, wrong location, cross-company or
   cross-tenant result;
5. replay that focused live at 3/3 with zero mismatch, fixture gap, budget
   recovery or tape divergence.

### Regression

The integrated release gate remains:

- full unit suite;
- provider benchmark;
- resolver benchmark;
- architecture gate;
- credential-shape scan;
- `git diff --check`.

Fresh100 `.278` and Frozen100 `.280` scores remain immutable. Focused success
does not rewrite either full-cohort score.

## Rollback

The change is isolated to iCIMS hosted-card parsing. If any card leaks location
between openings, produces a wrong-location Exact, changes cross-tenant
behavior or fails same-version replay, revert the provider parser change and
retain the Phase A evidence for redesign.

