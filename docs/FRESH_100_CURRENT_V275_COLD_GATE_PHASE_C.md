# Fresh100 Current `.275` Cold Gate Phase C

Date: 2026-07-28
Code commit: `da087dc867e02f25d1e71e191d254e3be5dff35b`
Product adapter: `2026-07-28.275`
Decision: **live accepted for measurement; replay gate failed; no Phase B selected**

## Scope

This is a current-version cold regression of the existing Fresh100 development
cohort. It is not a new cohort, blind holdout or replacement for the immutable
`.188`, `.209` or `.270` reports.

The run used
`samples/evaluation/live100_fresh_cohort_20260718.json`, SHA-256
`fcf2ece19f9096e3b1ac64dd7aba60b53f78c520b8c9228cf6505ee8a1c86402`.
It contained 100 records and 100 unique LinkedIn job IDs. Checkpoint,
completion, evidence, snapshot, failure and replay roots were all new. The
first live line reported 100 pending records, zero restored completions and
zero retryable resubmissions. Code remained frozen throughout the run.

No new cohort was opened. Sealed holdouts v2/v3, the authenticated plugin and
the isolated LLM branch were not inspected or executed.

## Live Result

The cold run completed all 100 records in 884.6 seconds:

| Metric | `.275` | `.270` | Delta |
| --- | ---: | ---: | ---: |
| Verified Website | 91 | 90 | +1 |
| Career page | 77 | 78 | -1 |
| Verified Job List | 72 | 73 | -1 |
| S7 Exact opening | 31 | 32 | -1 |
| Pipeline partial | 49 | 48 | +1 |
| Pipeline failed | 20 | 20 | 0 |

The causal ledger classifies the raw run as 31 Exact, 21 evidence-backed
Verified No Match, one External Blocked and 47 unresolved. These are runtime
evidence classifications, not final human eligibility annotations. Eligible
Exact recall and the official five-class `SYSTEM_GAP` count remain
unreportable.

Compared with `.270`, 29 Exact records remained Exact. BWXT and TreeHouse Foods
were newly recovered. Jushi Holdings, IMG and Target Hospitality regressed,
for a net change of -1 Exact.

Applying the existing reviewed-terminal allowlist to this code-frozen trace
produces a separate development projection of 38 Exact, 19 Verified No Match,
one External Blocked and 42 unresolved. The additional Verified No Match is
Mayo Clinic, whose `.275` live trace reached complete official inventory and
found no matching posting. This projection does not rewrite the raw 31/100
score.

## Exact Safety Audit

All 31 Exact records were independently inspected:

| Safety check | Result |
| --- | ---: |
| Wrong opening URL | 0 |
| Wrong title | 0 |
| Wrong location | 0 |
| Cross-company publication | 0 |
| Cross-tenant publication | 0 |
| Missing or hash-invalid evidence | 0 |

The 31 records map to 30 unique opening URLs because two Arkema LinkedIn
records safely map to the same verified opening.

Three records have a non-identical canonical Job Board representation: BWXT
uses the provider apex versus `/search`, and two Arkema records use
`/?locale=en_US` versus `/search`. Their provider, tenant and specific opening
identity are continuous, so these are representation differences rather than
wrong opening URLs.

Slant CRM and Team Royal retain provisional but evidence-complete hiring
relationships. Resolute Road Hospitality is authorized to use the
`Braintree-Hospitality` Paylocity tenant by its first-party Career handoff; the
selected Spokane opening, title and job ID are consistent.

## Replay Result

The full same-version replay exported and executed all 100 records with record
integrity passed and zero fixture gaps:

| Classification | Count |
| --- | ---: |
| Reproduced | 97 |
| Budget recovery | 2 |
| Mismatch | 1 |
| Fixture gap | 0 |

The replay outcome gate failed.

- Diamondback Energy and State of Montana exhausted the live caller deadline
  after repeated network timeouts. Replay normalized both to
  `CAREER_PAGE_NOT_FOUND`; neither change proves a product-terminal recovery.
- Brown and Caldwell changed from a verified UltiPro Exact to
  `INVALID_STRUCTURED_DATA`. Snapshot sanitization changes a nested public
  `Address.State` object, so replay location evidence no longer selects the
  same Wailuku candidate. Brown also has a separate duplicate UltiPro
  pagination ID.

The strict acceptance requirement remains unmet:

```text
100/100 reproduced
0 mismatch
0 fixture gap
0 tape divergence
0 missing snapshot boundary
```

## Causal Decision

Three independent read-only reviews reached the same conclusion: no current
cluster qualifies for Phase B.

| Candidate cluster | Surface size | Common recovery evidence | Decision |
| --- | ---: | ---: | --- |
| UltiPro structured-State snapshot drift | 3 companies | 1 terminal | Below recovery threshold |
| Caller deadline after repeated timeout | 2 companies | 0 terminals | Below company and recovery thresholds |
| `search_results_filtered_to_zero` | 21 companies | Mixed Career, ATS, transport, identity and availability causes | Invalid common-root cluster |
| Eligible board portfolio incomplete | 5 companies | Generic, Pinpoint, Eightfold, ADP and Paylocity paths | Invalid common-code-path cluster |
| Interactive ambiguous GET | 2 companies | 2 possible | Below company threshold |

The raw ledger reports two mechanically large candidate signatures, but both
fail causal review. A shared stage or reason label is insufficient when the
observable trigger and production path differ.

No behavior code, company/domain/job-ID exception, provider heuristic,
scheduler change or identity relaxation is selected from this run.

## Gate Status

Product code did not change after the accepted `.275` integrated release gate:

- 2,839 tests passed, 4 skipped;
- provider benchmark 25/25;
- resolver benchmark 6/6;
- architecture validation 48 adapters / 0 issues.

Repeating 2,839 tests for a governance-only commit would add no behavioral
evidence. This group instead requires report consistency, checksum
verification, acceptance-ledger validation and `git diff --check`.

The release privacy scan found six distinct Google Maps browser API keys
embedded by public source sites across 40 trace, checkpoint, snapshot and replay
files. A follow-up shape audit found one AWS access-key-ID-shaped value from one
public job board across three snapshot/replay files. No authenticated LinkedIn
state, cookies or user credentials were used. The raw values were never added
to Git. The corrected release copy replaces them with deterministic Google and
AWS redaction markers; a second scan reports zero remaining matches. This
changes captured body bytes, so the archive is an audit record rather than an
executable replay capsule. The replay result above was produced before the
release scrub.

This is a general capture-sanitizer gap to qualify before another live release,
not permission to continue this batch or to hide the failed replay gate.

Fresh100 replay acceptance, current-version Frozen100 no-regression and two
qualifying unseen-cohort gates remain pending. The product goal remains open,
and no new live batch may start as part of this release closure.

## Immutable Artifacts

The privacy-scrubbed live, failure bundle, full replay outputs, ledgers and
evaluation history are preserved as:

`artifacts/releases/fresh100-current-v275-cold-20260728-run1.tar.zst`

The tracked checksum is:

`artifacts/releases/fresh100-current-v275-cold-20260728-run1.tar.zst.sha256`

SHA-256:

`319c494b276deb89424be2e7970c5d1fb13bf2a80e97c1e173ecd067445ccd85`

The archive payload is intentionally ignored by Git. Its checksum, privacy
notice and this report are versioned. The archive preserves the recorded replay
result but is intentionally not replay-executable after body-value redaction.
