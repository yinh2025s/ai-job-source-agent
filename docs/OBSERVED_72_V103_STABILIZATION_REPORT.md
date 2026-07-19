# Observed 72 Stabilization Report (.103)

Date: 2026-07-18

This report records an observed development cohort. The 72 LinkedIn job IDs have
already influenced implementation and are not a blind holdout.

## Matched Configuration

The release comparison reuses the `.99` deterministic agent configuration:

- `max_candidates=12`
- `max_career_candidate_fetches=10`
- `max_career_discovery_transport_calls=48`
- `max_career_search_queries=6`
- `max_ats_board_fetches=8`
- `max_job_pages=6`
- `max_job_board_attempts=8`
- three-route candidate discovery enabled

An earlier `.102` diagnostic used a 32-call Career budget and is not used as the
release comparison.

## Funnel

| Metric | `.99` | `.103` matched | Delta |
| --- | ---: | ---: | ---: |
| Verified website | 70/72 | 71/72 | +1 |
| Career page | 55/72 | 60/72 | +5 |
| Verified Job List | 37/72 | 43/72 | +6 |
| S7 Exact Opening | 8/72 | 10/72 | +2 |

The `.103` run took 2,182.4 seconds. Its pipeline outcomes were 10 success, 45
partial, and 17 failed. No batch worker crashed and no malformed exact URL was
published.

## Exact Changes

New S7 exact openings:

- SKIMS: Pinpoint full inventory, exact title and Los Angeles location.
- Bacardi: first-party declared job search and exact title.
- adidas: first-party search action and exact title.

PUMA was exact in `.99`, but both the matched batch and one isolated retry ended
with a verified board plus `NETWORK_TIMEOUT` after approximately 180 seconds. The
current result is not overwritten with historical evidence.

## Board Changes

New verified boards include three Redlands Community Hospital records through the
native HealthcareSource adapter, Horizon Health through ADP, SKIMS through
Pinpoint, Bacardi, and EVONA. The matched run did not publish the speculative
LinkedIn Greenhouse board for one prior record; guessed provider paths remain a
safety-hardening target and must not establish hiring relationship by tenant-name
similarity alone.

## Failure Clusters

### S4 Career discovery

- Five official sites repeatedly return host-wide 401/403 responses. They require
  a bounded denial circuit and an access-block classification, not
  `CAREER_PAGE_NOT_FOUND`.
- Six records may lack a public surface, but a complete bounded search cascade is
  required before treating the negative as conclusive.
- Riverview remains `WEBSITE_NOT_RESOLVED`; low-authority unrelated fetch failures
  must not override unresolved company identity.

### S5 Career to Job List

- Future Beauty Brands has an explicit first-party no-open-positions state.
- Southeastern Renal Dialysis and Steve Madden expose no verified public board in
  the captured evidence and must remain non-exact.
- Square, Yamaha, L'OCCITANE, and Solomon Page expose cross-site actions or dynamic
  portal evidence that is not yet converted into a verified board.
- Elderwood, Gucci, Century Communities, and LTIMindtree discover downstream board
  evidence but fail hiring-relationship publication or complete inventory gates.

### S6/S7 Opening and identity

Only six no-opening outcomes in the 32-call diagnostic had complete enough evidence
to defend a current inventory no-match: Middesk, three Redlands records, Horizon,
and United Pharma. The other records remain incomplete, identity-rejected, or
provider-limited.

Two safety defects have priority over recall:

- A guessed ATS tenant cannot establish a hiring relationship from a matching name
  alone.
- A broad parent/group website inventory cannot prove a subsidiary no-match without
  explicit hiring-entity evidence. The Tata Technologies records must remain
  identity-incomplete rather than authoritative group-inventory no-match.

## Verification

Before the matched run, the release gate passed 2,022 tests (3 skipped), provider
benchmark 25/25, resolver benchmark 6/6, and architecture validation with 43 native
adapters and zero issues. SKIMS and Aveanna targeted live both passed after the
first-party sitemap / ATS / generic-search scheduling order was frozen.

The next gate must rerun after denial-circuit, guessed-candidate relationship, and
parent/group identity safety patches are integrated. A new aggregate live is not
required until those correctness gates pass; targeted records should be used first.

## Post-Benchmark `.104` Safety Gate

The integrated safety patch passed 2,030 tests (3 skipped), provider benchmark
25/25, resolver benchmark 6/6, and architecture validation 43/0.

Targeted live results:

- Tidelands Health now reports `HTTP_FORBIDDEN`, not Career Not Found. The official
  host circuit rejected 11 duplicate requests and left 20 of 48 transport calls
  unused. Generic search still makes the S4 stage take 153.6 seconds, so phase
  latency remains open work.
- Tata Technologies resolves to `tatatechnologies.com`, follows its first-party
  Career handoff to `tatatechnologies.ripplehire.com`, and returns a verified
  provider no-match instead of using broad `tata.com` group inventory.
- LinkedIn does not publish the guessed `job-boards.greenhouse.io/linkedin` tenant;
  it remains `JOB_BOARD_NOT_FOUND` without independent first-party relationship
  evidence.

## `.105` Pre-Regression Repair Wave

Before rerunning the matched cohort, `.105` repairs the generic clusters exposed by
the `.103` trace: continuous first-party Career handoffs, bounded official-host
denial pruning, dynamic JSON and Applicant Manager inventories, Meta title-directed
sampling, scoped SuccessFactors tenant recovery, multi-tenant board portfolios, and
detail-versus-list selection. These are implementation results, not aggregate live
claims; the matched funnel is updated only after the serial regression completes.

## `.109` Matched Regression And `.110` Repair

The serial `.109` matched run completed all 72 records in 1,604.3 seconds. Its
website/Career/Job-List/Exact funnel was `67/53/41/12`. Exact improved by two over
`.103`, but this run is not a clean availability comparison at the earlier stages:
21 records reported `NETWORK_TIMEOUT` and nine exhausted the company deadline,
including previously successful SKIMS, NexCare, United Pharma and Century paths.

Confirmed generic gains include Sony, Elderwood and PUMA Exact openings, plus
verified Job Lists for Square, Yamaha, Meta/Instagram and Paramount. The next
failure review found two deterministic publication defects independent of network
availability: semantic first-party actions such as `Jobs in the house` and
`Explore opportunities` were classified as high-confidence job actions but then
rejected by a second exact-label allowlist; and ADP was missing from the bounded
high-priority link/scoring domain sets. `.110` removes the duplicate wording gate
while retaining the provenance, HTTPS, same-origin landing and inventory gates,
and accepts only registry-identified, listing-classified ADP locators for direct
canonical handoff. Focused live then restored Gucci, LTIMindtree and Steve Madden
to verified Job Lists. Solomon's declared anonymous API remained too large and
slow for the company budget, while Southeastern Renal Dialysis explicitly sends
applicants to Indeed; neither is promoted without official opening evidence.

The full `.110` matched regression completed all 72 records in 1,682.6 seconds at
`67/54/45/11`. Relative to `.109`, Career increased by one and verified Job Lists
by four. Gucci, LTIMindtree and Steve Madden retained their repaired boards in the
full batch. Exact decreased by one because adidas exhausted the company deadline
after an editorial magazine page displaced the real Career portal; this was a
transport/routing failure, not a false identity acceptance.

Post-regression `.111` demotes editorial `/magazine/` routes and sends adidas to
the real Career portal first. The portal itself still timed out in focused live,
so the result remains retryable rather than being promoted. Middesk was the only
remaining `.110` `RESULT_IDENTITY_MISMATCH`: its Ashby posting has San Francisco
as the primary location and New York in `secondaryLocations`. The adapter now
normalizes both fields, and focused `.111` live produced the verified exact
opening in 8.8 seconds. Company, provider, tenant and opening identity gates are
unchanged.

Final `.111` offline gates pass 2,063 tests (3 skipped), provider benchmark 25/25,
resolver benchmark 6/6, and architecture validation with 43 native adapters and
zero issues.
