# Authenticated External Apply Input-Parity Gate

## Decision

The authenticated input-parity gate completed on 2026-07-21 with 24 LinkedIn
Jobs records. It does not authorize `coordinator-v2`; ADR-0030 remains proposed.

The run disproves the earlier interpretation that the anonymous Fresh100
External Apply result of 0/100 meant that the postings had no off-site Apply
control. In the authenticated DOM, 18/24 details exposed a visible, enabled
"on company website" control and 6/24 exposed LinkedIn Easy Apply. LinkedIn
rendered all 18 off-site controls as buttons without an `href` or another public
target URL in the inspected DOM. The extension therefore captured zero safe
External Apply URLs and correctly sent none to provider or S7 verification.

## Frozen Scope

- Source: installed Chrome extension page-scan v3 in a logged-in LinkedIn Jobs
  collection.
- Capture run: `127118dc83e1406ca34952da0c5cc17e`.
- Records: 24, each bound to a canonical LinkedIn job ID.
- Raw authenticated HTML, cookies, tokens, browser storage and headers were not
  persisted.
- The privacy-minimized capture remains under `/private/tmp` and is not a
  repository artifact.
- Fresh100, Frozen100 and sealed blind holdouts v2/v3 were not run or opened.
- The capture bridge intentionally returned `Complete` immediately; it did not
  execute the backend pipeline or claim verification rates.

## Observation Results

| Observation | Count | Rate |
| --- | ---: | ---: |
| `external_apply_observed` | 0 | 0.0% |
| `linkedin_native_observed` | 6 | 25.0% |
| `closed_observed` | 0 | 0.0% |
| `detail_observed_but_apply_absent` | 18 | 75.0% |
| `detail_not_observed` | 0 | 0.0% |

All 18 `detail_observed_but_apply_absent` records carry the more specific trace
`external_apply_control=target_url_unavailable_in_dom`. This means the detail
and off-site Apply control were observed, but no public target URL was available
for safety cleaning. It must not be read as "no Apply control exists."

The 24 LinkedIn job IDs were unique. Navigation failure and detail-not-observed
counts were both zero in the accepted capture.

## Required Metrics

| Metric | Result | Interpretation |
| --- | ---: | --- |
| External Apply URL coverage | 0/24 (0.0%) | No safe target URL was present in the DOM |
| Visible off-site Apply-control coverage | 18/24 (75.0%) | Diagnostic only; not a URL and not Exact evidence |
| Supported provider proportion | N/A (0 URL inputs) | Provider registry was not bypassed |
| Specific opening proportion | N/A (0 URL inputs) | No opening candidate was fabricated |
| S7 Exact | 0 | No External Apply record was eligible for S7 |
| Ordinary-path overlap | 0 | No External-attributable Exact existed to overlap |
| External Apply net-new Exact | 0 | No eligible External Apply URL existed |

Focused end-to-end verification was not run on the 18 button-only records,
because doing so would require inventing or guessing their destinations. The
requested focused denominator was the set of captured External Apply URLs; that
set is empty.

## Safety Audit

- Unsafe or malformed External Apply URL: 0 observed, denominator 0.
- Wrong opening URL: 0 observed, denominator 0.
- Cross-company Exact: 0 observed, denominator 0.
- Cross-tenant Exact: 0 observed, denominator 0.
- Wrong-location Exact: 0 observed, denominator 0.

These are non-events, not positive precision evidence. No URL entered provider,
tenant, relationship, inventory or S7 validation.

## Generic Scanner Repairs

The gate found and repaired only reusable browser-input defects:

- scan the current same-origin LinkedIn `/preload/` iframe when it owns the Jobs
  DOM;
- support both obfuscated lazy-column cards and standard
  `li[data-occludable-job-id]` cards;
- bind known card job ID, URL `currentJobId` and matching detail identity before
  accepting detail evidence;
- require stable repeated detail observations and distinguish detail timeout
  from an observed detail without a usable target;
- prevent a previously selected card from inheriting the preceding card's job
  ID during restoration;
- distinguish Easy Apply from a generic `.jobs-apply-button` and fail closed
  when an off-site button has no target URL;
- clear a residual external URL when explicit closed evidence wins.

No company, provider, tenant or job-ID special case was added. Focused extension,
popup, bridge and source-posting tests pass 42/42.

## Follow-Up Decision

The authenticated gate establishes an input-adapter capability gap, not a
candidate-coordinator result. ADR-0031 now accepts a separately versioned,
single-posting, user-mediated capture contract with no new Chrome permission.
Its behavior is now implemented in extension `0.4.0` / adapter `.213`, with
offline gates complete. A new 20-30 record authenticated focused gate must still
measure verified net-new value and stop again before any optional `tabs`
experiment or coordinator decision.

`coordinator-v2` remains proposed and unimplemented. Fresh100 and sealed blind
holdouts remain out of scope for the capture experiment.
