# Chrome Extension Acceptance

This checklist is the release gate for the authenticated LinkedIn extension. It is intentionally
manual: automated fixtures cannot prove that current LinkedIn DOM selectors work in the user's
logged-in Chrome session.

## Preconditions

- Chrome is logged into LinkedIn and the AI Job Source Agent unpacked extension is installed.
- The extension card in `chrome://extensions` shows version `0.2.2` after **Reload**.
- The local bridge is running with an explicit token:

```bash
JOB_SOURCE_BRIDGE_TOKEN="replace-with-a-local-secret" \
  python3.12 -m scripts.extension_bridge --port 8765 --workers 2 --fetch-timeout 8
```

Do not paste cookies, LinkedIn HTML, access tokens, or the bridge token into an issue or committed
artifact.

## Acceptance Run

1. Open a LinkedIn Jobs search page with a visible selected job detail.
2. Open the extension, expand **Connection**, enter `http://127.0.0.1:8765` and the matching token,
   then select **Save connection**. The state must become **Online**.
3. Select **Scan page** once. The popup must remain responsive, report at least one job, and must
   not duplicate the selected job. A DOM-observed **LinkedIn Apply** link must be immediately usable
   without waiting for backend verification. If LinkedIn is still hydrating, one bounded retry may occur.
4. Compare the first scanned selected job with the visible LinkedIn detail: company, title and job
   identity must refer to the same posting. An External Apply count may be zero.
5. Select optional **Verify source** once. A run must be queued without duplicate submissions; the
   immediate Apply link remains the primary path and verification may continue in the background.
6. Close and reopen the popup while the run is queued or running. The saved run must resume polling
   or allow **Refresh**; it must not create a new run.
7. When complete, verify rates are between 0% and 100%. Open one displayed **Exact opening** or
   **Job list** link and confirm it is a public HTTPS page for the same company or verified hiring
   entity. A reason code instead of a link is an acceptable typed failure.
8. Record only the run ID, final status, counts, and artifact directory. Do not commit the generated
   run directory, cache, token, authenticated page, or browser storage.

## Pass Criteria

- Connection, scan, submit, popup reopen, poll and result rendering all complete without a stuck
  disabled control or uncaught popup error.
- The selected detail record has one coherent LinkedIn job identity; no competing card is merged
  into it.
- No duplicate POST is created by repeated clicks, and no unsafe/private URL is rendered as a link.
- A successful result link is manually confirmed against the company/hiring-entity identity. A
  normal typed no-match or partial result does not fail the plugin workflow.

## Failure Capture

On failure, record the extension version, LinkedIn route (`search` or `view`), visible symptom,
popup message, and whether the bridge received a request. Do not capture the full authenticated
page. Classify the failure before changing code:

- `dom_identity`: company/title/job URL mismatch or wrong selected detail.
- `dom_selector`: visible fields or Apply state missing.
- `readiness`: LinkedIn content still loading after the bounded retry.
- `bridge_connection`: offline, timeout, rejected token, or stale run.
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
