# Final Demo Script

Audience: Li Kai / engineering reviewer

Target length: 3-5 minutes

This is a product walkthrough, not a live coverage benchmark. Do not call an
Apply button, discovered candidate, Career page, or Job List an Exact opening
before backend verification has passed.

## Pre-Recording Checklist

- [ ] Use a clean desktop and close notifications, email, chat, password
      managers, and unrelated tabs.
- [ ] Confirm the screen contains no cookies, authenticated HTML, connection
      tokens, browser storage, local paths with private data, or terminal history.
- [ ] Log into LinkedIn before recording; do not show the sign-in flow.
- [ ] Load the unpacked `extension` directory and confirm the current extension
      version at `chrome://extensions`.
- [ ] Open the Medtronic LinkedIn posting in the selected-job detail pane.
- [ ] Keep one known typed failure available for the honest failure example.
- [ ] Remove old CSV downloads so the new export is easy to identify.
- [ ] Check network stability, browser zoom, microphone level, and readable text.

## 0:00-0:30 - Start The Product

**Visible on screen**

- Repository root in a terminal.
- `make reviewer-start` starting successfully.
- Chrome extension popup changing to **Online** without opening
  **Advanced connection**.

**Suggested narration**

> The reviewer starts the complete local product with one command. The extension
> pairs with the local bridge automatically, so normal use does not require
> copying a URL or token.

## 0:30-1:10 - Read LinkedIn Jobs

**Visible on screen**

- A logged-in LinkedIn Jobs search with one job selected.
- **Scan selected** returning that one identity-bound record.
- A brief **Scan page** example showing bounded progress and multiple records.

**Suggested narration**

> Scan selected reads only the job visible in the detail pane. Scan page can
> inspect the currently loaded result cards. The extension binds each record to
> its LinkedIn job ID and keeps LinkedIn-native Apply separate from External
> Apply.

## 1:10-1:55 - Capture Medtronic External Apply

**Visible on screen**

- The Medtronic job's secondary detail view.
- Separate **LinkedIn posting** and **External Apply** rows.
- **Open Apply** selected for the Medtronic posting.
- The final Workday opening loaded in the new ATS tab.
- Return to the original LinkedIn tab, reopen the popup, and show the persisted
  External Apply URL beginning with
  `https://medtronic.wd1.myworkdayjobs.com/` and containing the specific
  `R72412-1` opening.

**Suggested narration**

> LinkedIn exposes an External Apply button here but may not expose its target
> in the page DOM. Open Apply performs one explicit, job-bound capture. After the
> ATS redirect completes, I return to LinkedIn and reopen the popup. The stored
> value is the final Medtronic Workday opening, not the LinkedIn posting. This is
> still candidate evidence until the backend verifies it.

## 1:55-2:45 - Verify And Inspect Evidence

**Visible on screen**

- **Verify source** entering a queued or running state with progress.
- A completed per-job detail with Company website, Career page, Job list, and
  Exact opening rows.
- The External Apply and LinkedIn posting rows remaining distinct.

**Suggested narration**

> Verify source sends the records through the backend identity pipeline. The
> system verifies the employer or hiring relationship, provider, tenant, title,
> location, opening status, and final URL. A plausible candidate can become a
> Career or verified Job List result without being promoted to Exact.

## 2:45-3:15 - Export Reviewer Results

**Visible on screen**

- **Export CSV** producing `ai-job-source-results.csv`.
- The CSV header and a few rows, with unavailable evidence blank.

**Suggested narration**

> Export CSV produces a reviewer-friendly handoff with the LinkedIn identity,
> company evidence, External Apply, Job List, exact opening, result status, and
> error code. Missing evidence stays blank. Cookies, authenticated page HTML,
> tokens, traces, and browser credentials are not exported.

## 3:15-3:45 - Show An Honest Typed Failure

**Visible on screen**

- One completed record with a typed non-success result.
- No fabricated Exact opening URL.

**Suggested narration**

> This record did not complete the identity chain, so the product returns a
> typed failure and leaves the opening empty. The system fails closed because a
> wrong-company or wrong-tenant URL is worse than an explicit no-result.

Do not stage a network error as a correctness success. State whether the visible
failure is a discovery gap, retryable transport issue, verified no-match, or
identity rejection.

## 3:45-4:30 - Explain The Architecture And Boundary

**Visible on screen**

- The three-route candidate-discovery diagram in `docs/ARCHITECTURE.md`.
- The S7 identity gate or a concise stage summary.

**Suggested narration**

> Candidate discovery has three routes: LinkedIn External Apply, provider-aware
> ATS search, and website or Career exploration. They improve recall but cannot
> declare success. Every candidate enters provider validation, and S7 requires a
> continuous chain from company or verified hiring entity through provider,
> tenant, Job List, and opening, with title and location checks.
>
> On the Fresh100 development cohort, the evidence funnel was 92 websites, 79
> Career pages, 71 verified Job Lists, and 36 raw Exact results. This is an honest
> development measurement, not a claim of 100 percent coverage. The 7-case demo
> demonstrates workflow behavior and is not a generalization benchmark.

## Closing

**Suggested narration**

> This Beta is designed to be usable and auditable: it captures the reviewer's
> LinkedIn input, returns evidence at the strongest verified level, exports the
> result, and clearly reports when it cannot safely produce an exact opening.

## Final Pass/Fail Checklist

- [ ] `make reviewer-start` reaches **Online** without manual URL or token entry.
- [ ] **Scan selected** returns the visible LinkedIn job with the correct company,
      title, and job identity.
- [ ] **Scan page** shows bounded progress and does not mix job identities.
- [ ] Medtronic **Open Apply** opens the final Workday job and the popup persists
      that external URL after returning to LinkedIn.
- [ ] LinkedIn posting and External Apply remain separate fields.
- [ ] **Verify source** reaches a terminal state and only verified evidence is
      shown as Exact.
- [ ] Per-job detail visibly distinguishes Company website, Career page, Job
      list, and Exact opening.
- [ ] **Export CSV** downloads readable rows with missing evidence blank.
- [ ] One typed failure is shown without a fabricated URL.
- [ ] Three-route discovery and S7 are explained without claiming 100 percent or
      presenting the 7-case demo as a benchmark.
- [ ] No cookies, authenticated HTML, tokens, private browser data, or unrelated
      personal content appears in the recording or exported file.

Any unchecked item is a recording failure. Fix the workflow or record the known
limitation before sending the demo.
