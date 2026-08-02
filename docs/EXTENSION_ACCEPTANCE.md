# Chrome Extension Acceptance

This checklist is the release gate for the authenticated LinkedIn extension. It is intentionally
manual: automated fixtures cannot prove that current LinkedIn DOM selectors work in the user's
logged-in Chrome session.

## Preconditions

- Chrome is logged into LinkedIn and the AI Job Source Agent unpacked extension is installed.
- The extension card in `chrome://extensions` shows version `0.7.0` after **Reload**.
- The unpacked extension is loaded before the local bridge starts.
- Start the local bridge without supplying or copying a token:

```bash
make reviewer-start
```

Do not paste cookies, LinkedIn HTML, access tokens, or the bridge token into an issue or committed
artifact.

Automatic pairing is process-local and first-Origin-wins. An unclaimed bridge may wait for the
reviewer, but generated credentials must not appear in terminal output, popup messages, backend
artifacts, or the release archive. After the first claim, another extension Origin must fail closed;
the bridge must never fall back to a shared token.

## Acceptance Run

1. Open a LinkedIn Jobs search page with a visible selected job detail.
2. Open the extension at any time while the bridge is running. It must move from **Connecting** to
   **Online** without opening Advanced connection or entering a URL/token.
3. Close and reopen the popup. It must reuse the saved credential and must not call the pairing
   endpoint again while health succeeds.
4. Restart the bridge, then reopen the popup during the new window. A stale credential must produce
   one automatic re-pair followed by successful health; no stale run may be submitted.
5. Select **Scan selected** once. The popup must remain responsive, report exactly one job, and must
   not merge other search cards into the selected job. A DOM-observed External Apply link must be
   immediately usable without waiting for backend verification. If LinkedIn exposes only an external
   Apply button without its destination, the popup must report that observation without inventing a URL
   and must not substitute the LinkedIn posting URL.
6. Compare the first scanned selected job with the visible LinkedIn detail: company, title and job
   identity must refer to the same posting. An External Apply count may be zero.
7. Close and reopen the popup after the selected scan. The same identity-bound record and Apply count
   must reappear, and **Verify source** must remain enabled. Changing the active LinkedIn tab or selected
   `currentJobId` must not restore an unrelated saved record.
8. Select **Scan page**. Progress must advance over the current loaded batch, footer/filter controls
   must not become jobs, cancellation must recover the controls, and completion must restore the
   originally selected job. Jobs whose matching detail exposes an external Apply must each retain their
   own URL; native Apply and bounded hydration timeouts may honestly have none. The result count is
   bounded at 30 and is not the total LinkedIn search count.
9. Select optional **Verify source** once. A run must be queued without duplicate submissions; the
   immediate Apply link remains the primary path and verification may continue in the background.
   A multi-record run must display monotonic progress such as `Running 7/25`, not only an unchanging
   `Running` label. Both scan controls must remain disabled until the run reaches a terminal state so
   a new scan cannot discard the active run identity.
10. Close and reopen the popup while the run is queued or running. The saved run must resume polling
   or allow **Refresh**; it must not create a new run.
11. When complete, select one job row. Its secondary detail view must retain the same company, title
   and location and show separate LinkedIn posting, External Apply, Company website, Career page, Job list
   and Exact opening rows. Open one available verified link and confirm it is a public HTTPS page for
   the same company or verified hiring entity. Missing evidence must remain unavailable. Return to
   the list and confirm the prior scroll position is preserved.
12. For one visible URL-less External Apply control, use **Open Apply** in that job's detail view. The
   company application page may open in a new tab. After it finishes loading, return to the LinkedIn
   tab and reopen the popup. The External Apply row must now link to the final company ATS opening,
   while the LinkedIn posting row remains the LinkedIn URL. For the Medtronic acceptance sample this
   means a `medtronic.wd1.myworkdayjobs.com/.../R72412-1?source=LinkedIn` URL, not
   `linkedin.com/jobs/view/...`. A native LinkedIn Apply record must not offer this action.
13. Select **Export CSV** after the scan and again after verification. The file must use exactly
   `company_name`, `linkedin_job_title`, `linkedin_job_url`, `company_website_url`,
   `career_page_url`, `job_list_page_url`, `external_apply_url`, `open_position_url`,
   `result_status`, and `error_code` in that order. Missing URLs must be blank. Confirm the file
   contains no trace object, cookie, token, authenticated HTML, private URL, credential URL,
   sensitive query parameter, or executable spreadsheet formula.
14. Record only the run ID, final status, counts, and artifact directory. Do not commit the generated
   run directory, cache, token, authenticated page, or browser storage.

## Pass Criteria

- Connection, scan, submit, popup reopen, poll and result rendering all complete without a stuck
  disabled control or uncaught popup error.
- The selected detail record has one coherent LinkedIn job identity; no competing card is merged
  into it.
- No duplicate POST is created by repeated clicks, and no unsafe/private URL is rendered as a link.
- A captured External Apply URL is bound to the same LinkedIn job ID and to the tab opened by that
  explicit action; unrelated tabs and LinkedIn posting URLs are never accepted as the destination.
- CSV export preserves the fixed ten-column contract, backend status and error code without leaking
  unlisted backend fields or browser/session data.
- Whole runs remain serialized and one run uses no more than four company workers; displayed progress
  never exceeds the submitted count.
- A successful result link is manually confirmed against the company/hiring-entity identity. A
  normal typed no-match or partial result does not fail the plugin workflow.

## Failure Capture

On failure, record the extension version, LinkedIn route (`search` or `view`), visible symptom,
popup message, and whether the bridge received a request. Do not capture the full authenticated
page. Classify the failure before changing code:

- `dom_identity`: company/title/job URL mismatch or wrong selected detail.
- `dom_selector`: visible fields or Apply state missing.
- `apply_capture`: explicit Apply click rejected, wrong tab correlated, final ATS URL not captured,
  or a LinkedIn posting URL displayed as External Apply.
- `readiness`: LinkedIn content still loading after the bounded retry.
- `bridge_connection`: offline, timeout, rejected token, or stale run.
- `pairing`: invalid client contract, claimed Origin conflict, malformed response, or a configured
  finite pairing window that expired.
- `response_contract`: malformed or incompatible bridge payload.
- `rendering`: valid result not displayed or unsafe result displayed as a link.

Fix a reusable failure cluster with a minimal sanitized fixture. Do not add a company-specific
selector or move ATS/provider logic into the extension.

## Latest Acceptance Evidence

On 2026-07-15, a logged-in Microsoft Jobs search exposed LinkedIn's obfuscated search UI. Version
`0.2.0` correctly returned `not_ready` instead of inventing a record; read-only DOM inspection then
froze a generic selected-job semantic fixture. Version `0.2.1` scanned one selected Microsoft job,
unwrapped one public LinkedIn Apply destination, and completed strict verification with a verified
job list but no verified exact opening. The run also exposed a popup polling gap that allowed repeated
submissions between polls. Version `0.2.2` displays the Apply target immediately, makes verification
optional, and prevents another submission while the current run is active. The user confirmed the
v0.2.2 immediate Scan/Apply UI. Reopening the popup during a v0.2.2 in-flight run was not repeated
manually; its state restoration and duplicate-run lock remain covered by the popup harness.

On 2026-08-01, extension `0.4.0` completed a fresh logged-in Chrome acceptance on a LinkedIn Jobs
search. **Scan selected** returned exactly the selected Medtronic posting and classified its visible
button as `external_apply_observed` without inventing a target URL. **Scan page** hydrated and bound
25/25 identity-bearing cards, distinguished external and LinkedIn-native Apply states, and restored
the original `currentJobId`. **Verify source** immediately rendered `Running`; closing and reopening
the popup restored that run, which then rendered `Complete` with the honest typed backend result
`CAREER_PAGE_NOT_FOUND`, zero Job Lists, and zero verified openings. Acceptance run
`43490fdb72864fa0b3eaf8fb88ba1f24` wrote only local temporary artifacts, which are excluded from
the repository and release package. Version `0.4.1` contains the same accepted behavior and only
advances release metadata.

Later on 2026-08-01, the zero-config bootstrap was exercised against extension `0.6.1`: the popup
auto-paired without an entered URL or token, and the already-open LinkedIn tab completed a 25-record
page scan after the versioned content-script upgrade. A real 25-record Verify run
`853117ab8a544235b19abc3883b39e08` reached a terminal backend artifact with 16 verified Job Lists
and 9 verified openings. This acceptance exposed one presentation-state race: starting another scan
while Verify was active discarded popup tracking even though the backend completed. Extension
`0.6.2` locks both scan controls for the lifetime of an active run; the regression is covered by the
focused popup gate. After the final Reload, the user visually confirmed `0.6.2` as **Online** with all
25 tab-scoped records restored and no stale `Running` state. This closes the manual extension gate;
the 9/25 opening result describes this one visible LinkedIn page, not generalization performance.

On 2026-08-02, extension `0.7.0` completed the final logged-in acceptance. `make reviewer-start`
auto-paired and reached **Online** without entering a Bridge URL or token. **Scan selected** bound
Medtronic job `4447068921`; its secondary detail kept the LinkedIn posting separate, and explicit
**Open Apply** captured and persisted the final public Workday opening ending in
`Software-Automation-Test-Engineer-I_R72412-1?source=LinkedIn`. Returning from the Workday tab and
reopening the popup retained that URL. Verify displayed **Running**, restored the same active run after
popup close/reopen, and completed run `1fc4d21ab759404386cb6d062fdf4168` with 25 records, 16 verified
Job Lists and 8 verified openings. Medtronic remained an honest `RESULT_IDENTITY_MISMATCH`; the
captured candidate did not bypass S7.

The acceptance also exposed and closed a generic presentation-state defect: rescanning used to erase
the verified-detail projection even though backend artifacts remained. Version `0.7.0` now keeps a
six-hour, 30-entry, public-field-only index keyed by LinkedIn job ID. After Reload and rescan, System
One's Website, Career and Job List remained visible. The downloaded 25-row CSV had the exact ten
columns, 92 public-HTTPS URL values, no LinkedIn-host External Apply URL, and no cookie, token,
authorization, raw HTML or trace marker. Runtime artifacts and browser storage remain local and are
excluded from the release.
