# Annotation Remediation Report

Status: remediation rounds and final live completed; delivery gates in progress  
Cohort classification: observed development cohort, not a blind holdout  
Baseline run date: 2026-07-16  
Manual review frozen: 2026-07-17T02:52:54+08:00

## Frozen Provenance

| Artifact | SHA-256 |
| --- | --- |
| Original manual annotations | `5a75b2aebfd5a4c5100718445e0e7a2f061d17105d857c3506e43e85cd4f23fa` |
| Baseline results | `3fa443fc7cb2b3af85fe659943188b64a50dd592f62dadabaed4bc05703f39c1` |
| Baseline trace | `dd1ae16e045a74afefe8ae86b420f50d6f335084fa64eb28e36e6815f327def0` |
| Baseline summary | `b7aa41657ff98c0017ece356a8c558e3b47389e09f09bb08cf52b4b701f4fcb0` |
| Frozen company input | `1250d5ffe92f424fe1a9399a253fe917e4208e0b82505e799c9fe51436d9ba49` |

- Baseline run-configuration digest:
  `2fdc706a387845a161dde2808c6c008e4b8f51e9c89a3542e6102bd346e3d895`
- Baseline HEAD: `e6b731d017d6f8a629342f317e83028d5abd173f`
- The normalized derivative is
  `samples/evaluation/observed40_exact13_manual_annotations.json`. It binds
  all source digests, preserves the 27 baseline failures, and leaves four
  unreviewed records as `unknown`.
- No annotation URL or expected disposition is used as a runtime override.

## Baseline

The frozen run contained 40 records:

| Funnel | Count | Rate |
| --- | ---: | ---: |
| Verified website | 35 | 87.5% |
| Career page | 26 | 65.0% |
| Job list | 20 | 50.0% |
| Exact opening | 13 | 32.5% |

Manual review covered 23 of the 27 non-exact records. The remaining four are
kept outside the eligible denominator until reviewed.

| Manual disposition | Count |
| --- | ---: |
| Confirmed system gap | 10 |
| Verified closed | 3 |
| No public opening | 5 |
| External/login blocked | 3 |
| Identity rejected | 1 |
| Eligibility unknown | 1 |
| Pending manual review | 4 |

Three confirmed gaps already had later targeted evidence of recovery before
this remediation goal: Aarris Healthcare, System One, and Community Health
Center of Snohomish County. Those targeted observations do not rewrite the
frozen 13/40 aggregate.

## Failure Clusters

| Cluster | Confirmed gaps | False-positive risk | Expected exact gain | Contract/module |
| --- | ---: | --- | ---: | --- |
| Candidate-to-hiring relationship and tenant binding | Cross-cutting correctness | Critical | 0 | S3/S5/S7 identity continuity |
| Provider inventory transport | 3 | Medium | up to 3 | SmartRecruiters, CWS, iSolved adapters |
| ATS candidate recall after website/career failure | 2 | High | up to 2 | targeted discovery and provider verification |
| Explicit job-list command traversal | 1 addressed offline | Low | up to 1 | Career traversal |
| Client-rendered Career route discovery | 1 addressed offline | Medium | Career recovery | bounded first-party bundle navigation |

Closed, no-public-opening, login-blocked, recruiter/client-undisclosed, and
identity-collision records remain negative controls. Recovery is not allowed to
turn any of them into an unverified exact URL.

## Remediation Rounds

### Round 1: Candidate-scoped hiring relationship

Status: implemented; focused offline gates pass.

- Added immutable `HiringRelationshipEvidence` and identity contract 1.1.
- Bound every provider candidate to candidate-specific company/provider/tenant
  evidence instead of inheriting a generic S3 identity.
- Ranked only relationship-verified candidates ahead of unrelated search
  tenants.
- Extended S7 to validate a published Job Board even when no opening is found.
- Suppressed the Job Board from public output when the hiring/provider identity
  chain fails.
- Added cross-tenant, unverified-board, and publication fail-closed tests.

Focused result: 36 tests passed; the broader identity/checkpoint/replay slice
passed 121 tests. Expected exact gain is zero; this round protects precision
before recall work.

### Round 2: Provider inventory variants

Status: implemented; provider-focused offline gates pass.

- SmartRecruiters: supports the official legacy widget configuration, multiple
  same-tenant configurations, and optional job-ad URLs while rejecting tenant
  conflicts. The frozen Avery page resolves to the canonical
  `jobs.smartrecruiters.com/AveryDennison` board.
- CWS/m-cloud: added a page-aware adapter with strict organization, host,
  redirect, and pagination continuity. The frozen Northwell page resolves its
  official board and public inventory API without a company override.
- iSolved Applicant Pro: canonical board and tenant/page identity are verified,
  but the frozen snapshot omitted the actual public inventory bundle. The
  adapter therefore returns typed incomplete instead of guessing an endpoint.
- Oracle HCM: validates CandidateExperience tenant/site/opening locators and
  exact detail-page JSON-LD. Login walls, closed pages, malformed structured
  data, redirect identity breaks, and board-only inputs remain typed incomplete
  or empty instead of becoming guessed exact results.

The provider discovery suite passes 394 tests with two environment-dependent
tests skipped. Frozen SmartRecruiters and CWS page probes recover their generic
provider contracts; iSolved remains an explicit incomplete result.

### Round 3: Targeted ATS opening candidates

Status: implemented; focused discovery and identity gates pass.

- Added bounded title-targeted queries per ATS family rather than one long OR
  query. Results remain untrusted `ProviderCandidate` values.
- Classified board and opening search candidates separately and merged them
  into the S5 portfolio without making S2/S4 mandatory blockers.
- Added a strict native-provider inventory relationship bridge: a selected
  exact opening can establish hiring evidence only when the adapter inventory
  is complete and the provider-reported hiring organization exactly matches the
  expected company or hiring entity.
- Added opaque Oracle-tenant recovery plus wrong-organization and cross-tenant
  negative tests. Search snippets and title similarity still cannot establish
  success.

Focused candidate-search tests pass 52 cases; checkpoint/search tests pass 78;
Oracle/opening/identity/application integration passes 90. The broader core
stage/identity/checkpoint slice passes 149 tests.

### Round 4: Explicit Career inventory traversal

Status: implemented; frozen-page replay and focused gates pass.

- Added bounded `job offers` command semantics to the shared link-extraction
  and scoring taxonomy. Explicit commands receive only a minimum traversal
  floor; existing stronger path/provider evidence keeps its prior ranking.
- Job-list commands survive the 200-link extraction cap and are recognized as
  listing candidates, while unlabeled, unsafe, or unrelated cross-site routes
  remain negative controls.
- A frozen Lacoste Career page now schedules and fetches
  `/en/annonces` from the visible `Our job offers` link. This is generic command
  evidence, not a Lacoste URL override.
- Added bounded parsing of first-party Angular route/label bundles. The frozen
  SpaceX page now recovers its official `/careers` route, but that shell alone
  does not establish a Job Board or exact opening.

The combined scoring, extraction, scheduler, hidden-board, and offline-pipeline
slice passes 178 tests. The frozen Lacoste probe selects the Job Offers page at
score 55 and fetches it before speculative fallbacks.

### Round 5: Provider inventory end-to-end contracts

- Added production-pipeline coverage for SmartRecruiters, CWS/m-cloud, iSolved,
  and Oracle HCM candidate-to-inventory handoffs.
- A provider may publish verified no-match only from complete inventory. A
  positively verified exact opening may still publish from a bounded incomplete
  visible inventory when provider, tenant, title, location, active status, and
  S7 identity all pass.
- CWS and SmartRecruiters expose candidates only after their adapter-specific
  inventory contract succeeds. Cross-tenant, incomplete, and wrong-company
  variants remain negative tests.

### Round 6: S2/S4-independent ATS recovery

- Added end-to-end pipeline tests proving that a verified targeted ATS candidate
  can recover after website or Career discovery fails, while an unverified
  search result cannot publish a board or opening.
- Preserved the seven stages: S2/S4 are no longer mandatory S5 blockers when
  External Apply or provider search supplies independently verifiable evidence.
- Preserved `hiring_organization_name` through selection trace and tightened
  first-party inventory completeness and Sitecore pagination semantics.
- Added replay-safe CWS and Oracle HCM policies without adding company URL maps
  or runtime annotation overrides.

The six-round resource stop is now reached. No further provider or heuristic
round is started from this observed cohort.

## Final Offline Gates

Before the final live run:

- CPython 3.12.6: 1,642 tests passed, 3 skipped.
- Production provider benchmark: 25/25 exact expectations.
- Resolver benchmark: 6/6.
- Architecture validator: 32 native adapters, 0 issues.
- Extension DOM/popup/bridge/HTTP slice: 34/34.
- Replay/live-batch/evaluation preflight slice: 103/103.
- `git diff --check`: passed.

## Replay Evidence

The old observed-40 snapshot cannot prove a complete post-change replay. The
scoped replay stops on the Solace record because the frozen source lacks an
outcome tape for `hiring_identity_resolution`. This is a source-artifact gap,
not a passing replay:

```text
no outcome tape for hiring_identity_resolution
```

The final live captured scoped evidence for all normal terminal paths. Automatic
full replay selected and exported 40/40 records but correctly failed preflight
because Gary and Mary West PACE crashed before finalizing its S5 scope. No tape
was invented after the fact.

The crash exposed a generic URL-contract mismatch: `ProviderCandidate` allowed
a public HTTPS URL form that `HiringRelationshipEvidence` rejected as
non-canonical. Candidate URLs now use the same strict identity canonicalizer;
tracking/trailing variants normalize and control-bearing values are rejected.
The replay planner also now follows the full producer chain for page-derived
generic boards instead of attempting to seed incomplete intermediate context.

Post-fix offline replay evidence:

| Slice | Reproduced | Mismatch | Gate |
| --- | ---: | ---: | --- |
| Exact outcomes | 18/18 | 0 | passed |
| Partial outcomes | 10/10 | 0 | passed |
| All replayable outcomes | 39/39 | 0 | passed |
| Original full live artifact | 39 replayable + 1 missing S5 boundary | 0 observed | failed closed |

The 39/39 gate is not presented as 40/40. The original failed 40-record manifest
is retained as the authoritative capture-integrity result.

After the P0 and replay-planner corrections, final release gates pass 1,646
CPython 3.12 tests (3 skipped), 25/25 provider expectations, 6/6 resolver cases,
32 native adapters with 0 architecture issues, 34/34 extension harness tests,
and `git diff --check`.

## Final Gate

Exactly one frozen 40-company final live evaluation ran on
2026-07-17. It used the same agent and batch configuration digests as the
baseline, isolated checkpoint/snapshot/completion roots, two company workers,
and no concurrent live benchmark.

| Funnel | Baseline | Final | Delta |
| --- | ---: | ---: | ---: |
| Verified website | 35/40 (87.5%) | 38/40 (95.0%) | +3 |
| Career page | 26/40 (65.0%) | 29/40 (72.5%) | +3 |
| Job list | 20/40 (50.0%) | 22/40 (55.0%) | +2 |
| Exact opening | 13/40 (32.5%) | 18/40 (45.0%) | +5 |

The normalized manual annotations cover 27/40 records. Among the ten records
explicitly marked eligible exact system gaps, exact recovery changed from 0/10
to 4/10 and system-defect rate changed from 10/10 to 6/10. Three recovered exact
URLs with a frozen expected URL matched 3/3; wrong expected URL count and unsafe
exact count are both zero.

Exact precision is **not reportable** for the whole 18-opening output: 15 exact
records lack a frozen independent expected URL. Independent review confirms the
Solace Ashby opening belongs to the `find-solace` healthcare employer even
though S2 selected the wrong same-name `solace.com` website. Lilly Pulitzer is
a temporal ambiguity: the reviewed LinkedIn posting was closed, while Workday
now exposes a newly posted active `R47803` role with the same title. It cannot be
proven to be the same requisition. Runtime S7 verification therefore remains a
safety proxy, not a claimed human precision score.

Final artifact provenance:

| Artifact | SHA-256 |
| --- | --- |
| Frozen final cohort | `37ca45671d22ed35a8ef50e10ca5d9f3888ba3bccc2cc7c7b0191db0192740f1` |
| Final results | `f85027be5b39159eac2e93070bccc203b24c67dcc2c59850d7f0bc5544abca32` |
| Final trace | `61ba1e7460aa03f4a6c40278d1227d77e5152cf08b2208978ebb3230caf0280b` |
| Final summary | `061b002c10d022644a40aac511c9982ddd90ee9b0cbffda825c97d635cdd4b3e` |
| Annotation evaluation | `0728c45197fe51768f98deeabff8c252fd544be3891d5b8159ef73d2bb89c95a` |

- Agent configuration digest:
  `2fdc706a387845a161dde2808c6c008e4b8f51e9c89a3542e6102bd346e3d895`
- Batch configuration digest:
  `1f2ca92a50b252c3f7987a65b9f9b7e4f53911a9b21820ca3af98a1d6dbdaf6a`
- Final artifacts remain under `/private/tmp`; snapshots, tokens, cookies, and
  authenticated HTML are not committed.

## Remaining Clusters

| Cluster | Count | Current meaning |
| --- | ---: | --- |
| Fetch budget exhausted | 6 | Retryable transport/search budget, not verified absence |
| Job Board not found | 6 | Career found, but no provider/first-party inventory handoff verified |
| Career page not found | 3 | Website found, no verified Career surface |
| Opening discovery incomplete | 3 | Board reached, inventory not complete enough for a conclusion |
| Website not resolved | 2 | Company identity evidence remained insufficient |
| Verified no public opening | 1 | Complete inventory produced a safe no-match |

The final-live Gary crash is fixed in code and tests but intentionally not
retested live because the goal allowed exactly one final live execution.

Next-stage candidates, limited to three:

1. Reduce retryable fetch-budget failures through request reuse and measured
   per-phase budgets, without increasing concurrency.
2. Add a generic, bounded first-party SPA inventory contract for the remaining
   Career-to-board handoff cluster, driven by new unfamiliar evidence.
3. Improve official website identity evidence for ambiguous brands and regional
   hiring domains, again using a new cohort rather than this observed one.
