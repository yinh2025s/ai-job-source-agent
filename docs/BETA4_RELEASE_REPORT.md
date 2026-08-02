# Beta 4 Release Report

## Release Identity

- Product: `AI Job Source Agent 0.1.0-beta.4`
- Chrome extension: `0.7.0`
- Backend adapter: `2026-07-29.286`
- Reviewer entry point: `make reviewer-start`
- Source archive: `dist/ai-job-source-agent-0.1.0-beta.4.zip`
- Checksum: `dist/ai-job-source-agent-0.1.0-beta.4.zip.sha256`
- Git identity: recorded by commit and repeated in the archive's
  `RELEASE_MANIFEST.json`

The checksum sidecar is authoritative for the final archive. The archive is
created only from a clean committed tree and refuses to overwrite any previous
release artifact.

## Product Scope

This release completes the reviewer workflow without changing discovery recall,
provider adapters, company rules, or S7 publication behavior. It adds:

- automatic loopback bridge pairing with one-command preflight;
- selected-job and bounded current-page LinkedIn scans;
- persistent scanned records, in-flight verification state, and identity-bound
  verified details that survive popup closure and a later rescan;
- per-job details for LinkedIn posting, External Apply, Website, Career, Job
  List, and Exact opening;
- explicit job-ID-bound capture of a button-only External Apply destination;
- a fixed ten-column, privacy-filtered CSV export;
- a one-page reviewer guide and a 3-5 minute demo checklist.

External Apply capture produces candidate evidence only. Provider, tenant,
company or hiring-entity, title, location, and opening continuity must still
pass the backend identity gates before the product reports Exact.

## Verification Evidence

The release candidate completed the following gates on 2026-08-02:

| Gate | Result |
| --- | --- |
| Focused extension/startup/release tests | 66/66 passed |
| CSV/release privacy tests | 12/12 passed |
| JavaScript syntax | background, content, popup, and harnesses passed |
| Final offline test suite | 3007 passed, 4 skipped |
| Provider benchmark | 25/25 passed |
| Resolver benchmark | 6/6 passed |
| Architecture validation | 48 native adapters, 0 issues |
| `git diff --check` | passed |
| Logged-in Medtronic External Apply | passed: final Workday `R72412-1?source=LinkedIn` captured |
| Popup reopen, rescan recovery, and CSV | passed in logged-in Chrome; 25 rows audited |
| Clean-package startup and privacy audit | passed: manifest/hash/path/privacy checks and real loopback start |

After the one-time offline gate, a narrow popup-state defect was found during
the logged-in acceptance: a new selected scan discarded the display projection
of an already verified job. The generic fix stores only the existing public
verified-detail allowlist, keyed by LinkedIn job ID, with the same six-hour TTL
and 30-record bound as the scan snapshot. The added rescan regression, complete
popup focused suite, JavaScript syntax check, and `git diff --check` passed. No
backend, adapter, provider, S7, or discovery behavior changed, so the complete
offline gate was not repeated. No additional Fresh100, Frozen100, provider, or
large live benchmark is part of this release.

## Logged-in Acceptance

On 2026-08-02, extension `0.7.0` auto-paired with `make reviewer-start` and
displayed **Online** without manual connection input. **Scan selected** bound the
visible Medtronic posting `4447068921`. From its secondary detail view, the user
selected **Open Apply** and the service worker retained the final destination:

`https://medtronic.wd1.myworkdayjobs.com/MedtronicCareers/job/Mounds-View-Minnesota-United-States-of-America/Software-Automation-Test-Engineer-I_R72412-1?source=LinkedIn`

The LinkedIn posting remained a separate field. Closing the popup while Workday
opened and reopening it on LinkedIn preserved the captured destination. A real
25-record Verify run (`1fc4d21ab759404386cb6d062fdf4168`) restored **Running**
after popup reopen and reached **Complete** with 16 verified Job Lists and 8
verified openings. The Medtronic candidate remained fail closed as
`RESULT_IDENTITY_MISMATCH`; capture alone did not become Exact. Rescanning then
restored System One's previously verified Website, Career, and Job List by job
identity, proving the state-loss regression fixed.

The downloaded CSV contained the exact ten-column contract, 25 records, and 92
non-empty URL values. Every URL was public HTTPS; no External Apply value used a
LinkedIn host, and the audit found no cookie, token, authorization, raw HTML,
trace, private URL, or credential marker. These are focused usability results,
not a replacement for the Fresh100 measurement below.

The clean release candidate contained 586 manifest-listed source files plus the
manifest itself. Independent extraction reproduced every size and SHA-256,
found zero forbidden release paths and zero credential-shape matches, passed
`make reviewer-check`, and started the packaged bridge on
`127.0.0.1:18765` before a clean `Ctrl-C` shutdown. The final archive repeats
the same checks after the report-only commit amendment.

## Measurement Boundary

The latest complete Fresh100 development-cohort funnel remains the `.283`
measurement:

| Evidence level | Result |
| --- | ---: |
| Website | 92/100 |
| Career | 79/100 |
| Verified Job List | 71/100 |
| Raw Exact | 36/100 |

These values are not rewritten as beta.4 results. Fresh100 is a development
cohort, the seven-record demo is not a generalization benchmark, and the old
Frozen100 result is a historical baseline rather than current release evidence.

## Privacy Boundary

The release builder considers Git-tracked source files only and excludes runtime
artifact, live, capture, cookie, token, raw, secret, cache, completion,
checkpoint, and snapshot path components. Synthetic offline fixtures remain in
the source package because they power deterministic tests and the offline demo;
authenticated LinkedIn HTML and browser/session data are prohibited.

The staged package is scanned for credential shapes before the deterministic ZIP
is written. CSV export separately rejects private or credential-bearing URLs,
sensitive query parameters, fragments, unlisted backend fields, trace objects,
raw HTML, tokens, cookies, and executable spreadsheet formulas.

## Known Limits

- Raw Exact recall remains 36/100 on the latest full development measurement.
- LinkedIn DOM changes may require a generic content-script update.
- Current-page scanning is bounded at 30 visible identity-bearing jobs and does
  not claim pagination coverage.
- Verification can be slow or partial when public company or ATS pages block,
  time out, require login, or expose incomplete inventory.
- A captured External Apply URL is not an Exact result until backend validation
  succeeds.

The reviewer should begin with `REVIEWER_START_HERE.md`; deeper implementation
and manual acceptance details are in `docs/ARCHITECTURE.md` and
`docs/EXTENSION_ACCEPTANCE.md`.
