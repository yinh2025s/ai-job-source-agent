# 100-Posting Three-Route Live Evaluation

Run date: 2026-07-17

## Objective

Evaluate the same 100 live LinkedIn job postings through all three candidate
discovery routes. A posting counts as an exact success when at least one route
can be attributed to the final S7-verified opening. Candidate URLs, search
snippets, and unverified boards do not count as success.

## Frozen Cohort

- 100 distinct LinkedIn job IDs from 73 companies.
- United States location, across 10 job families.
- Up to 15 postings per query and two public search pages.
- Cohort digest: `1626dc8e1f38a18ed01e33f47b89ecfa0df7ea05d016bdde108cb6322206187c`.
- Run configuration digest: `d5e3aefa9ecfa605dfd1866456c18edad0567d2deac5b0db36c5bfc2f4822a19`.
- Serial live gate with two bounded company workers; elapsed time was about
  48 minutes 33 seconds.

## Results

| Route | Input coverage | Candidate produced | Relationship verified | Exact, overall | Exact after verified relationship |
| --- | ---: | ---: | ---: | ---: | ---: |
| LinkedIn External Apply | 0/100 | N/A | N/A | N/A | N/A |
| Provider-targeted search | 99/100 | 19/99 | 19/19 | 11/100 | 11/19 |
| Website / Career exploration | 98/100 | 59/98 | 58/59 | 24/100 | 24/58 |
| At least one route | 100 | 68 | 67 | **28/100** | N/A |

External Apply is reported as unavailable, not as a 0% algorithmic success
rate. None of the 100 public LinkedIn detail pages exposed a usable External
Apply URL: 97 explicitly lacked one and three detail fetches failed.

Exact overlap:

- Provider search only: 4.
- Website / Career only: 17.
- Both provider search and Website / Career: 7.
- Neither: 72.

The two active routes are therefore complementary: provider search adds four
exact openings that Website / Career did not recover, while Website / Career
adds seventeen that provider search did not recover.

## Funnel And Failure Context

The complete pipeline resolved 98 websites, 85 career pages, 67 verified job
lists, and 28 exact openings. Final pipeline outcomes were 28 success, 53
partial, and 19 failed. The largest unresolved groups were 16 job-board-not-
found outcomes and 13 career-page-not-found outcomes. These numbers describe
recall gaps; they do not relax provider, tenant, hiring-relationship, title,
location, inventory, or S7 identity checks.

All 100 route traces were structurally valid. Offline scoped replay reproduced
98 outcomes and reported two mismatches: Stark Pharma retained the same visible
URLs but changed typed identity outcome, while Actabl changed from a live company
budget exhaustion to a replay partial/not-run boundary. The live exact result
remains the recorded result, but these mismatches mean the run is not yet a
perfect deterministic-reproduction gate.

Final offline gates pass 1,749 tests (three skipped), the 25/25 provider
benchmark, the 6/6 resolver benchmark, and architecture validation with 33
native adapters and zero issues.

## Interpretation

The requested OR objective is implemented and measurable: the union is 28%,
not the maximum of three loosely measured scrapers. Each exact result is tied
to route evidence and the same final identity contract. The evaluation does
not claim human-labelled URL precision, because this cohort was collected and
evaluated live rather than independently labelled before the run.

Detailed artifacts:

- `samples/evaluation/live100_three_route_cohort_20260717.json`
- `samples/evaluation/live100_three_route_metrics_20260717.json`
- `samples/evaluation/live100_three_route_records_20260717.csv`
- `docs/LIVE_100_THREE_ROUTE_MANUAL_REVIEW.md`
- `samples/evaluation/live100_three_route_manual_review_72_20260717.csv`
