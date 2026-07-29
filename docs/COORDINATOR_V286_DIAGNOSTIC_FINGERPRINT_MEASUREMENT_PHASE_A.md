# Coordinator `.286` Diagnostic Fingerprint Measurement

Date: 2026-07-29

## Purpose

The current Fresh100 development backlog has no implementation-qualified
cluster. Four deterministic singleton defects remain:

- CWS optional-reset/effective-configuration parsing;
- repeated static cards with sibling location and opaque detail URLs;
- safe same-page fragment pagination;
- multi-field GET forms with a title-specific field.

The next evidence step must seek two additional independent companies on one
of these exact paths. It must not run another arbitrary 30-company cohort or
turn a shared stage/budget label into a product change.

This phase prepares measurement tooling only. It performs no network request,
does not execute S2-S7, does not inspect sealed blind v2/v3 and does not access
the LLM branch.

## Sampling Contract

Collect one S1-only public LinkedIn candidate pool with 16 role queries:

| Lane | Queries |
| --- | --- |
| CWS exposure | Retail Operations Supervisor; Distribution Center Supervisor; Facilities Maintenance Technician; Medical Assistant |
| Static cards | Logistics Coordinator; Field Service Technician; Quality Inspector; Construction Estimator |
| Pagination | Credit Risk Analyst; Compliance Specialist; Claims Examiner; Payroll Specialist |
| Multi-field GET | CAD Designer; Project Controls Specialist; Clinical Laboratory Technologist; Revenue Cycle Specialist |

The role lanes diversify industry exposure; they do not label a provider or
authorize a fix. Every captured company is evaluated against every technical
fingerprint after Website/Career/Job List discovery.

The S1 collection ceiling is 480 public cards, at most 30 per query; the pool
must contain at least 320 unique job IDs or report a collection shortfall.
Selection then freezes one 80-record tranche with five independent companies
from each role query. Subsequent tranches exclude every company, LinkedIn
company slug and job ID used by earlier tranches. At most four 80-record
tranches may be drawn from the same frozen pool; each live tranche requires a
separate, isolated run root and is never merged into a product score.

Explicit exclusion inputs are:

- observed Fresh100 development input;
- the verified Frozen100 canonical input extracted from its historical release;
- all non-sealed v245-v273 diagnostic inputs and candidate pools;
- every earlier tranche from this measurement.

The selector must not recursively scan directories that may contain sealed
holdouts. Candidate and exclusion file paths and SHA-256 digests are serialized
in the manifest.

## Fingerprint Evidence

### CWS Effective Configuration

A positive failure requires one official Job List page with static
`CWS.jobs.set_api/set_options/sortby` calls, a provable optional reset, and a
later unique valid API, organization, detail path and final sort. The current
parser must fail because of the reset. A `cws_opts` shell or m-cloud string
alone is not sufficient.

### Repeated Static Cards

A positive failure requires at least three structurally repeated cards. The
target title, location and opaque detail anchor must share the same smallest
card owner; the current extractor must fail to bind that card. A specific CSS
class name is not part of the contract.

### Safe Fragment Pagination

A positive failure requires a same-origin, same-path next URL whose page
parameter advances exactly once while all other query fields remain stable.
The fragment must be presentation-only, the next page must add new records,
and fragment rejection must be the only current blocker.

### Multi-Field GET Title Search

A positive failure requires one official GET form with both a broad keyword
field and a semantically explicit Job Title field. Current code must select the
broad field, while submitting the full source title through the title field
must produce the target record and permit bounded pagination.

## Bundle Requirement

Each fingerprint needs an eight-company evidence bundle before implementation:

| Evidence type | Minimum |
| --- | ---: |
| New same-trigger, same-path recoverable failures | 2 |
| Existing-path successful controls | 2 |
| Shape-similar contract-negative controls | 2 |
| No-match, identity or location safety controls | 2 |

The existing Fresh100 singleton may be the third recoverable failure. Provider
tenant and hiring entity must also be independent; repeated postings or repeated
captures from one company count once.

## Measurement Tooling

`scripts/collect_linkedin_candidate_pool.py` now emits neutral
`development_candidate_pool` provenance and can require its exact target.

`scripts/prepare_diagnostic_cohort.py`:

- enforces fixed role quotas;
- rejects historical company, LinkedIn company slug and job-ID overlap;
- rejects Website, Career, External Apply, Job List and opening prefills;
- freezes source/exclusion digests, cohort hash and identity hash;
- records that selection executed S1 only.

`scripts/prepare_diagnostic_measurement.py`:

- requires CPython 3.12 and a completely clean Git worktree;
- validates cohort, identity and run-configuration contracts;
- refuses existing or symlinked artifact roots;
- creates disjoint checkpoint, completion, evidence, snapshot, output, replay
  and audit paths;
- writes a `prepared_not_executed` preflight and the exact frozen command;
- does not execute that command.

The frozen tranche configuration is:

`samples/evaluation/diagnostic_fingerprint_tranche_run_config.json`

## Runtime Layout

```text
/private/tmp/diagnostic-v286-fingerprint-<tranche>-20260729-run1/
  contract/
    cohort.json
    cohort-manifest.json
    run-config.json
    preflight.json
  live/
  state/
    checkpoints/
    completions/
    company-discovery-evidence.json
  capture/
    snapshots/
  replay/
    full/
  audit/
```

The run starts with 80 pending, zero restored and no prior evidence. Code and
configuration remain frozen through live, replay and audit.

## Acceptance

1. Live writes exactly 80 results and 80 traces.
2. Every Exact passes `scripts/audit_exact_identities.py`.
3. `scripts/audit_strict_replay.py` requires 80/80 `reproduced`; budget
   recovery, expected transition, mismatch, fixture gap and dropped records
   are all zero.
4. The complete capsule passes `scripts/scan_artifact_privacy.py
   --fail-on-match` with zero credential-shape matches before archive or
   publication. Reports never serialize matched values.
5. Exact URL, wrong-location, cross-company and cross-tenant errors remain zero.
6. Failure classification uses trigger plus production code path, not stage or
   reason-code labels alone.
7. Phase B begins only for at least three independent failures and at least
   three expected evidence-terminal recoveries.
8. If a generic change recovers fewer than three, the cluster is rejected and
   reclassified.

## Execution Status

Not authorized and not started. Candidate collection and live S2-S7 remain
network operations. After this tooling is committed and pushed, a separate
user approval is required before the first 80-record tranche is collected and
executed.

## Tooling Validation

- 112 related collector, selector, preflight, audit, LinkedIn discovery and
  live-runner tests pass.
- Exact audit passes the existing `.283` 36/36 and historical `.278` run4
  33/33 serialized chains.
- Strict replay audit passes the `.286` 6/6 reproduced bundle and correctly
  rejects historical `.278` run4 because it contains three budget recoveries
  and three mismatches.
- Privacy scanner passes a 45,475,974-byte `.286` artifact root with zero
  matches. Its 937,988,704-byte historical `.278` smoke reproduces the known
  ten Google-key-shaped matches across three files without serializing any
  matched value.
