# Annotation Remediation Report

Status: manually confirmed simple-path queue completed; product-default observed regression passed
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

The original six-round resource stop was removed by the user on 2026-07-17.
The observed cohort may now drive additional generic remediation rounds until
all manually confirmed eligible simple-path failures are recovered or new
evidence proves that a record is no longer eligible. Company-specific runtime
overrides and unverified URL publication remain prohibited.

There is no replacement numeric round limit for targeted remediation. Full
cohort live runs remain batched and serial, while targeted fixes continue until
the manually reviewed simple-path queue is empty or a concrete external,
identity, privacy, paid-access, or product-contract blocker is documented.

### Unbounded stabilization continuation

- Provider Career URLs retain typed first-party provenance even when the
  adapter canonicalizes `/jobs` or an old provider host to the tenant board.
  Texas Children's (Oracle HCM) and Gary and Mary West PACE (iSolved) replay
  from prior S7 identity failures to exact results.
- Northwell's CWS page-declared SmartPost organization is validated against the
  internal public inventory endpoint only after the declared endpoint returns
  not-found; every row must retain the same organization and open status.
- Same-title page candidates use target-location URL evidence only as a ranking
  feature. S7 separately rejects an explicit conflicting US state, preventing
  the prior Bellevue/York System One swap.
- Candidate counts distinguish incomplete native inventory from visible,
  selected page candidates; no empty inventory is rewritten as complete.
- Product entry points default to the staged three-route candidate path, while
  lower-level library defaults and legacy replay remain disabled for
  compatibility. The old path remains available through the explicit disable
  flag.

Focused live acceptance recovered exact openings for Northwell, Texas
Children's, Gary and Mary West PACE, System One, SpaceX, and Kodiak Cakes.
Texas/SpaceX passed again at 2/2 using the ordinary default command, and the
full-outcome replay passed 2/2. Hugh Chatham remains intentionally non-exact:
the public target belongs to Atrium Health and no verified hiring relationship
connects the source company to that tenant.

### Product-default observed regression

After the focused gates passed, a new serial 40-company run used the ordinary
product configuration with three-route discovery enabled by default. It did
not use annotation URLs, company maps, authenticated browser state, or a
parallel live benchmark.

| Funnel | Baseline | First checkpoint | Product default | Delta from checkpoint |
| --- | ---: | ---: | ---: | ---: |
| Verified website | 35/40 | 38/40 | 40/40 | +2 |
| Career page | 26/40 | 29/40 | 32/40 | +3 |
| Job list | 20/40 | 22/40 | 28/40 | +6 |
| Raw exact opening | 13/40 | 18/40 | 22/40 | +4 |

The run completed 40 unique companies in 401.5 seconds. Its final per-stage
capture lineage records 1,482 public HTTP transactions: 170 for website
resolution, 42 for hiring identity, 441 for Career discovery, 749 for Job Board
discovery, and 80 for opening match. All nine records still
eligible after manual and identity review recover an S7-verified exact opening:
Aarris Healthcare, System One, CHC, SpaceX, Lacoste, Texas Children's,
Northwell Health, Gary and Mary West PACE, and Avery Dennison. The frozen
annotation evaluator reports 9/10 only because the frozen source still labels
Hugh Chatham as a system gap; the later hiring-identity audit reclassifies it as
`eligibility_unknown`, so the current eligible queue is 9/9.

| Recovered record | Verified provider / tenant | Official exact opening |
| --- | --- | --- |
| Aarris Healthcare | ApplicantStack / `aarris` | `https://aarris.applicantstack.com/x/detail/a27xztr5mziq` |
| System One | First-party verified inventory / canonical board URL | `https://jobs.systemone.com/job/mechanical-design-engineer-industrial-manufacturing-york-pa-376670/07c3bf4e-7d3a-11f1-9454-02420a6c7775` |
| CHC Snohomish County | UltiPro / `COM1101CMHS` board | `https://recruiting2.ultipro.com/COM1101CMHS/JobBoard/8de41890-2fe6-4347-b1ad-f3043de88a1a/OpportunityDetail?opportunityId=7905b0d4-e183-4125-b3f6-fc6044809d7d` |
| SpaceX | Greenhouse / `spacex` | `https://boards.greenhouse.io/spacex/jobs/8527570002?gh_jid=8527570002` |
| Lacoste | DigitalRecruiters / `careers.lacoste.com` | `https://careers.lacoste.com/en/annonce/4371325-account-executive-10016-new-york` |
| Texas Children's Hospital | Oracle HCM / `eohh`, site `CX` | `https://eohh.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/425798` |
| Northwell Health | CWS / verified organization `1962` | `https://jobs.northwell.edu/job-3/23112933/registered-nurse-ambulatory-ob-gyn-lake-success-ny` |
| Gary and Mary West PACE | iSolved / `westpace` | `https://westpace.isolvedhire.com/jobs/1822062` |
| Avery Dennison | SmartRecruiters / `averydennison` | `https://jobs.smartrecruiters.com/averydennison/744000137723991` |

For every row, S7 verifies the source company to hiring entity, provider,
tenant, canonical board, selected opening, title, active status, and available
location evidence. The URL table is reporting evidence only; none of these
values is a production lookup override.

Four records have independently frozen expected opening URLs and match 4/4.
Wrong expected URL count and unsafe exact count are both zero. Whole-output
exact precision is still not reportable because 18 of the 22 exact records do
not have an independent frozen URL label. Solace remains an explicit review
item rather than a claimed precision success. Lilly Pulitzer safely remains
non-exact because the current opening's explicit location conflicts with the
LinkedIn target.

Final non-exact reasons are:

| Reason | Count | Interpretation |
| --- | ---: | --- |
| `CAREER_PAGE_NOT_FOUND` | 8 | No verified public Career surface |
| `JOB_BOARD_NOT_FOUND` | 4 | Career evidence exists, but no verified inventory handoff |
| `OPENING_DISCOVERY_INCOMPLETE` | 3 | Public inventory could not support an exact conclusion |
| `RESULT_IDENTITY_MISMATCH` | 1 | S7 rejected the explicit location conflict |
| `BOT_PROTECTION` | 1 | Public inventory was blocked during this run |
| `NO_PUBLIC_OPENINGS` | 1 | Complete official inventory verified no public match |

The post-fix full-outcome replay reproduces 40/40 outcomes with zero mismatches
and zero fixture gaps. No company worker raised an unhandled runtime exception.

Product-default artifact provenance:

| Artifact | Path | SHA-256 |
| --- | --- | --- |
| Cohort | `/private/tmp/remediation-observed40-current-cohort.json` | `6771e2a547841c5846767764b3bb8ac5b19b5e3312f279a1bd8f6cfb89f35f26` |
| Results | `/private/tmp/remediation-observed40-product-default-v2-results.json` | `18ee8467cbaaeeea1505b645a549e193d4a283ff5475f8fba7ebb229e99e6fb2` |
| Trace | `/private/tmp/remediation-observed40-product-default-v2-trace.json` | `745a42b84b750a76689427d1dbdaff112f6838f2160359a73c071ecfe4e1d787` |
| Summary | `/private/tmp/remediation-observed40-product-default-v2-summary.json` | `21a92ae8398092ac3d86968fe982249f9ab8f6f9464974d7c31004984c529ad5` |
| Annotation evaluation | `/private/tmp/remediation-observed40-product-default-v3-annotation-evaluation.json` | `6ac21ae31690e0ca82af7602c3697ec96669af26dfa701bbc718b099c0ea221a` |

- Agent configuration digest:
  `222ce27d63cda130b40453738145e80e8dd3d82b91ff75e6e76b9e9453b2b252`
- Batch configuration digest:
  `ce1de65e0bcfe8b700c9b7e5260868bfef955f04e80ba06c450f17fa1815f8f9`
- Replay output root:
  `/private/tmp/remediation-observed40-product-default-v3-full-replay`

## Latest Offline Gates

After the product-default live run and final identity/replay corrections:

- CPython 3.12.6: 1,725 tests passed, 3 skipped.
- Production provider benchmark: 25/25 exact expectations.
- Resolver benchmark: 6/6.
- Architecture validator: 33 native adapters, 0 issues.
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

The first frozen 40-company remediation checkpoint ran on 2026-07-17. It used
the same agent and batch configuration digests as the
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

The first-checkpoint Gary crash is fixed in code and tests. The former
single-live restriction has been removed, so it will be verified in the next
serial cohort regression after the remaining generic fixes pass offline gates.

Next-stage candidates, limited to three:

1. Reduce retryable fetch-budget failures through request reuse and measured
   per-phase budgets, without increasing concurrency.
2. Add a generic, bounded first-party SPA inventory contract for the remaining
   Career-to-board handoff cluster, driven by new unfamiliar evidence.
3. Improve official website identity evidence for ambiguous brands and regional
   hiring domains, again using a new cohort rather than this observed one.
