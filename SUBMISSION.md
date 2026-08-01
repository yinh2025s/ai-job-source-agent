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

Extension `0.6.2` in `extension/` is the logged-in LinkedIn input client. Load
the directory as an unpacked Chrome extension, run `make extension-bridge`, and
open the popup to pair automatically. Then use
**Scan selected**, **Scan page**, and **Verify source**. The 2026-08-01 manual
gate covered 1/1 selected scan, 25/25 page scan, async submission, popup reopen
recovery, and typed result rendering. Scan records are also restored after a
normal popup close when the same LinkedIn tab and posting context remain active.
Multi-record verification reports visible `completed/submitted` progress and
processes at most four companies concurrently. The final 25-record acceptance
batch completed with 16 verified Job Lists and 9 verified openings; this is a
workflow check, not a generalized Exact-rate claim. The extension did not claim
an External Apply URL when LinkedIn exposed only the button without a safe target.

## Review Path

1. Run `make beta-demo`.
2. Inspect the concise result and the seven-stage trace.
3. Read [README.md](README.md) for setup, architecture and measured limits.
4. Read [the project summary](docs/BETA_PROJECT_SUMMARY.md).
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
