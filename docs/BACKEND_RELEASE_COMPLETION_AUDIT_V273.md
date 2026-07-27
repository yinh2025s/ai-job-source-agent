# Backend Release Completion Audit After v273

Date: 2026-07-28  
Audited backend release base: `616d2d8`  
Decision: **backend release cycle closed; overall product goal remains open**

## Scope

This audit was performed after the v273 diagnostic cohort and before any new
live batch. It does not run or reinterpret a sealed holdout, and it does not
promote focused or development-cohort evidence into an official Fresh100 or
Frozen100 score.

The backend release base was frozen and synchronized on `main` and
`origin/main` before this grouped audit documentation commit.
The authenticated-apply plugin prototype remains isolated on
`codex/plugin-authenticated-apply-wip` at `aae68a6`. The independent LLM branch
remains outside `main`; no merge, rebase, cherry-pick or copy is authorized by
this audit.

## Release Evidence

The final v273 development diagnostic cohort ran on product adapter
`2026-07-27.270` and `stage_v1`:

- 30/30 live records completed;
- 26 verified Websites;
- 22 Career pages;
- 18 verified Job Lists;
- 13 S7 Exact openings;
- 30/30 same-version replay;
- 0 mismatch, fixture gap, tape divergence or missing record;
- 0 wrong URL, wrong location, cross-company or cross-tenant publication.

The immutable release archive is
`artifacts/releases/v273-diagnostic-20260727-run1.tar.zst`, with SHA-256
`ae9fdc4d23da1ab3493326ab60f39df00b4a7eebb7b513f8295f3ab2027233cc`.
The archive payload is preserved locally and intentionally ignored by Git;
the checksum and evidence reports are tracked. The original v273 `/private/tmp`
run directory is not the release authority.

The integrated offline release gate passed:

- CPython 3.12.6;
- 2,834 tests passed, 4 skipped;
- provider benchmark 25/25;
- resolver benchmark 6/6;
- architecture validation 48 adapters / 0 issues;
- `git diff --check` passed.

The accepted 2,834-test result is the permission-enabled integration run
recorded in the v273 Phase C report. A read-only sandbox reproduction during
this audit again reached the known loopback-bind restriction: 2,818 tests
passed, 4 skipped and one loopback setup failed, while provider 25/25, resolver
6/6 and architecture 48/0 reproduced. This is an environment denial, not a
replacement release result or a newly accepted full gate.

These facts close the v273 backend release cycle. They do not by themselves
close the product-level Fresh100 and unseen-cohort objective.

## Final Goal Audit

| Completion condition | Evidence at `616d2d8` | Status |
| --- | --- | --- |
| Fresh100 has an evidence-backed terminal for all 100 records | The latest matrix is explicitly a projection: 37 Exact, 12 Verified No Match, 1 External Blocked and 50 unresolved | **Not proven** |
| Every eligible Fresh100 record is S7 Exact | Focused runs have no complete eligibility annotation set or frozen 100-record rerun | **Not proven** |
| Fresh100 `SYSTEM_GAP=0` | Fifty records remain unresolved in the current projection | **Not met** |
| Fresh100 publication safety is zero-error | Focused and diagnostic Exact audits are clean, but no current-version unified Fresh100 audit exists | **Not proven for the full cohort** |
| Fresh100 current-version cold live completes 100/100 from empty state | Last official cold run was `.209`, with 19 Exact; no `.270` cold Fresh100 run exists | **Missing** |
| Fresh100 same-version replay is 100/100 with zero integrity defects | `.209` replay had one identity mismatch; later focused repairs do not replace a unified rerun | **Missing** |
| Frozen100 keeps the original 69 Exact on the same current version | `.188` remains immutable at 69/69 and replay 100/100; `.212` cross-version replay was inconclusive and no `.270` regression run exists | **Missing** |
| Offline release gates pass | 2,834 tests, provider 25/25, resolver 6/6, architecture 48/0 | **Proven** |
| Backend code, tests, reports and artifacts are committed and pushed | Backend release base `616d2d8` is on `origin/main`; v273 archive checksum is tracked | **Proven** |
| At least two unseen cohorts reach eligible recall >=70%, precision 100% and zero safety errors | v273 is a development diagnostic cohort with 43.3% raw Exact and no eligibility labels; blind v1 was 4/40 raw Exact; sealed v2/v3 remain unobserved | **Not met** |
| LLM product direction remains isolated pending explicit authorization | The LLM branch remains separate and was not integrated into this release | **Proven** |

## Historical Baselines That Remain Valid

The original Frozen100 `.188` result is preserved and must not be rewritten:

- Exact 69;
- Verified Not Found 23;
- External Blocked 5;
- Input Identity Invalid 3;
- System Gap 0;
- eligible Exact recall 69/69;
- Exact precision 69/69;
- replay 100/100.

It remains a valid `.188` baseline, but it is not evidence that current `.270`
behavior has no regression.

The latest official Fresh100 cold evidence remains the `.209` run summarized in
the `.212` stabilization report:

- 19 Exact;
- all 19 published Exact URLs passed the recorded safety audit;
- replay executed 100 records but failed acceptance on one Heritage identity
  mismatch;
- current-version product closure was not established.

The current Fresh100 matrix is a conservative development projection after
focused repairs, not a score: 37 Exact, 12 Verified No Match, 1 External
Blocked and 50 unresolved.

## Generalization Evidence

The number of cohorts satisfying the full unseen acceptance contract is
**0/2**.

Blind holdout v1 was genuinely unseen when executed, but its product funnel was
4/40 Exact. Its reported 100% conditional recall uses only four known-eligible
records while 36 eligibility labels remained unknown, and no accepted
same-version replay was reported. It therefore does not satisfy the complete
generalization gate.

Blind holdouts v2 and v3 are sealed, zero-overlap, unobserved one-shot cohorts.
Their identities were not opened for this audit. They remain future product
acceptance assets and cannot be counted as completed evidence.

The later v245-v273 cohorts are development diagnostics. Their Exact safety and
replay evidence is valuable, but they have influenced implementation and do not
qualify as two independent unseen acceptance cohorts.

## Stop Decision

No new cohort, Fresh100 rerun, Frozen100 migration run or sealed holdout was
started after v273. The v273 non-Exact records split into thirteen causal paths;
none met the repository rule of one shared trigger, one shared production code
path and at least three expected generic recoveries. Therefore this release
selects no further heuristic, provider or scheduler change.

The repository is release-clean and pushed, but the product goal must remain
active. The next product-validation phase requires an explicit measurement
decision: either authorize the missing current-version Fresh100/Frozen100 gates,
or preserve them and later consume sealed holdouts under the one-shot protocol.
Until then, no success claim may replace the missing evidence.
