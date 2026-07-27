# LLM URL Hypothesis Run 008 Report

## Decision

Run 008 **fails the promotion gate**. The LLM URL-hypothesis route produced
four correct Top-3 website candidates, but none became a strictly attributable
verified product recovery. The feature remains off by default and must not
advance to Fresh100, a blind holdout, or main.

The recommended disposition is to retain the LLM layer only as an experimental
fallback. Do not spend another paid run on prompt tuning now. First address the
general verification/transport failures and the deterministic search source
failure offline; any later paid experiment requires a new hypothesis and new
user authorization.

## Scope And Lineage

- Fixed cohort: 18 unchanged development records.
- Capture branch: `codex/llm-candidate-reasoning-foundation`.
- Capture commit: `f755fc7e3b4468372eac122d038c4c01ce1af223`.
- Model: `deepseek-v4-flash`.
- Prompt: `deepseek-company-candidates-v2`.
- Capture: one run only; no resume and no second paid attempt.
- Labels were loaded only after the capture was sealed.
- Fresh100 and blind holdouts were not opened or run.
- Main was not modified or merged.

The first evaluator invocation failed closed because it required a frozen
response for every proposed query, including queries not executed after the
10-candidate cap was full. Capture artifacts were not changed. Evaluator
commits `419530c` and `aeadfb1` bind source evidence to ranker-proven
`llm-query-N` execution and add explicit cross-brand, adopted-URL and per-record
token reporting. Evaluator version `1.2` then evaluated the same sealed capture.
No additional network or model call was made.

Persistent artifacts:

`/Users/yinhuang/.codex/visualizations/2026/07/20/019f8029-8c5e-77b2-9fe6-d357b476e283/ai-job-llm-url-hypothesis-run008-20260727`

## Metrics

| Metric | Control | Treatment / result |
|---|---:|---:|
| Candidate recall@3 | 0/18 | 4/18 |
| Candidate recall uplift | - | +22.22pp |
| Frozen search recall@3 | - | 0/18 |
| Frozen search recall@10 | - | 0/18 |
| LLM URL hypothesis recall@3 | - | 4/18 |
| Verified website recall | 8/18 | 3/18 |
| Exact openings | 2 | 2 |
| Eligible recovery | - | 0/18 |
| Strict causal LLM recovery | - | 0/18 |
| Website / Career / ATS causal recovery | - | 0 / 0 / 0 |

The candidate uplift misses the 25pp gate, and strict recovery misses the 40%
gate. Treatment's lower verified-website recall reflects independent live
transport variance; it is not attributed to the model.

## Candidate Signal

The LLM placed the evaluator reference URL in Top-3 for four records:

| Record | Company | Hypothesis outcome |
|---|---|---|
| 022 | City of Pharr, TX | Correct website/career hypotheses and a GovernmentJobs board entered S5, but hiring relationship remained unverified and the identity contract rejected publication. |
| 032 | Benefis Health System | Correct website/career hypotheses entered verification but the public endpoint returned HTTP 403. |
| 038 | North Dakota Information Technology | Correct nested government website fetched and matched content, but the parent/group ownership contract rejected it without downstream relationship evidence. |
| 088 | Ken Garff Automotive Group | Correct root was Top-3, but domain deduplication and the two-candidate verification budget selected Career variants; both were blocked and the root was not checked. |

All four were absent from the frozen search pool. This is genuine candidate
generation signal, but it is not product recovery: no S2/S5 adoption completed
the required identity chain. Ranker miss is not the limiting factor for these
four records.

## Exact Attribution

Run 008 returned the same two Exact openings in both arms:

- **011 Versana**: Lever tenant `Versana`, title `DevOps Engineer - Raleigh`,
  Raleigh location and opening URL all verify. Treatment made two LLM calls,
  but its `versanatech.com` hypotheses were not adopted. The Exact came from
  the existing verified provider path and contributes zero LLM recovery.
- **045 IMG (International Medical Group)**: first-party `imglobal.com`
  handoff to JazzHR tenant `img`, title and opening URL verify. The LLM made
  zero calls. Provider inventory omitted location, so the target Indianapolis
  location remains unconfirmed even though the existing S7 policy published a
  verified result.

No cache-only, deterministic, or provider bypass is credited to the LLM.

## Safety, Replay And Budget

| Gate | Result |
|---|---:|
| Wrong verified URL | 0 |
| Cross-company adoption | 0 |
| Cross-brand adoption | 0 |
| Cross-tenant adoption | 0 |
| Invented adopted URL | 0 |
| Candidate URL outside frozen source pools | 0 |
| Replay reproduced | 18/18 |
| Replay mismatch / fixture gap | 0 / 0 |
| Calls | 25 / 30 |
| Estimated cost | USD 0.00628894 / 0.05 |

Calls comprise 14 planner and 11 ranker invocations. Three planner calls timed
out. Usage was 22,759 prompt plus 11,081 completion tokens. Per-company LLM
latency was 7.515 seconds P50, 9.047 seconds P95 and 9.047 seconds maximum.

The sealed capture contains 1,631 hashed files. Hash verification passed after
evaluation. A content scan found zero API-key, raw Authorization, raw Cookie,
secret-file or `.env` occurrences. The post-evaluation manifest records the
capture/evaluator commits, report hashes, budget, replay and privacy audit.

Final offline gates pass 2,695 tests with four skipped, the production provider
benchmark 25/25, the resolver benchmark 6/6, and architecture validation with
46 native adapters and zero issues. `git diff --check` is clean.

## Failure Clusters

The 16 non-Exact records have one primary causal classification each:

| Root cause | Count | Records |
|---|---:|---|
| `URL_HYPOTHESIS_MISS` | 5 | 024 SDS International; 047 Necessary Ventures; 067 Team Royal; 083 Jushi Holdings; 097 Systematic Business Consulting |
| `TRANSPORT_FAILURE` | 5 | 018 NYC DSS; 072 RLB; 075 Hays + Sons; 080 City of Sioux Falls; 084 State of Montana |
| `IDENTITY_CONTRACT_REJECTION` | 2 | 022 City of Pharr; 038 North Dakota Information Technology |
| `SOURCE_RECALL_MISS` | 2 | 006 Caesars Entertainment; 081 WICHITA COMPANY LIMITED |
| `VERIFICATION_FAILURE` | 1 | 088 Ken Garff Automotive Group |
| `RANKER_MISS` | 0 | - |
| `INPUT_IDENTITY_INVALID` | 0 | - |
| `EXTERNAL_BLOCKED` | 1 | 032 Benefis Health System |

The five hypothesis misses also had zero correct frozen-search candidates, so
`SOURCE_RECALL_MISS` is a secondary cause. Records 072, 075 and 080 are planner
transport timeouts. Record 032 is a direct external 403 rather than an internal
identity rejection. Record 084 lost a deterministic website under live
transport variance. Pharr correctly rejected an unverified GovernmentJobs
relationship; NDIT is a general nested public-sector identity false negative,
not a company-specific exception. Ken Garff is a verification-allocation
failure, not a ranker miss, because the correct root was already Top-3. Wichita
reached an official job list but produced no target opening candidate.

## Interpretation

The LLM addresses part of the candidate-generation bottleneck: among 14
eligible records, 11 planners completed and four produced a correct reference
URL. That is 4/14 of eligible records and 4/11 of successful planner outputs.
It is still below the fixed cohort gate, and seven successful planners guessed
the wrong company domain.

The experiment therefore supports a narrow conclusion:

> Structured DeepSeek URL hypotheses can add candidates that the current search
> backend misses, but run 008 shows no verified end-to-end recovery and no
> evidence yet that the route is promotable.

The next engineering work, if pursued, should be unpaid and general:

1. make candidate verification resilient to one blocked LinkedIn/public fetch;
2. model nested government ownership without weakening private-company safety;
3. repair frozen search source recall, which was 0/18;
4. retain the LLM only as an audited fallback until a new independent
   experiment is explicitly authorized.
