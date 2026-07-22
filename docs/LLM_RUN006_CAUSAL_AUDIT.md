# Run 006 Causal Audit

Status: Stage A complete; read-only historical audit; no product or prompt
change.

## Scope And Evidence

This audit reconstructs the fixed 18-record `run-deepseek-v4-flash-006` from
the sealed evaluation report, replay result, decision aggregates and surviving
session evidence. It does not reinterpret a website difference between the two
live arms as an LLM effect. The arms performed independent network work, so a
result is causal only when an adopted LLM plan or ranking added the correct
candidate and the normal resolver accepted it.

The original `/private/tmp` run root is no longer present. The surviving report
proves per-record eligibility/call count, decision failure, resolver output,
Job List, Exact and replay outcome. It also proves aggregate planner/ranker
status and duration. It does **not** preserve every query string or each
record's complete Top-10 candidate pool. Those fields are marked `not retained`
rather than reconstructed from company names. One surviving decision example
confirms the expected structured query form for Benefis, but it is not used to
fill missing records.

## Decision Summary

- Fourteen records entered the LLM path. Eleven planners succeeded and produced
  adopted queries plus non-empty candidate pools, but all eleven ranker calls
  timed out. Two planners timed out and one planner response was rejected with
  `OUTPUT_URL_FORBIDDEN` before any query was adopted.
- Four records made zero model calls. Their treatment changes are deterministic
  or live-network variance, not LLM uplift.
- The only Exact, Versana, made zero model calls. Its Exact is valid product
  output but contributes zero LLM recovery.
- Wichita is a baseline identity defect: both arms bound a UK private company
  to Wichita city government. Its simultaneous ranker timeout does not replace
  the earlier shared identity defect as the record's causal class.
- No record can honestly be classified as `RANKER_MISS`: the ranker never
  returned a successful ordering. No record can be assigned a source or
  verification miss from run 006 because the retained evidence does not expose
  a comparable frozen candidate pool.

## Per-Record Chain

Legend: `Y` yes, `N` no, `NR` exact value not retained, `NA` not applicable.
`Q/C` means adopted new query / non-empty candidate pool. A successful planner
followed by a rank invocation proves `Y/Y`, but not that the reference website
was present. `Outputs` lists treatment Website / Job List / Exact.

| ID | Company | Eligible | Planner | Q/C | Reference in pool | Ranker | Ranking used | Resolver | Outputs | Sole causal class |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 006 | Caesars Entertainment | Y | success | Y/Y | NR | timeout | N | no treatment website; baseline had `caesars.com` | N/N/N | `OPERATIONAL_FAILURE` |
| 011 | Versana | N | NA | N/N | NA | NA | N | accepted deterministic `versana.io` | Y/Y/Y | `DETERMINISTIC_OR_NETWORK_VARIANCE` |
| 018 | NYC Department of Social Services | Y | success | Y/Y | NR | timeout | N | no website | N/N/N | `OPERATIONAL_FAILURE` |
| 022 | City of Pharr, TX | Y | timeout | N/N | NA | NA | N | no treatment website; baseline had `pharr-tx.gov` | N/N/N | `OPERATIONAL_FAILURE` |
| 024 | SDS International, Inc. | N | NA | N/N | NA | NA | N | accepted deterministic `sdslink.com` | Y/Y/N | `DETERMINISTIC_OR_NETWORK_VARIANCE` |
| 032 | Benefis Health System | Y | success | Y/Y | NR | timeout | N | no website | N/N/N | `OPERATIONAL_FAILURE` |
| 038 | North Dakota Information Technology (NDIT) | N | NA | N/N | NA | NA | N | accepted deterministic `ndit.nd.gov` | Y/N/N | `DETERMINISTIC_OR_NETWORK_VARIANCE` |
| 045 | IMG (International Medical Group) | Y | success | Y/Y | NR | timeout | N | no website | N/N/N | `OPERATIONAL_FAILURE` |
| 047 | Necessary Ventures | Y | success | Y/Y | NR | timeout | N | no website | N/N/N | `OPERATIONAL_FAILURE` |
| 067 | Team Royal | Y | success | Y/Y | NR | timeout | N | no website | N/N/N | `OPERATIONAL_FAILURE` |
| 072 | Rider Levett Bucknall RLB | Y | timeout | N/N | NA | NA | N | no website | N/N/N | `OPERATIONAL_FAILURE` |
| 075 | Hays + Sons | Y | success | Y/Y | NR | timeout | N | no website | N/N/N | `OPERATIONAL_FAILURE` |
| 080 | City of Sioux Falls | Y | success | Y/Y | NR | timeout | N | no website | N/N/N | `OPERATIONAL_FAILURE` |
| 081 | WICHITA COMPANY LIMITED | Y | success | Y/Y | NR | timeout | N | accepted wrong `wichita.gov` in both arms | Y/Y/N | `BASELINE_IDENTITY_DEFECT` |
| 083 | Jushi Holdings Inc. | Y | rejected: URL output | N/N | NA | NA | N | no website | N/N/N | `OPERATIONAL_FAILURE` |
| 084 | State of Montana | Y | success | Y/Y | NR | timeout | N | no website | N/N/N | `OPERATIONAL_FAILURE` |
| 088 | Ken Garff Automotive Group | N | NA | N/N | NA | NA | N | accepted deterministic `kengarff.com` | Y/Y/N | `DETERMINISTIC_OR_NETWORK_VARIANCE` |
| 097 | Systematic Business Consulting | Y | success | Y/Y | NR | timeout | N | no website | N/N/N | `OPERATIONAL_FAILURE` |

Class totals: `OPERATIONAL_FAILURE` 13,
`DETERMINISTIC_OR_NETWORK_VARIANCE` 4, `BASELINE_IDENTITY_DEFECT` 1,
all other classes 0.

## Operational Root Cause

The experiment runner exposed a nominal 15-second LLM budget while the
low-level DeepSeek transport used a hidden 3-second socket timeout. Planner
successes took 3.54-5.74 seconds. Search then consumed more of the shared
deadline, and every attempted ranker failed after 4.28-5.30 seconds. This was
not evidence that the ranker selected the wrong candidate; it never returned a
selection.

The audit ledger also over-counted usage on failures. Query-plan timeout records
showed 537 and 593 tokens, even though a failed call should contribute zero in
the per-call audit. The provider capture ledger reported 4,747 prompt and 1,988
completion tokens, while the evaluator summed 5,535 and 2,330. The discrepancy
is consistent with a client retaining the previous invocation's usage state.

## Consequences For The Plan

1. Run 006 cannot support a source-recall or ranker-quality conclusion.
2. Stage B must version transport and phase budgets, reserve executable ranker
   time, reset usage before every call and record explicit causal fields.
3. Stage C must freeze search responses per query and use an identical candidate
   pool for deterministic and LLM rankers. Query planner source recall and
   conditional ranker recall must be reported separately.
4. Future sealed reports must retain the query ledger and candidate pool in a
   durable experiment bundle. A temporary directory path is not sufficient
   historical evidence.
5. Existing run 007 remains the one completed formal rerun. This audit does not
   authorize another paid formal A/B, Fresh100 or blind cohort.
