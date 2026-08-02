# Reviewer Start Here

This guide gets the AI Job Source Agent running in under five minutes. The
product reads jobs already visible in a logged-in LinkedIn Jobs page, then uses
the local verification backend to find and validate the employer's website,
Career page, Job List, and exact opening.

## Prerequisites

- macOS or Linux with Make
- CPython 3.12
- Google Chrome, logged into LinkedIn

From the repository root, start the local reviewer service:

```bash
make reviewer-start
```

Leave this terminal running. The command performs its own preflight checks and
starts the local bridge. It does not print or ask you to copy a connection
token.

## Load The Extension

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Select **Load unpacked** and choose the repository's `extension` directory.
4. Open a LinkedIn Jobs search page with a job selected in the right-hand
   detail pane.
5. Open **AI Job Source Agent** from the Chrome toolbar.

The popup should become **Online** automatically. Normal use does not require a
Bridge URL or token under **Advanced connection**.

## Review The Workflow

1. Select **Scan selected** to read only the job currently shown in LinkedIn's
   detail pane, or **Scan page** to inspect the currently loaded result cards.
2. Select a scanned job row to open its per-job detail. LinkedIn posting,
   External Apply, Company website, Career page, Job list, and Exact opening
   are shown as separate evidence fields.
3. If LinkedIn exposes an External Apply button but not its target, select
   **Open Apply**. The employer's ATS opens in a new tab. After it finishes
   loading, return to the LinkedIn tab and reopen the extension popup. The
   persisted External Apply field should now contain the final ATS URL, not the
   LinkedIn posting URL.
4. Select **Verify source** to run provider, tenant, company, title, location,
   and opening checks. A discovered button or candidate is not Exact until this
   backend verification succeeds.
5. Select **Export CSV** to download the current records. Missing evidence is
   left blank rather than guessed.

## Stop

Return to the terminal running `make reviewer-start` and press `Ctrl-C`.

## Troubleshooting

- **Popup stays Offline:** confirm `make reviewer-start` is still running, then
  close and reopen the popup. Reload the unpacked extension if its files changed.
- **LinkedIn page is not ready:** wait for the selected job detail to finish
  loading, then select **Scan selected** again.
- **Open Apply has no captured URL:** allow the ATS tab to finish redirecting,
  return to the original LinkedIn tab, and reopen the popup.
- **Python 3.12 is installed under another command:** use
  `make reviewer-start PYTHON=/path/to/python3.12`.
- **Verification takes time:** close and reopen the popup or use **Refresh**;
  the run and scanned records are persisted.

## Measurement Boundary

The latest full Fresh100 development-cohort funnel is:

| Evidence level | Result |
| --- | ---: |
| Website | 92/100 |
| Career | 79/100 |
| Verified Job List | 71/100 |
| Raw Exact | 36/100 |

Fresh100 is a development cohort, not an independent generalization benchmark.
The separate 7-case demo is a workflow demonstration and must not be interpreted
as a generalization benchmark either.

## Privacy And Deeper Review

The reviewer export and release do not include cookies, authenticated LinkedIn
HTML, connection tokens, or browser credentials, and those materials must never
be committed. For implementation and acceptance details, see
[Architecture](docs/ARCHITECTURE.md),
[Extension Acceptance](docs/EXTENSION_ACCEPTANCE.md), and
[Beta Project Summary](docs/BETA_PROJECT_SUMMARY.md). These are optional and are
not required before running the product.
