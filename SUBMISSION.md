# Beta Submission

This repository is submitted as a bounded beta, not as a claim of universal
job-source coverage. It publishes an opening only after the S7 identity gate
verifies company/hiring relationship, provider, tenant, board, title, location
and opening continuity. Missing evidence remains partial, retryable, blocked or
rejected instead of producing a guessed URL.

## One-Command Demo

```bash
make beta-demo
```

The deterministic offline demo writes:

- `/tmp/ai-job-source-agent-beta-demo/results.json`
- `/tmp/ai-job-source-agent-beta-demo/trace.json`

It demonstrates one S7-verified Exact result and one fail-closed identity
rejection without requiring network access.

## Focused Live Demo

The public demo cohort was revalidated on 2026-07-31. To intentionally repeat
that focused live check outside the presentation, run:

```bash
python3.12 -m job_source_agent \
  --input samples/beta_demo_input.json \
  --fetch-timeout 8 \
  --output /tmp/beta-live-results.json \
  --trace-output /tmp/beta-live-trace.json
```

The live cohort is presentation evidence only. It is deliberately small and is
not reported as a generalization benchmark.

## Authenticated Extension

Extension `0.7.0` in `extension/` is the logged-in LinkedIn input client. Load
the directory as an unpacked Chrome extension, run `make reviewer-start`, and
open the popup to pair automatically. Then use **Scan selected**, **Scan page**,
and **Verify source**. The final 2026-08-02 manual gate covered selected and
25-record page state, async submission, popup reopen recovery, typed result
rendering, rescan recovery and CSV export. Scan records are restored after a
normal popup close when the same LinkedIn tab and posting context remain active;
verified public details are also retained by job ID across a later rescan.
Multi-record verification reports visible `completed/submitted` progress and
processes at most four companies concurrently. The final 25-record acceptance
run completed with 16 verified Job Lists and 8 verified openings; this is a
workflow check, not a generalized Exact-rate claim. Page scanning does not claim
an External Apply URL when LinkedIn exposes only a button without a safe target.
Each job row opens a secondary detail view that groups its LinkedIn posting,
External Apply, company website, Career page, Job List and Exact opening evidence.
For a URL-less external button, the reviewer may explicitly select **Open Apply**
for that job. The extension binds the click and resulting tab to the LinkedIn job
ID, then records only the final sanitized ATS URL; it never substitutes the
LinkedIn posting URL. The final gate captured Medtronic's specific Workday
`R72412-1?source=LinkedIn` destination. Backend provider, tenant and S7 verification remains
mandatory. **Export CSV** emits only the ten reviewer-facing result fields and
omits trace, credentials and authenticated page data.

## Review Path

1. Start with [REVIEWER_START_HERE.md](REVIEWER_START_HERE.md), or run
   `make beta-demo` for the deterministic offline path.
2. Read [the beta.4 release report](docs/BETA4_RELEASE_REPORT.md) for fixed
   versions, gates, privacy, and known limits.
3. Inspect the concise result and the seven-stage trace.
4. Read [README.md](README.md) for setup, architecture and measured limits.
5. Read [the project summary](docs/BETA_PROJECT_SUMMARY.md).
5. Use [the demo script](docs/BETA_DEMO_SCRIPT.md) for a 3-5 minute walkthrough.
6. Check [demo evidence](docs/BETA_DEMO_EVIDENCE.md) for current URL validation.
7. Check [extension acceptance](docs/EXTENSION_ACCEPTANCE.md) for the logged-in
   Chrome gate and privacy boundary.

## Release Package

```bash
make offline-gates
make beta-package
```

The package builder uses a tracked-file allowlist and excludes historical
artifacts, caches, checkpoints, snapshots, cookies, local environments, sealed
holdouts and Git metadata. It also runs the credential-shape scanner before
creating the archive.
