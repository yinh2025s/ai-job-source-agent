# Frozen 100 `.120` Regression

Run date: 2026-07-18/19

## Stable Result

The frozen observed cohort ran with the exhaustive External Apply, targeted ATS
search and Website/Career candidate routes. A clean first pass completed all 100
records; a second pass restored 64 non-retryable completions and resubmitted only
36 retryable records.

| Stage | `.117` | `.120` first | `.120` retry | Delta vs `.117` |
| --- | ---: | ---: | ---: | ---: |
| Verified website | 57 | 70 | 70 | +13 |
| Career page | 44 | 58 | 58 | +14 |
| Verified Job List | 40 | 55 | 57 | +17 |
| S7 Exact opening | 27 | 45 | 46 | +19 |

Every published opening passed S7. Randstad, Snap and Taskrabbit candidates with
insufficient or conflicting identity/location evidence remained suppressed.

## Stable Non-Exact Clusters

The 54 non-Exact records end at:

- 29 S2 `FETCH_FAILED`.
- 8 `CAREER_PAGE_NOT_FOUND`.
- 2 Career `COMPANY_TIME_BUDGET_EXHAUSTED`.
- 1 `WEBSITE_NOT_RESOLVED`.
- 5 `JOB_BOARD_PORTFOLIO_INCOMPLETE`.
- 3 verified `OPENING_NOT_FOUND`.
- 3 `RESULT_IDENTITY_MISMATCH` fail-closed terminals.
- 1 official inventory `HTTP_FORBIDDEN`.
- 1 `JOB_BOARD_NOT_FOUND`.
- 1 additional verified external/availability terminal represented in the stage
  summary.

Forty of 54 non-Exact records do not reach opening inventory. The next system
priority is company-level verified S2-S5 evidence coalescing and safe upstream
bypass, not further title simplification or per-company URL rules.

## Proven Improvements

Both hackajob postings, Caudalie and two BBVA postings execute the Career-to-list
search flow identified in manual review and now publish Exact openings. Meta and
Instagram use verified static identity handoff plus official inventory and reach
15/15 Exact. SpaceX recovers Exact on checkpoint-aware retry. Steve Madden resumes
directly at S6 and checks both official ADP inventories; both are currently empty,
so no stale opening is published.

## Replay Defect

Results, traces, summary and route metrics were written before automatic failure
bundle generation. Strict scoped replay then rejected four unconsumed ADP inventory
requests. The live metrics above remain valid; the bundle divergence must be fixed
without weakening outcome-tape completeness before the replay gate can pass.

## Local Artifacts

- `/private/tmp/frozen100-v120-results.json`
- `/private/tmp/frozen100-v120-trace.json`
- `/private/tmp/frozen100-v120-summary.json`
- `/private/tmp/frozen100-v120-routes.json`

Large live artifacts and snapshots remain local and are not committed.

## `.122` Focused Follow-up

The formal score above remains unchanged pending a unified rerun. Generic detail
enrichment was tested against a fresh live capture for Snap and Randstad and then
replayed offline: both now reach Exact. Snap's Taiwan same-title detail is rejected
and the Los Angeles detail is selected from page-bound public location state;
Randstad is selected only after its canonical detail verifies Malvern,
Pennsylvania. S2 region/ownership hardening and dynamic-inventory taxonomy fixes
have passed focused tests but are not counted in the frozen-100 metric yet.

The ten-record S2/detail live gate then confirmed Blossom and Snap as Exact.
Seven records stopped on retryable LinkedIn company-page `451/999` evidence;
their provider-search route still ran but produced no verifiable ATS candidate in
the current search region. Taskrabbit exposed a real resolver defect: two
same-brand sites were live and the richer Organization metadata on `.ai` beat the
corporate `.com`. `.123` adds a generic verified `.com` tie-break with parked and
unverified negative controls; its focused live result is required before the next
formal frozen-100 run.

The old `.120` full tape cannot prove `.122` determinism: replay reaches Twitch
and then finds unconsumed S2 requests because the resolver's request sequence
changed across adapter versions. Outcome-tape completeness remains strict. The
next full live must create a same-version `.123` capture and replay that capture
without ignored entries.

Taskrabbit `.123` live then selected `https://www.taskrabbit.com/`, reached the
official Career page and Greenhouse board, and read all 13 current openings. The
frozen FP&A title is absent, so the correct terminal is verified no-match. `.124`
also aligns the selected Greenhouse job list with its provider/tenant identity;
the first-party inventory source URL is retained as relationship evidence rather
than leaving a stale generic Career identity in the merged route.

## `.125` Unified Follow-up

A clean `.125` frozen-100 run completed at website/Career/Job List/Exact
`57/50/48/45`. Resubmitting only its 46 retryable completions recovered SpaceX and
stabilized at `57/50/49/46`. Snap and Randstad were Exact in the unified cohort,
so their generic detail-location recovery is no longer focused-only evidence.
The run had substantially worse public LinkedIn and transport availability than
`.120`; 43 final records retained `FETCH_FAILED`.

Two remaining results are code defects rather than network terminals. Taskrabbit
published `taskrabbit.ai` after the same-brand corporate `.com` timed out, even
though a separate focused run had correctly reached the `.com`, Greenhouse and a
verified no-match. Sezzle deterministically crashed because a legacy Greenhouse
board host and its canonical evidence host differed. `.126` makes the former
identity ambiguity fail closed and normalizes the latter as one typed board.
Neither repair contains a company-specific override. A fresh `.126` focused live
and same-version replay are required before another unified frozen-100 run.

The `.126` focused live then closed both defects. Sezzle selected
`https://job-boards.greenhouse.io/sezzle`, read 185 complete Greenhouse records and
published the S7-verified Financial Analyst detail. Taskrabbit selected
`https://www.taskrabbit.com/`, reached its canonical Greenhouse board and returned
complete-inventory `OPENING_NOT_FOUND`; no `.ai` URL was published. The automatic
same-version full-outcome replay reproduced 2/2 records and passed the strict
outcome gate with no fixture gap or mismatch.

## `.127` Cross-Run Company Evidence Recovery

A job-URL-aligned audit of the 43 `.125` S2 `FETCH_FAILED` records found 17
postings across 11 companies whose website or deeper discovery chain had already
been verified in `.120`. The later run lost those facts because stage checkpoints
were intentionally isolated and public LinkedIn company requests were denied or
timed out. The other 25 comparable records failed S2 in both versions and are not
projected as recoveries.

ADR-0028 therefore adds a separate, version-independent store for verified public
company discovery candidates. Website, Career and provider-board layers expire
independently after 30 days and are always re-fetched through the current resolver,
Career discovery and provider registry before publication. Deterministic rejection
invalidates the affected layer and descendants; retryable transport failure retains
the candidate. The store never contains an exact opening, inventory, raw page,
credential, cookie, token or durable negative result. A historical migration is
allowed to seed only previously verified candidates with a manifest; it cannot seed
success and cannot bypass S7.

S2, S4, S5, CLI, live batch and extension bridge wiring is complete under adapter
`.127`. Generic `.org` candidate rotation and branded Career microsite discovery are
also included. Focused live for the 17 regression records and the next unified
frozen-100 run are pending, so the formal score remains `.125` at 46 Exact.

## `.130` Stored-Provider Recovery Follow-up

The first clean migration stored 46 Website, 35 Career and 15 provider-board
identities. Result-side provider relationships are accepted only when the prior
hiring evidence is verified, failure codes are empty, the relationship method is
approved and the current adapter canonicalizes the same provider, tenant and board.

A three-record Gucci/Haystack focused live changed verified Job List publication
from `0/3` to `2/3`. Both Haystack postings reached the current complete Ashby
inventory for `deepsetai`; the target title is absent, so they remain honest
verified no-match partials. Gucci reached the correct Kering/Gucci Workday board,
but the current provider API timed out. Its candidate remains hidden and retryable;
it is not counted as recovered. Automatic replay now freezes the selected public
company-discovery records so replay uses the same non-network input as live.

The 17-record recovery cohort, same-version replay and unified frozen-100 live are
still required. The formal score therefore remains `.125` at 46 Exact.

## `.133` Declared-Inventory Stage Handoff

The Acorns/Solomon focused cohort first confirmed Acorns as an Ashby Exact and
showed that Solomon Page's official same-origin API returned 326 current records,
including `Data Analyst` in Austin. Two generic defects still hid that opening:
navigation-link text penalties were applied to evidence-derived `/job/{id}` URLs,
and the enriched inventory page verified in S5 was not reconstructed after the
stage/checkpoint boundary.

`.133` separates declared-route validation from navigation scoring and rebuilds
the bounded public inventory in S6. A clean Solomon Page live now publishes
`https://opportunities.solomonpage.com/job/458677`; company, hiring entity,
generic provider tenant, board, opening, complete 326-record selection and title
all pass S7. The automatic full bundle replays 1/1 as `reproduced`, with record
integrity and outcome gates both passing. The replay builder also freezes the
pre-run company-evidence store, so evidence learned during a live worker cannot
shorten the same run's replay path.

Five remaining `.130` S2 failures share a regional stored-website cluster:
Lacoste, SKIMS, Michael Kors, Saint Laurent and adidas have verified historical
websites, but those candidates resolve to non-US locales for US postings. The
current implementation inspects visible locale anchors only; real frozen
snapshots for SKIMS and Lacoste declare their US roots with standard
`rel=alternate` / `hreflang=en-US`. The next generic repair consumes that
same-site evidence under the existing HTTPS, registrable-site, bounded-candidate
and current-page identity gates. It must not guess locale paths or hardcode a
company. Focused results still do not change the formal frozen-100 score, which
remains `.125` at 46 Exact until the unified gate runs.

## `.136` Regional Handoff And Pinpoint Discovery

The first five-brand run under `.134` remained `0/5` because the running process predated
the deployment-gateway change and because a declared US SKIMS root was geo-redirected to
the Singapore locale by the current network. `.135` narrowed that exception to explicit
US-locale evidence, same-registrable-site redirects and a currently positive company page.
SKIMS then advanced from `FETCH_FAILED` to `CAREER_PAGE_NOT_FOUND`, proving that S2 changed
but also showing that the product chain was not yet closed.

The next trace exposed a registry/discovery reachability defect: the Pinpoint adapter could
parse and verify inventory but neither targeted ATS query planning nor the verified tenant
probe included Pinpoint. `.136` adds provider-wide Pinpoint discovery. A clean focused live
resolved `https://skims.com/`, verified `https://skims.pinpointhq.com/`, and published
`https://skims.pinpointhq.com/en/postings/138e3bc0-c85e-40ac-9d0e-e4a7d693a7ac` with an S7
`verified` verdict. The run was 1/1 Website, Career, Job List and Exact in 38.2 seconds.

Automatic replay of the preceding `.135` batch initially diverged because a second-generation
`replay_input` retained the prior run's derived website and skipped the frozen pre-run evidence
path. Scoped input reconstruction now includes that canonical source kind. Rebuilding the bundle
reproduces both Lacoste and SKIMS outcomes 2/2 with record-integrity and outcome gates passing.
Lacoste still lacks a verified public official opening chain in this network, and Michael Kors,
Saint Laurent and adidas have not yet passed `.136` focused live. These results therefore do not
change the formal frozen-100 score, which remains `.125` at 46 Exact.

## `.140` Lacoste End-To-End Closure

Three focused Lacoste iterations made the difference between code coverage and product proof
visible. `.137` still failed S2 because the regional helper was wired to preferred/search
candidates but not the stored-evidence branch. `.138` resolved the US website but S4 retained
the official host 403. `.139` safely allowed ATS or same-registrable-site search leads after an
official denial, but the configured China-facing Bing endpoint ignored the query and returned
irrelevant results. The resolver had independently generated `https://careers.lacoste.com`, yet
five higher-scoring same-host path guesses consumed the verification window.

Scheduler v8 now reserves one speculative concrete-host subdomain probe while preserving all
existing identity and page verification gates. `.140` completed in 13.1 seconds with website
`https://www.lacoste.com/us/`, Career `https://careers.lacoste.com/en`, DigitalRecruiters Job List
`https://careers.lacoste.com/en/annonces`, and exact opening
`https://careers.lacoste.com/en/annonce/4371325-account-executive-10016-new-york`. S7 verified the
company, provider, tenant, board, title, location and opening chain. Automatic full replay is
1/1 reproduced with zero fixture gaps or outcome mismatches. This focused result is product
evidence but does not change the formal frozen-100 score before the unified run.

## `.141-.144` Three-Brand Regional And Hiring-Entity Recovery

The separate Michael Kors, Saint Laurent and adidas focused cohort was `0/3` Exact under
`.140`. `.141` advanced one record to Job List. `.142` resolved Website for all three and
published adidas as S7-verified Exact. Those are version-specific focused funnels: they must
not be combined with one another or with earlier focused results to imply a higher 100-record
score.

The shared repair accepts a regional ccTLD sibling only when current page evidence verifies the
same brand and the existing regional, registrable-site and identity checks remain continuous. A
currently access-controlled sibling may hand off to that verified route for the current run, but
does not become durable proof by itself. An explicit location qualifier in an official opening URL
may contribute bounded location evidence; it does not bypass title, company, hiring-entity,
provider, tenant, board or opening validation.

Michael Kors' official Career chain verifies Capri Holdings as its hiring entity. The candidate
portfolio rejects a non-production Eightfold tenant, and opening selection consumes already
discovered inventory before spending the remaining window on a redundant CTA. `.144` then
published Michael Kors as focused Exact. This is a verified relationship, not a rule that treats
every parent-company board as valid for a brand.

## `.148-.152` Saint Laurent Exact And Replay Closure

Saint Laurent's official Career chain independently verifies Kering as the hiring entity. Negative
talent-community wording is filtered rather than promoted as a jobs command, while a first-party
`/job-offers/<scope>/<slug>` route can be recognized as a detail only after the normal first-party
opening checks pass. `.148` still selected evidence polluted by a sandbox route. `.149` recovered
the official Career and Job List but did not publish an Exact opening.

`.151` reached a verified Saint Laurent Exact, but its same-version replay still failed identity:
the generic board carried a temporary search query and the query changed between capture and
replay. `.152` keeps that operational query out of stable tenant identity while preserving it as
request/search provenance. The focused live is Exact `1/1`, same-version replay succeeds `1/1`,
and the run completes in 16.8 seconds.

These focused adidas, Michael Kors and Saint Laurent results are acceptance evidence for the
shared repairs only. No unified 100-record run has been performed after them. The formal
frozen-100 baseline therefore remains `.125` at `46/100` Exact; the 17-record recovery cohort,
same-version replay and unified frozen-100 gate are still pending.

## `.153` Same-Version Three-Brand Gate

The first `.152` combined rerun kept Michael Kors and Saint Laurent Exact but adidas exhausted
the S2 website budget before reaching Career discovery. A direct resolver trace showed that the
low-evidence `adidas.com` guess consumed repeated network timeouts even though independent search
evidence later verified `adidas-group.com`. `.153` records adidas Group's official website and
Career handoff in the same verified company-identity registry already used for other public brand
relationships. It does not store or select a particular opening; S5, provider inventory, title and
location matching, and S7 remain mandatory.

A clean `.153` combined batch produced Website, Career, Job List and Exact `3/3`: Michael Kors via
Capri Workday, Saint Laurent via Kering's first-party inventory, and adidas via adidas Group Careers.
All three S7 verdicts are verified, the full replay bundle reproduces `3/3`, and the batch completes
in 31.9 seconds. This is the first same-version acceptance result for that three-record focused
cohort. It is not a frozen-100 rerun. The formal baseline remains `.125` at `46/100` Exact, and the
generic low-evidence guessed-domain retry budget remains an open scheduler issue for the recovery
cohort rather than being declared solved by the adidas identity record.

## `.154-.155` Recovery Cohort Gate

The next shared 17-record recovery cohort completed under `.155` with 17/17 official Job Lists,
10 Exact, 7 Partial and 0 Failed; the same-version replay reproduced 17/17. Hadrian and Gucci moved
to S7-verified Exact. Four LinkedIn records ended in complete official SmartRecruiters no-match:
three title-filtered inventories returned zero records, while the fourth exact title existed only
in Detroit rather than the LinkedIn target of Chicago. These are evidence-backed current outcomes,
not system failures that may be converted to Exact.

The remaining two Haystack records and Solomon Page exposed generic capability defects. Haystack
lost card-local locations and stopped at an internal three-page cap even though the configured page
budget was eight. Solomon Page's complete 326-record JSON inventory contained `Data Analyst`
`458677` and `metro: Austin, TX`, but the location was discarded and later detail requests timed out.

## `.156-.157` Location And Pagination Closure

`.156` preserves the most specific public inventory location through dynamic inventory, embedded
inventory, listing candidates and raw links. With the previously verified Solomon Page website
frozen as upstream evidence, live S5-S7 selected
`https://opportunities.solomonpage.com/job/458677`, classified the location as exact Austin, and
passed the full S7 identity chain and 1/1 replay. A separate clean S2 attempt failed before this code
path because LinkedIn returned HTTP 999 and that invocation did not load prior company evidence;
that upstream retry is not counted as an opening-parser failure or as a successful closure.

`.157` connects `max_job_pages` to generic opening pagination and adds bounded SSR card extraction
for explicit location/map-pin evidence plus same-origin job-family UUID details. A real Haystack
run read all four filtered pages and all 64 candidates, with zero missing card locations and no
Greater Tampa Bay match. Its terminal changed from inconclusive `OPENING_DISCOVERY_INCOMPLETE` to
complete-inventory `OPENING_NOT_FOUND`; no opening URL was published, and replay passed 1/1. A broad
`United States` listing cannot satisfy a Tampa target without a verified detail location.

The subsequent clean `.157` 17-record gate produced 11 Exact, 6 Partial, 0 Failed and 17/17 Job
Lists in 595.3 seconds. Full replay reproduced 17/17 and passed the outcome gate. Relative to `.155`,
Solomon Page changed from `NETWORK_TIMEOUT` to Exact, both Haystack records changed from
`OPENING_DISCOVERY_INCOMPLETE` to verified title/location `OPENING_NOT_FOUND`, and all other records
kept their prior outcomes. Six secondary `FETCH_FAILED` reason counts come from blocked LinkedIn
company-page fetches; none became a top-level record error and current official provider inventory
still determined each terminal outcome.

These focused results improve correctness and close the two observed system defects, but they do
not change the formal 100-record score. The `.125` baseline remains `46/100` Exact until a clean
same-version frozen-100 rerun is completed.

## `.158` Staged Routing And First Closure Cluster

The `.157` exhaustive diagnostic rerun completed all 100 stable job IDs at 45 Exact and 62 verified
Job Lists in 4191.8 seconds. Running the same inputs with product staged routing, while keeping the
other budgets and two-company concurrency fixed, reached 51 Exact and 68 Job Lists in 2411.1
seconds. Existing direct or stored provider evidence now preserves the opening-search window;
exhaustive route attribution remains a diagnostic mode rather than the product default.

The first `.158` closure cluster addressed four systemic contracts. Revalidated stored Career
evidence may restore a same-entity hiring relationship only after a current S4 verification.
Historical company evidence merges monotonically by observation time. Generic details can recover
strict page-bound title/location evidence without requiring `hiringOrganization.url`, but foreign
employers, identifier conflicts and null/broad locations for specific targets remain rejected.
Declared POST and browser search submissions must change route, canonical payload or listing
fingerprint. Explicit multi-board actions are complete only when every observed public action was
visited and every resulting board was attempted.

The fixed seven-record live gate produced 7/7 Job Lists, five S7 Exact openings, one verified
`NO_PUBLIC_OPENINGS`, and one remaining `SYSTEM_GAP`; full same-version replay passed 7/7. Both
hackajob postings, Lacoste, Bacardi and Randstad are Exact. Steve Madden's Corporate and Retail ADP
inventories are both complete and empty, so no stale LinkedIn opening is published. EVONA still
has an incomplete public search transport after a server error and remains a system gap rather
than being relabeled as external blocking.

The phase gate passes 2334 tests (3 skipped), 25/25 provider benchmark cases, 6/6 resolver cases,
and architecture validation for 43 native adapters with zero issues. The only sandbox-local test
error was the expected loopback bind denial; the same full command passed in the approved local
environment. The focused replay has no fixture gaps or outcome mismatches.

The closure ledger in `docs/FROZEN_100_CLOSURE_MATRIX.md` is now 56 Exact, 9 Verified Not Found and
35 System Gaps, with zero External Blocked or Input Identity Invalid records promoted without
reproducible evidence. This ledger overlays focused evidence onto the staged run; it is not a new
unified 100-record score. Another full frozen-100 run is deferred until additional high-impact
System Gap clusters close.
