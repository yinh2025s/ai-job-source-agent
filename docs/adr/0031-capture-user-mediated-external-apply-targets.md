# ADR-0031: Capture User-Mediated External Apply Targets

- Status: accepted
- Date: 2026-07-21
- Behavior implementation: extension `0.4.0`, live acceptance pending

## Context

The authenticated input-parity gate observed 24 LinkedIn Jobs details. Eighteen
showed a visible, enabled "on company website" button, but none exposed a target
URL in the inspected DOM. The current evidence adapter correctly records these
as `detail_observed_but_apply_absent` with
`external_apply_control=target_url_unavailable_in_dom`.

Adding more DOM selectors cannot recover a URL that is created only by the page's
click handler. Automatically clicking every Apply control during page-scan would
also create real navigation side effects without a trusted user activation. A
MAIN-world `window.open` wrapper is incomplete and page-controlled, while a
background listener with broad tab visibility would expand the extension's
permissions before its product value is known.

## Decision

Implement the first target-capture experiment as an explicit, single-posting,
user-mediated flow with no new Chrome permission:

1. On a selected LinkedIn detail, the user arms capture for exactly one canonical
   LinkedIn job ID.
2. The extension freezes the selected job identity and visible External Apply
   control evidence in `chrome.storage.session` with a short expiry.
3. The user clicks LinkedIn's real Apply button. The extension does not synthesize
   or repeat that click.
4. On the resulting external page, the user invokes the extension again. The
   existing `activeTab` grant allows the popup to inspect that active tab for this
   explicit action.
5. The popup shows the frozen company/title identity and asks the user to confirm
   binding this destination to that posting.
6. Only a confirmed URL that passes browser-side and Python-side public External
   Apply sanitization becomes an untrusted candidate lead. Provider, tenant,
   hiring relationship, inventory, title/location and S7 verification remain
   unchanged and mandatory.

The experiment is selected-job only. Page-scan v3 must not batch-click External
Apply controls. It does not use `tabs.onCreated`, `webNavigation`, `webRequest`,
`debugger`, `<all_urls>`, or MAIN-world injection.

ADR-0002's prohibition on clicking buttons remains intact: the extension never
clicks or replays the Apply action. This ADR only permits a user to perform the
normal navigation and explicitly offer its destination as evidence.

## Capture Contract

### State Machine

The persisted state is:

```text
idle -> armed -> awaiting_user_navigation -> target_presented
     -> validating -> bound -> committed
```

Typed terminal failures are:

```text
cancelled
capture_expired
permission_unavailable
source_identity_changed
external_control_not_observed
target_not_observed
target_is_linkedin
unsafe_target_url
sensitive_target_url
ambiguous_capture
bridge_validation_failed
```

Only one capture may be armed per browser profile. Arming a new posting requires
explicitly cancelling or expiring the old attempt; it must not silently overwrite
it. Success, cancellation and every failure clear the attempt.

### Frozen Identity

An armed attempt contains only:

- a random `capture_id`;
- source tab ID and canonical LinkedIn job ID/job URL;
- normalized company, title and location snapshots;
- evidence that the matching detail contained a visible, enabled off-site Apply
  control;
- `started_at`, `expires_at` and contract version.

Before arming, `currentJobId`, canonical detail URL, detail root and frozen card
metadata must identify the same posting. A stale or changing detail fails closed.
The captured URL updates a record only by exact canonical LinkedIn job URL, never
by fuzzy company/title matching or list order.

### URL And Trust Boundary

The destination is not success evidence merely because a page opened or the user
confirmed it. Acceptance requires:

- HTTPS with no username, password or fragment;
- a syntactically valid public host and port;
- no localhost, private/link-local/reserved address, LinkedIn-owned host or
  LinkedIn-lookalike host;
- no sensitive query key under the existing request-identity policy;
- browser sanitization before storage or display; and
- independent Python sanitization before constructing `CompanyInput`.

The Python input boundary currently normalizes records without re-sanitizing a
direct `external_apply_url`; that must be corrected before this capture source can
be enabled. The browser's result is always an untrusted lead, not authority to
publish an Exact URL.

Navigation capture uses provenance
`authenticated_user_apply_navigation`. It must not impersonate
`authenticated_detail_dom`. Source-classifier allowlists, checkpoint fingerprint,
bridge validation and evaluation counters must be updated together before release.

### Privacy And Side Effects

- Store the pending attempt in `chrome.storage.session`, not `storage.local`.
- Never store authenticated HTML, cookies, headers, browser history, redirect
  chains, rejected raw URLs, profile data or page storage.
- Persist only a sanitized accepted URL and minimal typed provenance. Failure
  traces contain reason codes, not raw rejected destinations.
- Do not auto-close, refocus, navigate back, submit forms or retry the Apply
  action. The external tab remains under user control.
- Incognito capture is unsupported in the first release.

## Rejected First-Release Alternatives

### MAIN-World Interception

Temporarily replacing `window.open` has no new named permission, but it shares the
page's JavaScript environment. The page can replace, bypass or call the wrapper;
the action may instead navigate the same tab, pre-open `about:blank`, or use an
iframe. It therefore cannot be the identity authority or the first-release default.

### Background Tab Observation

`tabs.onCreated`/`tabs.onUpdated` can observe actual browser navigation, but URL
access for arbitrary external tabs requires the sensitive `tabs` permission or
broad target-host access. `openerTabId` can also be absent, and a service worker
must recover races and suspension safely. This option remains a later experiment
only if the zero-permission live gate proves material External Apply value and
manual confirmation is the measured bottleneck.

If evaluated later, `tabs` must be an optional runtime permission. The extension
must still avoid `<all_urls>`, accept only one explicitly armed attempt, require a
unique opener/source/time binding, and leave user tabs untouched.

## Implementation Boundaries

After explicit behavior approval, work may split into disjoint ownership:

| Workstream | Ownership | Output |
| --- | --- | --- |
| Session contract | new `extension/capture_session.js` and focused harness | versioned state machine and expiry |
| Selected-detail arm | `extension/content.js` and content tests | strict job/control identity evidence |
| Popup handoff | `extension/popup.html`, `popup.js`, `popup.css` and popup tests | arm, confirm, cancel and recovery UI |
| Python trust boundary | `job_source_agent/linkedin.py` and adapter tests | mandatory re-sanitization of direct records |
| Source provenance | source classifier, fingerprint/evaluation tests | new provenance without weakening old allowlists |
| Governance/integration | manifest, ADR/plan/changelog and final review | versioning, merge and gates |

The main line owns shared schema/provenance changes and final integration. No
workstream may add company, domain, tenant or job-ID special cases.

## Acceptance

Offline tests must cover:

- arm, cancel, expiry, duplicate arm and popup/service-worker-free recovery;
- stale detail, changed job, same-company multiple jobs and exact job-ID binding;
- native Apply, closed posting, hidden/disabled/missing external control;
- LinkedIn destination, credentials, fragments, malformed ports, private hosts,
  lookalikes, sensitive query keys and late/replayed confirmation;
- browser/Python sanitizer parity and rejection at both trust boundaries;
- no `<all_urls>`, no new named permission and no automatic Apply click;
- bridge, checkpoint and evaluation provenance round-trip.

The authenticated live gate then captures 20-30 selected postings without running
Fresh100 or sealed holdouts. It reports External Apply coverage, supported-provider
rate, specific-opening rate, S7 Exact, overlap with the ordinary path, net-new
Exact, and all wrong-URL/company/tenant/location counts. Button presence and user
confirmation alone never enter any success numerator.

After that report, stop again. Only measured net-new value can justify either
coordinator-v2 authorization or a separately reviewed optional-`tabs` experiment.

## Rollback

The feature is additive in extension `0.4.0` with capture contract version 1.
Rollback removes the
capture UI/session/provenance and restores DOM-only behavior without changing
provider adapters, S6/S7, existing checkpoints or the `.188` release artifacts.

## Consequences

This path adds one deliberate user confirmation and is not a batch automation.
In return, it establishes user intent, exact posting identity and a minimal
permission/privacy boundary before investing in broader browser observation. It
answers the current product question--whether authenticated External Apply creates
meaningful verified recall--without prematurely migrating the coordinator.

## References

- Chrome `activeTab`: https://developer.chrome.com/docs/extensions/develop/concepts/activeTab
- Chrome `storage.session`: https://developer.chrome.com/docs/extensions/reference/api/storage
- Chrome optional permissions: https://developer.chrome.com/docs/extensions/reference/api/permissions
- Chrome `tabs` API: https://developer.chrome.com/docs/extensions/reference/api/tabs
- Chrome `scripting` execution worlds: https://developer.chrome.com/docs/extensions/reference/api/scripting
