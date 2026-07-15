# ADR-0002: Keep Browser Extension As An Evidence Adapter

- Status: accepted
- Date: 2026-07-13

## Context

Public LinkedIn guest pages can expose company and job metadata but do not reliably reveal the off-site destination behind the Apply control. A logged-in browser can observe more useful DOM evidence and can scan the job list the evaluator actually sees. Reimplementing website resolution or ATS handling in a Chrome extension would create a second product path with different security, tests, and provider behavior.

## Decision

1. Ship a Manifest V3 extension that reads only visible LinkedIn Jobs DOM evidence.
2. Treat External Apply as optional evidence; do not click buttons, infer redirects, or claim it is always available.
3. Send records to a Python bridge bound only to loopback and protected by an explicit bearer token.
4. Limit each request to 30 records and 256 KiB, run discovery asynchronously, and persist versioned results/trace/summary outside the repository.
5. Reuse `CompanyInput` normalization and the existing `PipelineApplication` composition root.
6. Keep provider detection, board canonicalization, inventory reads, title matching, reason codes, and validation exclusively in the Python pipeline.
7. Bind detail-page evidence to the selected LinkedIn job identity. A search SPA may expose competing cards and stale detail roots; only a canonical LinkedIn HTTPS job URL matching `currentJobId` or an explicit detail-root identity can authorize detail evidence.
8. Treat content-script scan responses as contract version `2`, with `ready` or `not_ready` state. The popup may perform only a small bounded readiness retry and must ask the user to retry after that budget.
9. Validate the bridge endpoint in the extension before attaching credentials: only `http://127.0.0.1:<port>` with no credentials, path, query, or fragment is accepted. Popup requests use a bounded timeout, prevent duplicate operations, validate response shapes, and clear stale run IDs after authentication or not-found responses.
10. Render result links only when they are public HTTPS URLs. DOM evidence must reject credentials, fragments, local/private hosts, LinkedIn/lookalike hosts, and sensitive query keys; it never records raw HTML, cookies, browser storage, or page snapshots.

## Consequences

Positive:

- Logged-in page evidence can be used without storing or transferring browser cookies.
- The extension reports the same stage/provider outcomes as CLI and batch runs.
- New ATS adapters require no extension changes.
- Long discovery runs survive popup closure because the bridge owns run state.

Costs and limits:

- The user must run a local bridge and load the unpacked extension.
- LinkedIn DOM selectors require live browser hardening as its UI changes.
- External Apply remains unavailable when LinkedIn renders only a button with no observable destination.
- The loopback token is stored in Chrome extension local storage and should be treated as a local secret.
- LinkedIn is a changing authenticated SPA, so fixture coverage cannot prove selectors against the current production DOM.

## Validation

- Static Manifest contract rejects `<all_urls>`, scopes content injection to LinkedIn Jobs, and grants bridge access only to `127.0.0.1`.
- Node-backed DOM fixtures cover selected-job binding, hidden/disabled controls, canonical identity, safe External Apply evidence, provenance, and readiness. Popup fixtures cover endpoint validation, duplicate operations, scan retry, timeout/error recovery, stale runs, malformed responses, and safe result links.
- Unit tests cover input normalization, batch bounds, auth/origin policy, async completion, artifact persistence, and an offline exact-opening run.
- A real loopback smoke returns `200` for health, `202` for submission, and a completed 1/1 job-list plus 1/1 opening result.
- Chrome installation and a live LinkedIn DOM scan remain a manual acceptance step because they require user-approved extension installation and the user's authenticated browser session. The release checklist is `docs/EXTENSION_ACCEPTANCE.md`; automated smoke cannot mark this gate passed.
