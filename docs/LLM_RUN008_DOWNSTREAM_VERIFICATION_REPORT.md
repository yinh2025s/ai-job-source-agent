# Run 008 Downstream Verification Audit

Date: 2026-07-28

This is a read-only post-experiment audit of the sealed Run 008 capture. It
does not make a model call, tune a prompt, run Fresh100 or a blind holdout,
modify `main`, or change the feature flag.

## Decision

Pause the LLM route. Do not request Run 009 yet.

Run 008 proved a candidate-generation signal, but no proposed downstream
cluster meets the fixed implementation threshold:

- at least three independent companies or a provider-family protocol;
- one trigger and one production code path;
- one general fix expected to restore at least three correct terminal states;
- positive and safety-negative evidence;
- no company exception or identity relaxation.

Consequently Stage C implementation and Stage D focused live/replay are not
applicable in this goal. Implementing a one-record fix would violate the
experiment contract and would not make the next paid run causally useful.

## Frozen Evidence

The authoritative artifact root is:

`/Users/yinhuang/.codex/visualizations/2026/07/20/019f8029-8c5e-77b2-9fe6-d357b476e283/ai-job-llm-url-hypothesis-run008-20260727`

The capture remains at commit `f755fc7`. The capture manifest contains 1,631
sealed files; all 1,631 hashes verify. Replay remains 18/18 reproduced with
zero mismatch and zero fixture gap. The capture was not modified.

Evidence abbreviations used below:

- `B`: `baseline/trace.json`
- `T`: `treatment/trace.json`
- `D`: `treatment/decisions/llm-decisions.jsonl`
- `C`: `treatment/candidate-records.json`
- `SF`: `treatment/snapshots/fetch-failures.jsonl`
- `SS`: `treatment/snapshots/snapshots.jsonl`
- `E`: `evaluation-report.json`
- `F`: `failure-classification.json`
- `R`: `replay/replay-summary.json`

## Closure Matrix

`Executed` means that the relevant candidate was actually fetched or entered
the provider path. A correct hypothesis that was merely ranked is not a
recovery.

| ID | Hypothesis / Top-3 | Executed request and transport | Rejecting gate and correctness | Budget owner / downstream candidate / general recovery |
|---|---|---|---|---|
| 006 Caesars | No LLM hypothesis; deterministic `caesars.com` existed | `jobs.caesars.com` timed out during TLS | S4 `CAREER_PAGE_NOT_FOUND`; fail-closed result was correct | S4 verification budget; Website existed, but no sealed Exact candidate; possible Partial only, not a qualified cluster. `B/T/SF` |
| 011 Versana | LLM Top-3 was wrong: `versanatech.com`, its Career path and `aur.edu` | Wrong root hit TLS failure and was not adopted | Existing Lever tenant `Versana` passed S5-S7 independently | Exact is deterministic and contributes zero LLM recovery. `D/C/T/E` |
| 018 NYC DSS | No Top-3; planning was never reached | `GET https://lnkd.in` handshake timed out | S2 `NETWORK_TIMEOUT`; correct fail-closed outcome | S2 transport budget; no sealed correct downstream candidate. `T/SF/F` |
| 022 City of Pharr | Correct root, `/jobs`, and GovernmentJobs board were proposed | Root returned HTTP 403; GovernmentJobs entered S5, then inventory timed out | S7 correctly rejected missing hiring relationship/provider continuity | S5 provider/relationship budget; correct board exists, but relationship and inventory both remain open. One record only. `D/C/T/SF/E` |
| 024 SDS | Top-3 was wrong: `sdsinternational.com` variants and Chemical Safety | Wrong Career/apex failed with TLS EOF | No adoption; rejection was correct | S2 verification budget; baseline had `sdslink.com/careers`, but treatment evidence preservation is a different problem. `B/T/C/F` |
| 032 Benefis | Correct Career and root were Top-3; Allrecipes was third | Correct Career and Allrecipes were fetched and returned HTTP 403; root was not fetched | External endpoint blocked verification; no identity promotion | S2 slots plus external block; a root reservation might recover Website only, with no sealed Exact. `D/C/T/SF/E` |
| 038 NDIT | Correct Career/root and a wrong North Face URL were Top-3 | Correct Career fetched and redirected to `/about-us/careers`; content confirmed NDIT | Parent/group ownership contract rejected the nested agency; this is a false negative, but relaxing the contract would be unsafe | S2 identity owner; correct Website/Career exists, opening recovery is not proven. One common-path peer at most. `C/T/SS/E` |
| 045 IMG | No LLM call | Deterministic first-party handoff reached JazzHR and produced an opening | Existing S7 published Exact with `location_classification=missing`; this is a latent fail-open | S7 publication owner; correct closure is downgrade until Indianapolis is evidenced. Main has fixed this generic safety rule, but Run 008 supplies only one affected company. `T/E/R` |
| 047 Necessary Ventures | Top-3 was wrong: `necessaryventures.com` variants and iCIBA | Wrong domain failed certificate validation | No adoption; rejection was correct | S2 verification budget; baseline had `necessary.vc -> jobs.necessary.vc`; treatment evidence preservation is not an LLM recovery. `B/T/C/F` |
| 067 Team Royal | All Top-3 URLs were wrong `teamroyal.com` variants | Wrong root hit TLS EOF | No adoption; rejection was correct | Hypothesis/source miss; correct `royal.us` was absent, so downstream verification cannot recover it. `D/C/T/F` |
| 072 RLB | Planner timed out; no Top-3 | Query-plan timeout plus LinkedIn HTTP 999 | No candidate reached verification | Planner and S2 transport budgets; baseline had `rlb.com/jobs`, but no sealed treatment candidate. `D/B/T/F` |
| 075 Hays + Sons | Planner timed out; no Top-3 | Query-plan timeout plus LinkedIn HTTP 999 | No candidate reached verification | Planner and S2 transport budgets; no sealed correct downstream candidate. `D/T/F` |
| 080 Sioux Falls | Planner timed out; no Top-3 | Query-plan timeout plus LinkedIn HTTP 999 | No candidate reached verification | Planner and S2 transport budgets; a planner recovery would not prove a terminal result. `D/T/F` |
| 081 Wichita | No LLM call | Deterministic `wichita.co.uk -> Regal Rexnord /jobs`; five title-search routes ended as `unsafe_next_url` or `single_page_unbounded` | S6 correctly refused an unsupported opening | S6 inventory owner; official board exists, target opening is absent from sealed evidence. `T/R/F` |
| 083 Jushi | Top-3 was wrong: `jushi.com` variants and IMDb | Wrong root hit handshake timeout/TLS EOF | No adoption; rejection was correct | Hypothesis/source miss; correct `jushico.com` was absent. `D/C/T/F` |
| 084 Montana | Proposed `montana.gov`, `statecareers.mt.gov`, and `state.gov`; none was label-exact | First two timed out; `state.gov` returned but was the wrong parent surface | No adoption; identity rejection was correct | S2 transport budget; baseline had `mt.gov`, so this is deterministic evidence variance rather than downstream LLM recovery. `B/C/T/SF/F` |
| 088 Ken Garff | Correct root was Top-3 number two | Slots went to `/careers` and `careers.kengarff.com`; both returned HTTP 403; root was not fetched | S2 allocation prevented root verification | S2 `_allocate_verification_slots`; root reservation may recover Website only, not a sealed opening. One proven company only. `D/C/T/SF/E` |
| 097 Systematic BC | Top-3 contained wrong long-domain variants and Merriam-Webster | Wrong domain hit TLS EOF; third candidate returned HTTP 403 | No adoption; rejection was correct | Hypothesis/source miss; correct `systematicbc.com` was absent. `D/C/T/F` |

The completion requirement refers to the four correct hypotheses and the
remaining non-Exact records. The cohort itself has 18 records: two Exact and
16 non-Exact. The matrix above therefore covers all 18 exactly once.

## Cluster Qualification

The apparent three-record scheduling cluster does not survive causal review:

- Benefis executed its correct Career URL and was blocked by HTTP 403.
- NDIT executed and read its correct Career URL, then failed identity.
- Ken Garff alone failed because the correct root received no slot.

They share S2 as a stage, not a trigger or production failure path.

| Candidate cluster | Evidence | Expected correct terminal recoveries | Decision |
|---|---|---:|---|
| Same-site variants consume root verification | Ken Garff; Versana is a second, materially different source-crowding case | At most 1 proven | Reject |
| Nested public-sector ownership | NDIT and NYC DSS; Lubbock/Pharr use different redirect or relationship paths | 0 proven full chains | Reject |
| Provider board lacks hiring continuation | Pharr; Slant is a prior success, not another failure | At most 1 | Reject |
| Blocked candidate consumes independent slots | Ken Garff, Versana and Loveland have different triggers | 0 proven | Reject |
| Deterministic Website varies between arms | Independent live requests, not one reproducible request path | 0 | Reject |
| Missing location still publishes Exact | IMG only | 1 safety downgrade, 0 recoveries | Reject for this goal |
| Planner/verification/transport timeout attribution | Three planner timeouts, but no evidence their output would contain a correct terminal candidate | 0 | Classify only |

The historical four-company public-domain candidate cluster proves that
authoritative `.gov` discovery can improve Website candidates. It does not
prove three Website-to-Career-to-Opening terminal recoveries and therefore
cannot satisfy this downstream closure gate.

## Main Comparison

Read-only comparison used clean commits `main@8ae36ef` and
`codex/llm-candidate-reasoning-foundation@2cc07fc`.

| Capability | Main | LLM branch |
|---|---|---|
| Native adapters | 48 | 46 |
| Public-sector discovery | Authoritative public-domain registry and typed identity | Absent |
| Provisional Career continuation | First-party provisional evidence and bounded continuation | Absent |
| Relationship evidence | Route-local evidence; tenant similarity cannot authorize | Older contract |
| Transport budgeting | Stage reservation and typed exhaustion | Older shared budget behavior |
| Location/S7 | Missing specific location fails closed unless title/URL proves it | Unique candidate can still publish with missing location |
| LLM URL hypotheses | Absent by design | Present, default off |
| Ken-style root scheduling | No proven generic fix | No proven generic fix |

Main makes the LLM integration architecture stale, but it does not replace the
candidate-generation capability. A future experiment would need to integrate
the zero-trust hypothesis layer with the newer deterministic backend rather
than continue prompt work on this old pipeline. The newer behavior is a
multi-module contract slice, not a safe bulk cherry-pick into this branch.

## Eligibility Re-evaluation

No sealed score is rewritten:

- raw LLM hypothesis recall: 4/18;
- correct hypotheses completing strict verification: 0/4;
- strict causal offline recoveries: 0/18;
- Website/Career/ATS causal contribution: 0/0/0;
- direct external block: 1 (Benefis);
- primary URL-hypothesis misses: 5;
- wrong verified URL, cross-company, cross-brand, cross-tenant and invented
  adopted URL: all 0;
- replay: 18/18, zero mismatch and zero fixture gap.

Run 009 prerequisites are not met: no three-company downstream cluster was
closed, and fewer than three of the four correct hypotheses can complete the
strict chain on frozen evidence. No new experiment hypothesis, budget or
latency proposal should be submitted yet.

## Engineering Gates

- sealed capture verification: 1,631/1,631;
- unit tests: 2,695 passed, 4 skipped;
- provider benchmark: 25/25;
- resolver benchmark: 6/6;
- architecture validation: 46 native adapters, 0 issues;
- `git diff --check`: clean.

The first sandboxed full-suite run passed all ordinary tests but could not bind
the loopback socket used by `test_extension_bridge_http`; its five tests then
passed in an approved local-network execution. This was an environment
permission boundary, not a product assertion failure.

## Audit Boundary

No paid call, live benchmark, Fresh100 run, blind holdout run, merge, rebase or
main modification occurred. One delegated read-only comparison initially used a
broad `git grep -- docs` command. It produced no blind content in its output,
but the command may have traversed a blind protocol file while searching the
tree. A later main-thread command searching documentation for benchmark command
names also displayed the blind protocol filename and two aggregate historical
gate lines. No blind cohort record, label, per-record result or answer was
opened, displayed, executed or used in this analysis. Both broad searches are
recorded as process-compliance exceptions; this report does not claim absolute
zero-touch blind-document compliance.

Future audits must use an explicit allowlist of code/test/report paths rather
than a repository-wide docs search.
